#!/usr/bin/env python3
"""gworkspace.py — stdlib-only Google Drive/Docs/Sheets/Slides helper.

Auth: reuses the OAuth refresh token already stored by `gog` (steipete/gogcli),
so no extra Python packages and no second OAuth dance. Works regardless of gog
version, because it talks to the Google REST APIs directly.

Token source resolution (first hit wins):
  1. --refresh-token-file / --client-secret-file flags
  2. env GOOGLE_REFRESH_TOKEN + GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET
  3. gog: exports a temporary refresh-token JSON with `gog auth tokens export` + gog credentials.json

Commands:
  token                                  print a fresh access token
  upload   <path> --as doc|sheet|slide|raw [--name N] [--parent FOLDER]
  export   <fileId> --mime MIME [--out PATH]
  mkdir    <name> [--parent FOLDER]
  share    <fileId> --email E --role reader|writer|commenter
  meta     <fileId>

Exit code is non-zero on any API error; errors print JSON to stderr.
"""
import argparse, json, os, sys, urllib.request, urllib.parse, uuid, subprocess, mimetypes, shutil

GOOGLE_TYPES = {
    "doc": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "slide": "application/vnd.google-apps.presentation",
}
# Best source MIME per source extension for clean conversion.
SRC_MIME = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html", ".htm": "text/html", ".txt": "text/plain",
}


def _gog_bin():
    """Resolve the gog binary via PATH; never trust a bare relative name.

    Returns None when gog is not installed. gog is only ever a credential
    *fallback* here (explicit files and env vars take precedence), so a missing
    gog must not abort — it should let credential resolution fall through to a
    clean missing_credentials error.
    """
    return shutil.which("gog")


def _gog_account():
    return os.environ.get("GOG_ACCOUNT") or _default_gog_account()


def _default_gog_account():
    gog = _gog_bin()
    if not gog:
        return None
    try:
        out = subprocess.run([gog, "auth", "list", "--plain"],
                             capture_output=True, text=True, timeout=20)
        line = out.stdout.strip().splitlines()[0]
        return line.split("\t")[0]
    except Exception:
        return None


def _read_secret_json(path):
    """Read a JSON secret file, refusing world/group-readable or other-owned files."""
    real = os.path.realpath(os.path.expanduser(path))
    st = os.stat(real)
    if st.st_uid != os.getuid():
        sys.exit(json.dumps({"error": "insecure_credential_file",
                             "detail": f"{path} is not owned by the current user"}))
    if st.st_mode & 0o077:
        sys.exit(json.dumps({"error": "insecure_credential_file",
                             "detail": f"{path} is group/world accessible; chmod 600 it"}))
    if st.st_size > 1_000_000:
        sys.exit(json.dumps({"error": "credential_file_too_large", "detail": path}))
    with open(real) as fh:
        return json.load(fh)


def _load_creds(args):
    # 1. explicit files
    rt = cid = csec = None
    if args.refresh_token_file:
        d = _read_secret_json(args.refresh_token_file)
        rt = d.get("refresh_token", d.get("refreshToken"))
    if args.client_secret_file:
        d = _read_secret_json(args.client_secret_file)
        d = d.get("installed", d.get("web", d)); cid = d["client_id"]; csec = d["client_secret"]
    # 2. env
    rt = rt or os.environ.get("GOOGLE_REFRESH_TOKEN")
    cid = cid or os.environ.get("GOOGLE_CLIENT_ID")
    csec = csec or os.environ.get("GOOGLE_CLIENT_SECRET")
    # 3. gog
    if not rt:
        gog = _gog_bin()
        acct = _gog_account() if gog else None
        if gog and acct:
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            try:
                os.unlink(tmp_path)  # gog v0.9 refuses to overwrite an existing temp path in some modes
            except OSError:
                pass
            try:
                r = subprocess.run([gog, "auth", "tokens", "export", acct, "--output", tmp_path, "--force"],
                                   capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and os.path.exists(tmp_path):
                    with open(tmp_path) as fh:
                        rt = json.load(fh).get("refresh_token")
            except Exception:
                pass
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    if not (cid and csec):
        # gog credentials.json default locations (macOS + linux)
        for p in [
            os.path.expanduser("~/Library/Application Support/gogcli/credentials.json"),
            os.path.expanduser("~/.config/gogcli/credentials.json"),
        ]:
            if not os.path.exists(p):
                continue
            try:
                with open(p) as fh:
                    d = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            d = d.get("installed", d.get("web", d))
            cid = cid or d.get("client_id")
            csec = csec or d.get("client_secret")
            if cid and csec:
                break
    if not (rt and cid and csec):
        sys.exit(json.dumps({"error": "missing_credentials",
                             "have": {"refresh_token": bool(rt), "client_id": bool(cid), "client_secret": bool(csec)}}))
    return rt, cid, csec


def access_token(args):
    rt, cid, csec = _load_creds(args)
    data = urllib.parse.urlencode({"client_id": cid, "client_secret": csec,
                                   "refresh_token": rt, "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(json.dumps({"error": "token_refresh_failed", "code": e.code, "body": e.read().decode()[:300]}))


def _api(token, method, url, body=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as r:
            return r.read() if raw else json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(json.dumps({"error": "api_error", "code": e.code, "url": url, "body": e.read().decode()[:400]}))


def cmd_token(args):
    print(access_token(args))


def cmd_upload(args):
    token = access_token(args)
    ext = os.path.splitext(args.path)[1].lower()
    src_mime = SRC_MIME.get(ext) or mimetypes.guess_type(args.path)[0] or "application/octet-stream"
    target = None if args.as_ == "raw" else GOOGLE_TYPES[args.as_]
    name = args.name or os.path.splitext(os.path.basename(args.path))[0]
    meta = {"name": name}
    if args.parent:
        meta["parents"] = [args.parent]
    if target:
        meta["mimeType"] = target
    boundary = "----b" + uuid.uuid4().hex
    with open(args.path, "rb") as fh:
        file_body = fh.read()
    payload = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(meta).encode(),
        f"\r\n--{boundary}\r\nContent-Type: {src_mime}\r\n\r\n".encode(),
        file_body,
        f"\r\n--{boundary}--\r\n".encode()])
    url = ("https://www.googleapis.com/upload/drive/v3/files"
           "?uploadType=multipart&fields=id,name,mimeType,webViewLink&supportsAllDrives=true")
    res = _api(token, "POST", url, body=payload,
               headers={"Content-Type": f"multipart/related; boundary={boundary}"})
    print(json.dumps(res, indent=2))


def cmd_export(args):
    token = access_token(args)
    url = (f"https://www.googleapis.com/drive/v3/files/{args.file_id}/export"
           f"?mimeType={urllib.parse.quote(args.mime)}&supportsAllDrives=true")
    data = _api(token, "GET", url, raw=True)
    if args.out:
        with open(args.out, "wb") as fh:
            fh.write(data)
        print(json.dumps({"status": "exported", "fileId": args.file_id, "path": args.out, "bytes": len(data)}))
    else:
        sys.stdout.buffer.write(data)


def cmd_mkdir(args):
    token = access_token(args)
    meta = {"name": args.name, "mimeType": "application/vnd.google-apps.folder"}
    if args.parent:
        meta["parents"] = [args.parent]
    res = _api(token, "POST", "https://www.googleapis.com/drive/v3/files?fields=id,name,webViewLink&supportsAllDrives=true",
               body=json.dumps(meta).encode(), headers={"Content-Type": "application/json"})
    print(json.dumps(res, indent=2))


def cmd_share(args):
    token = access_token(args)
    body = {"role": args.role, "type": "user", "emailAddress": args.email}
    url = (f"https://www.googleapis.com/drive/v3/files/{args.file_id}/permissions"
           f"?fields=id,role,type&sendNotificationEmail={'true' if args.notify else 'false'}&supportsAllDrives=true")
    res = _api(token, "POST", url, body=json.dumps(body).encode(),
               headers={"Content-Type": "application/json"})
    print(json.dumps(res, indent=2))


def cmd_meta(args):
    token = access_token(args)
    url = (f"https://www.googleapis.com/drive/v3/files/{args.file_id}"
           "?fields=id,name,mimeType,webViewLink,modifiedTime,size,parents&supportsAllDrives=true")
    print(json.dumps(_api(token, "GET", url), indent=2))


def main():
    p = argparse.ArgumentParser(description="stdlib-only Google Workspace helper (reuses gog auth)")
    p.add_argument("--refresh-token-file"); p.add_argument("--client-secret-file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("token")
    up = sub.add_parser("upload"); up.add_argument("path")
    up.add_argument("--as", dest="as_", required=True, choices=["doc", "sheet", "slide", "raw"])
    up.add_argument("--name"); up.add_argument("--parent")
    ex = sub.add_parser("export"); ex.add_argument("file_id"); ex.add_argument("--mime", required=True); ex.add_argument("--out")
    mk = sub.add_parser("mkdir"); mk.add_argument("name"); mk.add_argument("--parent")
    sh = sub.add_parser("share"); sh.add_argument("file_id"); sh.add_argument("--email", required=True)
    sh.add_argument("--role", default="reader", choices=["reader", "writer", "commenter"]); sh.add_argument("--notify", action="store_true")
    mt = sub.add_parser("meta"); mt.add_argument("file_id")
    args = p.parse_args()
    {"token": cmd_token, "upload": cmd_upload, "export": cmd_export,
     "mkdir": cmd_mkdir, "share": cmd_share, "meta": cmd_meta}[args.cmd](args)


if __name__ == "__main__":
    main()
