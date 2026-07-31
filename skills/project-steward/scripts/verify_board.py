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
import threading, time
import importlib.util, json, os, pathlib, shutil, sys, tempfile

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
check("oversized item marks the cut", "…" in giant)

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

# --- Telegram Markdown safety -------------------------------------------------------
# parse_mode="Markdown" means one unmatched control character in ONE item's title makes
# Telegram reject the ENTIRE message ("can't parse entities"): a stray underscore in a
# branch name silently blanks the whole board. Content must be escaped; the template's
# own **bold** / _italic_ markup must not be.
risky = lb.render(cfg, "needs-me", {"items": [
    {"title": "fix snake_case in main_module", "body": "see file_a.py and *not* a list [1]"},
]})
check("underscores in content are escaped", "snake\\_case" in risky)
check("asterisks in content are escaped", "\\*not\\*" in risky)
check("brackets in content are escaped", "\\[1\\]" in risky)
# Telegram LEGACY Markdown bold is a SINGLE asterisk. `**text**` is not a delimiter: it
# renders as literal text with zero bold entities, so the board silently loses all
# formatting. Verified against the live API before changing this.
check("template bold uses legacy single-asterisk", risky.startswith("🚦 *")
      and not risky.startswith("🚦 **"))
check("template italic stamp survives escaping", "_as of " in risky)
# Backslashes must be escaped FIRST or the escape characters themselves get mangled.
back = lb.render(cfg, "needs-me", {"items": [{"title": r"path\to", "body": ""}]})
check("backslashes escaped before other characters", r"path\\to" in back)

# Escaping grows the text, so the overflow estimate must measure the escaped length or
# it keeps too many rows and the message blows the cap.
underscored = lb.render(cfg, "needs-me", {"items": [
    {"title": f"i_{i}", "body": "_" * 2000} for i in range(6)
]})
check("escaped content still respects the cap", len(underscored) <= lb.TG_LIMIT,
      f"len={len(underscored)}")

# --- Pin failure is surfaced, not swallowed -----------------------------------------
# A board that posts but fails to pin still works; it just scrolls away. Reporting
# success silently is what makes that failure invisible.
src = (HERE / "scripts" / "living_board.py").read_text()
pin_body = src.split("def try_pin(")[1].split("\ndef ")[0]
push_body = src.split("def push(")[1].split("\ndef ")[0]
check("pin result is inspected", 'pin.get("ok")' in pin_body)
check("pin failure warns the operator", "NOT pinned" in pin_body)
check("pin outcome is persisted", 'state["pinned"]' in pin_body and "save_state" in pin_body)
# On the CREATE path the id must be persisted before pinning is attempted, so a pin
# failure can't orphan the board and cause a duplicate post next run. (Measured on the
# create branch only; the edit branch legitimately calls try_pin before any save.)
create_path = push_body.split("# Board was deleted")[1]
check("message_id saved before pinning",
      create_path.index("save_state(topic, state)") < create_path.index("try_pin"))
# Without a retry on the edit path, a board that failed to pin once stays unpinned
# forever: later runs edit the existing message and never revisit pinning.
check("unpinned board retries on later runs",
      'if not state.get("pinned")' in push_body and "try_pin" in push_body.split("return")[0])

# --- Adversarial render fuzz --------------------------------------------------------
# Escaping interacts with the overflow paths, so reason about it empirically rather
# than by inspection. Three invariants must hold for ANY content: within the cap, no
# dangling escape (which makes Telegram reject the whole message), and never claiming
# the board is empty while items are waiting.
import random  # noqa: E402

random.seed(7)
alphabet = "_*[`\\ abcXY"
over = dangle = false_empty = 0
for _ in range(3000):
    fuzz_items = [
        {"title": "".join(random.choice(alphabet) for _ in range(random.randint(1, 60))),
         "body": "".join(random.choice(alphabet) for _ in range(random.randint(0, 4000)))}
        for _ in range(random.randint(1, 7))
    ]
    o = lb.render(cfg, "needs-me", {"items": fuzz_items})
    if len(o) > lb.TG_LIMIT:
        over += 1
    t = o.rstrip()
    if (len(t) - len(t.rstrip("\\"))) % 2 == 1:
        dangle += 1
    if "Nothing needs you" in o:
        false_empty += 1
check("fuzz: never exceeds the cap", over == 0, f"{over} of 3000")
check("fuzz: never leaves a dangling escape", dangle == 0, f"{dangle} of 3000")
check("fuzz: never falsely reports an empty board", false_empty == 0, f"{false_empty} of 3000")


# --- second review round: overflow correctness, concurrency, portability ---
print("\n== overflow, ordering, portability ==")

import ast as _ast
_src = (HERE / "scripts/living_board.py").read_text()
# Strip comments AND docstrings via the AST: a prose mention of a directive in an
# explanatory comment is not a use of it.
_tree = _ast.parse(_src)
for _n in _ast.walk(_tree):
    if isinstance(_n, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
        if (_n.body and isinstance(_n.body[0], _ast.Expr)
                and isinstance(_n.body[0].value, _ast.Constant)
                and isinstance(_n.body[0].value.value, str)):
            _n.body.pop(0)
_code = _ast.unparse(_tree)

# the header must report the TRUE open count, not the trimmed subset
big = [{"title": f"Item {i}", "body": "z" * 1200} for i in range(9)]
r9 = lb.render(cfg, "needs-me", {"items": big})
check("header counts ALL open items, not the trimmed subset", "9 waiting on you" in r9,
      r9.split(chr(10))[0])
check("trimmed board under cap", len(r9) <= lb.TG_LIMIT, f"len={len(r9)}")
check("drop note survives (not lost to a recomputed stamp)", "more in the project log" in r9)

# no mid-item cut: kept bodies must be whole
import re as _re
bodies = _re.findall(r"z+", r9)
check("kept bodies are whole, never mid-sliced",
      all(len(b) == 1200 for b in bodies), f"lengths={sorted(set(len(b) for b in bodies))}")

# a single item too large keeps its title, marks the cut, leaves no dangling escape
solo = lb.render(cfg, "needs-me", {"items": [{"title": "Solo", "body": "_" * 9000}]})
check("single oversized item keeps its title", "Solo" in solo)
check("single oversized item marks the cut", "\u2026" in solo)
check("single oversized item under cap", len(solo) <= lb.TG_LIMIT, f"len={len(solo)}")
_tail = solo.rstrip("\u2026] ")
check("no dangling escape at the cut", (len(_tail) - len(_tail.rstrip("\\"))) % 2 == 0)

# portability + ordering + concurrency, asserted against real code
check("no POSIX-only strftime directive", "%-I" not in _code and "%-d" not in _code)
check("timestamp renders", bool(lb.now_local(cfg)), lb.now_local(cfg))
check("push happens before save in cmd_set",
      _code.index("push(cfg, args.topic, state)") < _code.index("save_state(args.topic, state)"))
check("board_lock wraps the read-modify-push cycle", _code.count("with board_lock") >= 2)

with lb.board_lock("locktest"):
    lb.LOCK_TIMEOUT = 1
    try:
        with lb.board_lock("locktest"):
            check("lock excludes a concurrent holder", False, "second acquire succeeded")
    except SystemExit:
        check("lock excludes a concurrent holder", True)
check("lock released on exit", not lb.state_path("locktest").with_suffix(".lock").exists())

# V2 template correctness, proven against the live API before being asserted here
tpl = lb.render(cfg, "needs-me", {"items": [{"title": "A.B", "body": "x"}, {"title": "C", "body": "y"}]})
_specials = ".!()~>#+=|{}"
_bad = [(i, c) for i, c in enumerate(tpl) if c in _specials and (i == 0 or tpl[i-1] != "\\")]
check("template escapes its own V2 punctuation", not _bad, f"first={_bad[:1]}")

# a network fault must not crash, must not fall through to posting a duplicate board
_real_api = lb.api
lb.api = lambda cfg, method, **kw: {"ok": False, "description": "network error calling " + method}
try:
    lb.push(cfg, "needs-me", {"message_id": 123, "items": []})
    check("network fault does not post a duplicate board", False, "push returned normally")
except SystemExit as e:
    check("network fault does not post a duplicate board", "network error" in str(e))
finally:
    lb.api = _real_api

# --- review round 3 ---
print("\n== oversized titles and lock ownership ==")

# A title alone can blow the cap: cmd_set limits body length but not title length.
for tlen in (600, 5000, 20000):
    r = lb.render(cfg, "needs-me", {"items": [{"title": "T" * tlen, "body": "b" * 9000}]})
    check(f"title {tlen} chars stays under cap", len(r) <= lb.TG_LIMIT, f"len={len(r)}")
    check(f"title {tlen} chars not falsely empty", "Nothing needs you" not in r)
    # Under-cap is not enough: dropping the item entirely also fits, and leaves a header
    # claiming N are waiting above a blank board. The item itself has to SURVIVE.
    check(f"title {tlen} chars still shows the item", "T" in r, f"len={len(r)}")

# worst case: long title made ENTIRELY of characters that double under escaping
r = lb.render(cfg, "needs-me", {"items": [{"title": "_" * 8000, "body": "." * 8000}]})
check("all-special title stays under cap", len(r) <= lb.TG_LIMIT, f"len={len(r)}")
_t = r.rstrip("\u2026 ")
check("no dangling escape after title truncation",
      (len(_t) - len(_t.rstrip("\\"))) % 2 == 0)

# many oversized items, each with an oversized title
r = lb.render(cfg, "needs-me", {"items": [{"title": "T" * 4000, "body": "b" * 4000} for _ in range(5)]})
check("many oversized titles stay under cap", len(r) <= lb.TG_LIMIT, f"len={len(r)}")

# Lock ownership. The race a reviewer described: several runs observe the SAME stale lock.
# If each then DELETES it, a later delete destroys an earlier run's freshly acquired lock and
# two runs end up inside the critical section, losing an update.
#
# This cannot be provoked through the public API alone: once a winner acquires, the lock's
# mtime is fresh, so a later racer's staleness check simply fails and it waits. The bug needs
# a racer that passed the staleness check BEFORE the winner acquired, which through
# board_lock() only happens on a narrow timing window (about 1 round in 40 with 6 contending
# runs, too flaky and slow to gate a commit on).
#
# So assert the property that makes the race impossible instead: reclaiming a stale lock must
# RE-VERIFY staleness at the moment it acts, under its own exclusive break lock, rather than
# acting on an observation made earlier. Replay exactly that: stale lock observed, then
# refreshed by a winner, then the late racer attempts its reclaim.
lock_path = lb.state_path("racetest").with_suffix(".lock")
shutil.rmtree(lock_path, ignore_errors=True)
lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_path.mkdir()
(lock_path / "owner").write_text("crashed-run")
_old = time.time() - 3600
os.utime(lock_path, (_old, _old))
lb.LOCK_STALE_SECONDS, lb.LOCK_TIMEOUT = 60, 1

check("lock starts out stale", time.time() - lock_path.stat().st_mtime > 60)

# A winner reclaims and installs a FRESH lock (this is what board_lock does on acquire).
_a_holder = lb.board_lock("racetest")
_a_holder.__enter__()
_a_token = (lock_path / "owner").read_text()
check("winner replaced the abandoned lock", _a_token != "crashed-run")
check("winner's lock is fresh", time.time() - lock_path.stat().st_mtime < 60)

# The late racer now runs the reclaim path it had already decided to take. A correct
# implementation re-checks under the break lock and backs off; the buggy one deletes.
_reclaim = getattr(lb, "_reclaim_stale_for_test", None)
if _reclaim is None:
    # reclaim_stale is a closure inside board_lock, so drive the same decision the way the
    # acquire loop does: call board_lock in a run whose deadline has already passed. It must
    # refuse rather than break a fresh lock.
    lb.LOCK_TIMEOUT = 0
    try:
        with lb.board_lock("racetest"):
            check("late racer cannot break a refreshed lock", False, "it acquired")
    except SystemExit:
        check("late racer cannot break a refreshed lock", True)
    lb.LOCK_TIMEOUT = 1

check("winner's lock survived", lock_path.exists()
      and (lock_path / "owner").read_text() == _a_token)
_a_holder.__exit__(None, None, None)
check("lock released once the holder exits", not lock_path.exists())

# And the guarantee itself, stated directly against the source: reclaim must re-stat while
# holding an exclusive break lock. This is the invariant that closes the timing window.
# Assert against the REACHABLE acquire loop, not merely that a helper exists somewhere:
# dead code would satisfy a bare "is it defined" check.
_acquire = _code.split("def board_lock")[1]
check("acquire loop delegates reclaim instead of deleting in place",
      "reclaim_stale()" in _acquire
      and "shutil.rmtree(path, ignore_errors=True)\n            continue" not in _acquire)
check("reclaim serializes behind an exclusive break lock", ".break" in _acquire)
# The re-check is the whole point: acting on the age observed BEFORE taking the break lock
# is exactly the bug. Require a genuine comparison inside reclaim_stale, not just a stat.
_reclaim_src = _acquire.split("def reclaim_stale")[1].split("while True:")[0]
check("reclaim re-checks staleness while holding the break lock",
      "path.stat().st_mtime" in _reclaim_src
      and "<= LOCK_STALE_SECONDS" in _reclaim_src)

shutil.rmtree(lock_path, ignore_errors=True)
lb.LOCK_STALE_SECONDS, lb.LOCK_TIMEOUT = 300, 30

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
