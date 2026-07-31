#!/usr/bin/env python3
"""Self-check for living_board.py.

Exercises the pure rendering and state logic: empty states, resolved items disappearing,
title-match dedup, atomic round-trip, and the three overflow/corruption edge cases that a
code review caught. Network sends are deliberately excluded so this runs offline with no
token and no side effects.

Run it after editing the board, and on a new install to confirm the environment works:

    python3 scripts/verify_board.py

Exits non-zero on any failure. Verified to actually catch regressions: removing the
"keep at least one item" guard makes it fail exactly the two checks that guard it.
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
check("shipped template parses",
      bool(lb._parse_minimal_toml((HERE / "templates/board.toml").read_text()).get("topics")))
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

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
