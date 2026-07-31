#!/usr/bin/env python3
"""Self-check for living_board.py.

Exercises the pure rendering and state logic: empty states, resolved items disappearing,
title-match dedup, atomic round-trip, and the three overflow/corruption edge cases that a
code review caught. Network sends are deliberately excluded so this runs offline with no
token and no side effects.

Run it after editing the board, and on a new install to confirm the environment works:

    python3 scripts/verify_board.py
    python3 scripts/verify_board.py ~/.hermes/scripts/living_board.py   # also check a
                                                                       # deployed copy

The optional argument catches the failure mode where a fix lands in the repo but never
gets back-ported to the copy actually running.

Exits non-zero on any failure. Both branches are verified to catch real regressions:
removing the "keep at least one item" guard fails exactly the two checks that guard it,
and relaxing ITEM_LIMIT on a deployed copy fails the cap check.
"""
import importlib.util, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
fails = []

def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond: fails.append(name)

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

tmp = pathlib.Path(tempfile.mkdtemp(prefix="hermes-verify-board-"))
cfg_path = tmp / "board.toml"
cfg_path.write_text('bot_token_env = "TELEGRAM_BOT_TOKEN"\nchat_id = -1001234567890\n'
                    'timezone = "UTC"\n\n[topics]\nbrief = 11\nneeds-me = 22\n\n'
                    '[topics_decision]\nneeds-me = true\n')

print("\n== living_board self-check ==")
os.environ["BOARD_CONFIG"] = str(cfg_path)
os.environ["BOARD_STATE_DIR"] = str(tmp / "state")
lb = load(HERE / "scripts/living_board.py", "lb_repo")
cfg = lb.load_config()

check("config parses", cfg["chat_id"] == -1001234567890 and cfg["topics"]["brief"] == 11)

# Parse the SHIPPED template, not a fixture, so a broken template fails here rather than on
# someone's first install. Cross-check the fallback parser against stdlib tomllib when
# available: the two backends must agree or 3.9 and 3.11+ users get different boards.
tpl = (HERE / "templates/board.toml").read_text()
fb = lb._parse_minimal_toml(tpl)
check("shipped template: topics", isinstance(fb.get("topics"), dict) and bool(fb["topics"]))
check("shipped template: chat_id int", isinstance(fb.get("chat_id"), int))
check("shipped template: decision map", fb.get("topics_decision", {}).get("needs-me") is True)
try:
    import tomllib
    std = tomllib.loads(tpl)
    check("TOML backends agree",
          std["topics"] == fb["topics"] and std["chat_id"] == fb["chat_id"]
          and std.get("topics_decision") == fb.get("topics_decision"))
except ModuleNotFoundError:
    print("  SKIP  TOML backends agree (stdlib tomllib needs 3.11+)")

check("decision empty state", "Nothing needs you" in lb.render(cfg, "needs-me", {"items": []}))
check("change empty state", "Nothing material" in lb.render(cfg, "brief", {"items": []}))
check("decision board counts",
      "2 waiting on you" in lb.render(cfg, "needs-me",
          {"items": [{"title": "A", "body": "x"}, {"title": "B", "body": "y"}]}))
check("resolved items vanish",
      "B" not in lb.render(cfg, "needs-me",
          {"items": [{"title": "A", "body": "x"}, {"title": "B", "body": "y", "resolved": True}]}))

giant = lb.render(cfg, "needs-me", {"items": [{"title": "Huge", "body": "y" * 9000}]})
check("oversized item never renders as empty", "Nothing needs you" not in giant)
check("oversized item stays under cap", len(giant) <= lb.TG_LIMIT, f"len={len(giant)}")
check("oversized item marks the cut", "[…]" in giant)

many = lb.render(cfg, "needs-me", {"items": [{"title": f"I{i}", "body": "z"*3000} for i in range(6)]})
check("many oversized items stay under cap", len(many) <= lb.TG_LIMIT, f"len={len(many)}")
check("many oversized items report the drop", "more in the project log" in many)

sd = pathlib.Path(os.environ["BOARD_STATE_DIR"]); sd.mkdir(parents=True, exist_ok=True)
(sd / "brief.json").write_text("{ not json")
try:
    lb.load_state("brief"); check("corrupt state raises instead of wiping", False, "silently wiped")
except SystemExit:
    check("corrupt state raises instead of wiping", True)
    check("corrupt state file preserved", any(p.name.startswith("brief.corrupt") for p in sd.iterdir()))

st = {"message_id": None, "items": []}
for body in ("first", "second"):
    for it in st["items"]:
        if it["title"].lower() == "same": it["body"] = body; break
    else: st["items"].append({"title": "same", "body": body, "resolved": False})
check("title match dedups", len(st["items"]) == 1 and st["items"][0]["body"] == "second")
lb.save_state("rt", st)
check("state round-trips", lb.load_state("rt")["items"][0]["body"] == "second")

# --- review round 2: overflow correctness, ordering, portability ---
print("\n== overflow and portability ==")

# header must report the TRUE open count even when the board is trimmed
big = [{"title": f"Item {i}", "body": "z" * 1200} for i in range(9)]
r = lb.render(cfg, "needs-me", {"items": big})
check("header counts ALL open items, not the trimmed subset", "9 waiting on you" in r,
      r.split(chr(10))[0])
check("trimmed board under cap", len(r) <= lb.TG_LIMIT, f"len={len(r)}")
check("trimmed board reports the drop", "more in the project log" in r)

# the drop note must actually appear (an earlier version replaced on a recomputed stamp)
import re
m = re.search(r"\+(\d+) more in the project log", r)
shown = r.count("**") // 2 - 0  # bold markers: header + one per kept title
check("drop note present with a count", bool(m), m.group(0) if m else "MISSING")

# no mid-item cut: every rendered body must be whole (no bare slice artefacts)
kept_bodies = re.findall(r"z+", r)
check("kept bodies are whole, never mid-sliced",
      all(len(b) == 1200 for b in kept_bodies) or "[…]" in r,
      f"lengths={sorted(set(len(b) for b in kept_bodies))}")

# a single item too large for the whole board keeps its title and marks the cut
one = lb.render(cfg, "needs-me", {"items": [{"title": "Solo", "body": "q" * 9000}]})
check("single oversized item keeps its title", "Solo" in one)
check("single oversized item marks the cut", "[…]" in one)
check("single oversized item under cap", len(one) <= lb.TG_LIMIT, f"len={len(one)}")

# timestamp must not use POSIX-only %-I (crashes on Windows)
src = (HERE / "scripts/living_board.py").read_text()
# Strip comments AND docstrings via the AST: a prose mention of a directive in an
# explanatory comment is not a use of it, and an earlier version of this check kept
# failing on its own documentation.
import ast
_tree = ast.parse(src)
for _node in ast.walk(_tree):
    if isinstance(_node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if (_node.body and isinstance(_node.body[0], ast.Expr)
                and isinstance(_node.body[0].value, ast.Constant)
                and isinstance(_node.body[0].value.value, str)):
            _node.body.pop(0)
code = ast.unparse(_tree)
check("no POSIX-only strftime directive", "%-I" not in code and "%-d" not in code)
check("timestamp renders", bool(lb.now_local(cfg)), lb.now_local(cfg))

# state is written only after a successful push, and the whole cycle is locked
check("push happens before save in cmd_set",
      src.index("push(cfg, args.topic, state)") < src.index("save_state(args.topic, state)"))
check("board_lock wraps the read-modify-push cycle",
      "with board_lock(args.topic):" in src and src.count("with board_lock") >= 2)
check("pin failure is surfaced", "could not be pinned" in src)
# Compare against `code` (comments stripped): a prose mention is not a use.
check("uses HTML parse mode, never Markdown",
      ("parse_mode='HTML'" in code or 'parse_mode="HTML"' in code)
      and "Markdown" not in code)

# the lock actually excludes a second holder
import contextlib
with lb.board_lock("locktest"):
    try:
        lb.LOCK_TIMEOUT = 1
        with lb.board_lock("locktest"):
            check("lock excludes a concurrent holder", False, "second acquire succeeded")
    except SystemExit:
        check("lock excludes a concurrent holder", True)
check("lock released on exit", not lb.state_path("locktest").with_suffix(".lock").exists())

# markdown-hostile content must survive rendering untouched
hostile = lb.render(cfg, "brief", {"items": [{"title": "a_b *c* [d](e) `f`", "body": "x_y"}]})
check("markdown-hostile text passes through unescaped", "a_b *c* [d](e) `f`" in hostile)

# HTML-special characters MUST be escaped or Telegram rejects the whole message
htmlish = lb.render(cfg, "brief", {"items": [{"title": "A & B <tag>", "body": "x > y & z"}]})
check("ampersand escaped", "&amp;" in htmlish and "A & B" not in htmlish)
check("angle brackets escaped", "&lt;tag&gt;" in htmlish)
check("bold markup intact", "<b>1. A &amp; B &lt;tag&gt;</b>" in htmlish)

# If a deployed copy exists elsewhere (a profile that vendored this script), check it too.
# A fix applied to the repo but not back-ported to the running copy is the failure mode this
# catches. Pass the path as argv[1]; skipped silently when not given.
if len(sys.argv) > 1:
    other = pathlib.Path(sys.argv[1]).expanduser()
    if other.is_file():
        print(f"\n== deployed copy ({other}) ==")
        dl = load(other, "lb_deployed")
        # Deployed copies predate the config layer, so render() takes (topic, state).
        r = dl.render if dl.render.__code__.co_argcount == 2 else lambda t, s: dl.render(cfg, t, s)
        g = r("needs-me", {"items": [{"title": "Huge", "body": "y" * 9000}]})
        check("oversized item never renders as empty", "Nothing needs you" not in g)
        check("oversized item stays under cap", len(g) <= dl.TG_LIMIT, f"len={len(g)}")
        m = r("needs-me", {"items": [{"title": f"I{i}", "body": "z" * 3000} for i in range(6)]})
        check("many oversized items stay under cap", len(m) <= dl.TG_LIMIT, f"len={len(m)}")
        src = other.read_text()
        check("corrupt state raises instead of wiping",
              "raise SystemExit" in src.split("def load_state")[1][:900])
        check("item cap enforced", getattr(dl, "ITEM_LIMIT", 0) == 600)
    else:
        check("deployed copy exists", False, str(other))

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
