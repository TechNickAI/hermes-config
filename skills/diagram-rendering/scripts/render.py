#!/usr/bin/env python3
"""
diagram-rendering: render text-as-diagram (D2 / Mermaid / Graphviz) and
data charts (QuickChart / Chart.js) into a PNG suitable for inline messaging.

Design goals (why this exists):
  - Telegram (and most chat platforms) render PNG/JPG inline; they do NOT render
    .d2/.mmd/.dot/.svg source. So every path must terminate in a raster PNG or a
    directly-fetchable image URL.
  - Diagram *look* is driven by the LANGUAGE, not the host. D2 is the cleanest;
    Graphviz has the most robust auto-layout; Mermaid is quick but plainer.
  - No local diagram engines are assumed (mmdc/d2/dot may be absent). We render
    via hosted Kroki (SVG/PNG) and rasterize SVG locally with headless Chromium.

HARD-WON GOTCHAS baked in (do not "simplify" these away):
  1. Kroki renders D2 to SVG ONLY (PNG 400s). We fetch SVG then rasterize.
  2. Some Mermaid syntaxes 400 on Kroki's PNG endpoint but succeed on SVG.
  3. Snap Chromium is AppArmor-confined and CANNOT write outside $HOME.
     Output paths are forced under $HOME (symlink-resolved); /tmp silently fails.
  4. A failed render (bad syntax / host down) can yield a blank or non-PNG body.
     We verify PNG magic + non-blank pixels and fail closed.

SECURITY:
  - Kroki/QuickChart responses are UNTRUSTED. SVG is rasterized with JavaScript
    DISABLED (kills <script>), and external resource references (href/xlink:href/src
    to http(s)/file, and CSS url()) are neutralized before rendering so declarative
    <image>/<use> loads cannot phone home / SSRF during rasterization.
  - KROKI_BASE/QUICKCHART_BASE are treated as trusted operator config: http(s) only.
    Never derive them from an untrusted prompt.
  - Diagram source AND chart data are sent to the render host. Not for secrets;
    self-host via *_BASE for sensitive content.

Usage:
  render.py diagram --lang d2       --in flow.d2  --out ~/out.png
  render.py diagram --lang mermaid  --in flow.mmd --out ~/out.png
  render.py diagram --lang graphviz --in flow.dot --out ~/out.png
  render.py chart   --config chart.json --out ~/out.png     # local raster (POST)
  render.py chart   --config chart.json --short-url         # inline URL
  render.py svg     --in diagram.svg --out ~/out.png        # rasterize trusted SVG

Env knobs (all optional):
  KROKI_BASE      default https://kroki.io    (point at a self-hosted Kroki)
  QUICKCHART_BASE default https://quickchart.io
  CHROMIUM_BIN    default: first of chromium-browser/chromium/google-chrome on PATH
  DIAGRAM_SCALE   device scale factor for raster (default 2, clamped 1..4)
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NoReturn, Optional

KROKI_BASE = os.environ.get("KROKI_BASE", "https://kroki.io").rstrip("/")
QUICKCHART_BASE = os.environ.get("QUICKCHART_BASE", "https://quickchart.io").rstrip("/")
HOME = os.path.realpath(os.path.expanduser("~"))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Kroki accepts these languages; D2 is SVG-only on the hosted service.
SVG_ONLY_LANGS = {"d2"}
KROKI_LANGS = {"d2", "mermaid", "graphviz", "plantuml", "blockdiag", "erd"}

# Kroki and some render hosts 403 the default "Python-urllib/x.y" UA. Send a real one.
_UA = "Mozilla/5.0 (compatible; hermes-diagram-rendering/1.0)"

# Guardrails against resource exhaustion / validator crashes.
MAX_INPUT_BYTES = 512 * 1024        # source diagram / chart config
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # rendered image from host
MAX_DIM = 8000                      # width/height ceiling (px, pre-scale)


def _err(msg: str, code: int = 1) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _scale() -> int:
    try:
        s = int(os.environ.get("DIAGRAM_SCALE", "2"))
    except ValueError:
        s = 2
    return max(1, min(4, s))


def _clamp_dim(v: int, name: str) -> int:
    if v <= 0 or v > MAX_DIM:
        _err(f"{name}={v} out of range (1..{MAX_DIM}).")
    return v


def _find_chromium() -> str:
    explicit = os.environ.get("CHROMIUM_BIN")
    if explicit:
        if os.path.isfile(explicit) or shutil.which(explicit):
            return explicit
        _err(f"CHROMIUM_BIN={explicit!r} not found.")
    for cand in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        p = shutil.which(cand)
        if p:
            return p
    _err(
        "no chromium binary found (need chromium-browser/chromium/google-chrome). "
        "Install one or set CHROMIUM_BIN. (Not needed for `chart --short-url`.)"
    )


def _enforce_home(path: str) -> str:
    """Snap Chromium can only write under $HOME. Resolve symlinks and confine.

    Lexical startswith() is bypassable via a symlink under $HOME pointing outside
    it, so we resolve the *parent* (which must exist) with realpath and compare via
    commonpath, and refuse a final component that is itself a symlink.
    """
    expanded = os.path.expanduser(path)
    parent = os.path.dirname(os.path.abspath(expanded)) or HOME
    os.makedirs(parent, exist_ok=True)
    real_parent = os.path.realpath(parent)
    if os.path.commonpath([real_parent, HOME]) != HOME:
        _err(
            f"output dir {real_parent!r} is outside $HOME ({HOME}). "
            "Snap Chromium is AppArmor-confined and cannot write there. "
            "Choose a path under your home directory."
        )
    final = os.path.join(real_parent, os.path.basename(expanded))
    if os.path.islink(final):
        _err(f"refusing to write through symlink {final!r}.")
    return final


def _check_base(url: str, name: str):
    if not (url.startswith("http://") or url.startswith("https://")):
        _err(f"{name} must be an http(s) URL (got {url!r}). Treat as trusted operator config.")


def _post(url: str, data: bytes, ctype: Optional[str] = None, timeout: int = 30,
          retries: int = 2) -> bytes:
    """POST with a small retry: hosted renderers (esp. Kroki mermaid) intermittently
    500/400 with 'Failed to launch the browser process' under load. Retry transient
    5xx and that specific 400 a couple times before giving up."""
    headers = {"User-Agent": _UA}
    if ctype:
        headers["Content-Type"] = ctype
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            transient = e.code >= 500 or (
                e.code == 400 and b"Failed to launch the browser process" in body
            )
            if not transient or attempt == retries:
                _err(f"render host {url} failed (HTTP {e.code}): {body[:200]!r}")
            time.sleep(1.5 * (attempt + 1))
        except urllib.error.URLError as e:
            if attempt == retries:
                _err(f"could not reach render host {url}: {e.reason}")
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")  # keeps type-checkers happy


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_RESPONSE_BYTES + 1)


def _too_big(b: bytes) -> bool:
    return len(b) > MAX_RESPONSE_BYTES


def _read_input(path: str) -> bytes:
    if not os.path.isfile(path):
        _err(f"input not found: {path}")
    if os.path.getsize(path) > MAX_INPUT_BYTES:
        _err(f"input {path} exceeds {MAX_INPUT_BYTES} bytes.")
    data = open(path, "rb").read()
    if not data.strip():
        _err(f"input {path} is empty.")
    return data


def _is_png(b: bytes) -> bool:
    return b[:8] == PNG_MAGIC


def _verify_png_not_blank(png_path: str):
    """Fail closed on a blank/invalid image (CDN blip / bad syntax)."""
    with open(png_path, "rb") as f:
        head = f.read(8)
    if head != PNG_MAGIC:
        os.unlink(png_path)
        _err(f"{png_path} is not a valid PNG (render host returned an error body?).")
    try:
        from PIL import Image
    except ImportError:
        # Heuristic only. A real diagram PNG is > ~2KB; but a large blank canvas can
        # exceed that, so this is NOT authoritative — SKILL.md tells the agent to eyeball.
        if os.path.getsize(png_path) < 2048:
            os.unlink(png_path)
            _err(f"{png_path} is suspiciously small (<2KB) and Pillow is absent — likely blank.")
        return
    im = Image.open(png_path)
    im.verify()  # raises on corrupt PNG
    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    if w < 2 or h < 2:
        os.unlink(png_path)
        _err(f"{png_path} is too small to be a real diagram ({w}x{h}).")
    colors = im.getcolors(maxcolors=100000)
    distinct = len(colors) if colors else 100001
    px = im.load()
    if px is None:
        return
    bg = px[0, 0]
    sampled = 0
    nonbg = 0
    # Sample on a fine grid (step 3) so thin strokes / sparse diagrams can't fall
    # entirely between sample points and be misread as blank.
    step = 3
    for y in range(0, h, step):
        for x in range(0, w, step):
            sampled += 1
            if px[x, y] != bg:
                nonbg += 1
    # A blank canvas is one solid color (all samples == bg). A legitimate minimal
    # diagram can be small/sparse, so use a PROPORTIONAL floor (fraction of sampled
    # points that are non-background) rather than an absolute count that false-positives
    # on tiny valid renders. Require >=2 colors AND at least ~0.05% non-bg samples
    # (with a floor of just 1 sample so a genuinely tiny-but-inked image passes).
    min_nonbg = max(1, int(sampled * 0.0005))
    if distinct < 2 or nonbg < min_nonbg:
        os.unlink(png_path)
        _err(
            f"{png_path} looks blank (distinct_colors={distinct}, "
            f"nonbg={nonbg}/{sampled}, floor={min_nonbg}). "
            "Check diagram syntax / network to the render host."
        )


def _write_verified_png(png_bytes: bytes, out_png: str):
    """Write bytes to a fresh $HOME temp (not symlink-followable), verify non-blank,
    then atomically replace the destination ONLY on success. Mirrors the diagram
    path so a bad render never deletes a pre-existing good file at --out, and the
    intermediate write can't be redirected through a pre-planted symlink."""
    out_png = _enforce_home(out_png)
    fd, tmp = tempfile.mkstemp(suffix=".png", dir=HOME, prefix="diagram_out_")
    replaced = False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png_bytes)
        _verify_png_not_blank(tmp)  # deletes tmp (not out_png) on failure
        os.replace(tmp, out_png)
        replaced = True
    finally:
        # Only clean up the temp if it was NOT successfully moved into place.
        # (After os.replace, tmp no longer exists; if replace raised, tmp still holds
        #  the only good render — surface the error rather than deleting it here.)
        if not replaced and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _chromium_screenshot(src_url: str, out_png: str, w: int, h: int) -> str:
    """Render src_url to out_png. Fails closed; never leaves a stale file as 'fresh'."""
    out_png = _enforce_home(out_png)
    w = _clamp_dim(w, "width")
    h = _clamp_dim(h, "height")
    chromium = _find_chromium()
    # Render to a fresh temp file so a failed run can't pass off a stale image.
    # NOTE: the OUTPUT file must NOT be dot-prefixed — snap Chromium's AppArmor
    # profile silently refuses to WRITE hidden files under $HOME (reading a hidden
    # source is fine). Use a visible prefix for the screenshot target.
    fd, tmp_png = tempfile.mkstemp(suffix=".png", dir=HOME, prefix="diagram_out_")
    os.close(fd)
    replaced = False
    try:
        args = [
            chromium, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
            # Untrusted-response hardening: disabling JS kills the script-execution
            # vector for malicious SVG. (A custom --user-data-dir is intentionally NOT
            # used: snap Chromium is already per-invocation isolated and rejects
            # dot-prefixed profile dirs under $HOME with a SingletonLock error.)
            "--disable-javascript", "--disable-remote-fonts", "--disable-extensions",
            "--no-first-run", "--no-default-browser-check",
            f"--force-device-scale-factor={_scale()}", f"--window-size={w},{h}",
            "--default-background-color=00000000",
            f"--screenshot={tmp_png}", src_url,
        ]
        try:
            subprocess.run(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=90, check=True,
            )
        except subprocess.TimeoutExpired:
            _err("chromium timed out rendering the diagram (90s).")
        except subprocess.CalledProcessError as e:
            tail = (e.stderr or b"")[-400:].decode("utf-8", "replace")
            _err(f"chromium exited {e.returncode}. stderr tail: {tail}")
        if not os.path.exists(tmp_png) or os.path.getsize(tmp_png) == 0:
            _err(f"chromium produced no output for {src_url}.")
        _verify_png_not_blank(tmp_png)
        os.replace(tmp_png, out_png)  # atomic; only on verified success
        replaced = True
        return out_png
    finally:
        # Only remove tmp if it wasn't successfully moved into place; after a failed
        # os.replace it holds the only good render, so surface the error instead.
        if not replaced and os.path.exists(tmp_png):
            try:
                os.unlink(tmp_png)
            except OSError:
                pass


def _neutralize_external_refs(svg: bytes) -> bytes:
    """Strip external resource references from untrusted SVG before rasterizing.

    Disabling JS stops <script>, but declarative SVG can still fetch external URLs
    during raster via <image href="https://...">, <use href>, or CSS url(). Chromium
    would issue those outbound requests (SSRF / phone-home). We blank any http(s)/file
    reference in href/xlink:href/url() so nothing leaves the box during rasterization.
    Local diagram content (kroki output) never legitimately needs an external fetch.
    Allowlist approach: keep ONLY local in-document fragment refs (href="#id"); blank
    everything else (http(s)/file/absolute filesystem paths, relative paths,
    protocol-relative //host). This is stricter and closes the absolute-path vector.
    """
    def _href_repl(m: "re.Match[bytes]") -> bytes:
        attr, quote, val = m.group(1), m.group(2), m.group(3)
        if val.startswith(b"#"):          # local fragment: safe, keep it
            return m.group(0)
        return attr + b"=" + quote + b"#neutralized" + quote
    # href / xlink:href / src = "..."  → neutralize unless value is a local #fragment.
    svg = re.sub(rb'((?:xlink:)?href|src)\s*=\s*(["\'])\s*([^"\']*)\2',
                 _href_repl, svg, flags=re.IGNORECASE)

    def _href_unquoted_repl(m: "re.Match[bytes]") -> bytes:
        attr, val = m.group(1), m.group(2)
        if val.startswith(b"#"):
            return m.group(0)
        return attr + b'="#neutralized"'
    # Unquoted form (valid in HTML/SVG parsers): absolute-path and HTTP refs.
    svg = re.sub(rb'((?:xlink:)?href|src)\s*=\s*([^"\'\s>]+)',
                 _href_unquoted_repl, svg, flags=re.IGNORECASE)
    # CSS url(...) → keep only local url(#id); blank any external/path reference.
    def _url_repl(m: "re.Match[bytes]") -> bytes:
        inner = m.group(1).strip(b' "\'')
        if inner.startswith(b"#"):
            return m.group(0)
        return b"url(#neutralized)"
    svg = re.sub(rb'url\(\s*([^)]*)\)', _url_repl, svg, flags=re.IGNORECASE)
    return svg


def _svg_to_png(svg_bytes: bytes, out_png: str, w: int, h: int) -> str:
    """Wrap SVG in a padded HTML doc under $HOME, then screenshot it (JS disabled,
    external refs neutralized)."""
    if b"<svg" not in svg_bytes[:2000].lower():
        _err("expected an <svg> root but got something else (render host error page?).")
    svg_bytes = _neutralize_external_refs(svg_bytes)
    wrap = (
        b"<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        b"html,body{margin:0;background:#020617;padding:24px;display:inline-block}"
        b"</style></head><body>" + svg_bytes + b"</body></html>"
    )
    # Source HTML must ALSO be under $HOME for snap chromium to read it.
    fd, htmlpath = tempfile.mkstemp(suffix=".html", dir=HOME, prefix=".diagram_src_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wrap)
        # Use as_uri() so paths with spaces / reserved chars produce a valid file URL.
        return _chromium_screenshot(pathlib.Path(htmlpath).as_uri(), out_png, w, h)
    finally:
        try:
            os.unlink(htmlpath)
        except OSError:
            pass


def cmd_diagram(a):
    _check_base(KROKI_BASE, "KROKI_BASE")
    lang = a.lang.lower()
    if lang not in KROKI_LANGS:
        _err(f"unsupported --lang {lang!r}. Supported: {sorted(KROKI_LANGS)}")
    src = _read_input(a.infile)
    out = _enforce_home(a.out)
    # Consistent 2x rasterization + honored --width/--height for ALL languages:
    # always fetch SVG from Kroki and rasterize locally. (Direct-PNG shortcut removed
    # so the documented scale/dims claims actually hold everywhere.) _post handles
    # transient-error retry and fails closed via _err on give-up.
    svg = _post(f"{KROKI_BASE}/{lang}/svg", src)
    if _too_big(svg):
        _err("render response exceeded size limit.")
    if not svg.lstrip().startswith(b"<"):
        _err(f"kroki {lang} returned non-SVG: {svg[:200]!r}")
    _svg_to_png(svg, out, a.width, a.height)
    print(out)


def cmd_chart(a):
    _check_base(QUICKCHART_BASE, "QUICKCHART_BASE")
    if not os.path.isfile(a.config):
        _err(f"chart config not found: {a.config}")
    if os.path.getsize(a.config) > MAX_INPUT_BYTES:
        _err(f"chart config exceeds {MAX_INPUT_BYTES} bytes.")
    try:
        cfg = json.load(open(a.config))
    except (json.JSONDecodeError, OSError) as e:
        _err(f"could not read chart config {a.config}: {e}")
    payload = {
        "backgroundColor": a.bg, "width": _clamp_dim(a.width, "width"),
        "height": _clamp_dim(a.height, "height"), "format": "png", "chart": cfg,
    }
    if a.short_url:
        # POST once, get a tidy inline-able render URL.
        try:
            resp = json.loads(_post(
                f"{QUICKCHART_BASE}/chart/create",
                json.dumps(payload).encode(), ctype="application/json",
            ))
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            _err(f"quickchart short-url request failed: {e}")
        if not resp.get("success"):
            _err(f"quickchart short-url failed: {resp}")
        url = resp.get("url")
        if not url:
            _err(f"quickchart returned success but no url: {resp}")
        print(url)
        return
    # Local raster path: POST the config (no GET URL-length limit) and save the PNG.
    out = _enforce_home(a.out)
    try:
        png = _post(f"{QUICKCHART_BASE}/chart", json.dumps(payload).encode(),
                    ctype="application/json")
    except urllib.error.URLError as e:
        _err(f"quickchart render failed: {e}")
    if _too_big(png):
        _err("chart response exceeded size limit.")
    if not _is_png(png):
        _err(f"quickchart did not return a PNG: {png[:200]!r}")
    _write_verified_png(png, out)
    print(out)


def cmd_svg(a):
    # Trusted-SVG path: still rendered with JS disabled. Only pass SVG you trust.
    svg = _read_input(a.infile)
    print(_svg_to_png(svg, a.out, a.width, a.height))


def main():
    p = argparse.ArgumentParser(description="Render diagrams/charts to inline-ready PNG.")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diagram", help="Render D2/Mermaid/Graphviz text to PNG.")
    d.add_argument("--lang", required=True)
    d.add_argument("--in", dest="infile", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--width", type=int, default=900)
    d.add_argument("--height", type=int, default=700)
    d.set_defaults(func=cmd_diagram)

    c = sub.add_parser("chart", help="Render a Chart.js config via QuickChart.")
    c.add_argument("--config", required=True)
    c.add_argument("--out", default=os.path.join(HOME, "chart.png"))
    c.add_argument("--short-url", action="store_true", help="Print inline URL instead of PNG.")
    c.add_argument("--bg", default="#020617")
    c.add_argument("--width", type=int, default=700)
    c.add_argument("--height", type=int, default=400)
    c.set_defaults(func=cmd_chart)

    s = sub.add_parser("svg", help="Rasterize a TRUSTED SVG file to PNG.")
    s.add_argument("--in", dest="infile", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--width", type=int, default=900)
    s.add_argument("--height", type=int, default=700)
    s.set_defaults(func=cmd_svg)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
