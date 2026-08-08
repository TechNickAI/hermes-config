#!/usr/bin/env python3
"""Robustify collectors v1 — gather FACTS only. No interpretation, no LLM.

Emits plain text sections. Each collector is independent and fail-soft:
a broken collector reports its own failure rather than killing the run.
"""
import json, os, re, shlex, shutil, sqlite3, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
# HERMES_HOME lets this collector inspect a DIFFERENT agent's home than the one it runs
# as — required for the external-observation pattern, where one agent checks another
# (a wedged agent cannot report that it is wedged). Falls back to ~/.hermes.
H = Path(os.environ.get("HERMES_HOME") or (HOME / ".hermes")).expanduser()
DEEP = "--deep" in sys.argv
# Every path interpolated into a shell string goes through SQ. A home directory with a
# space in it (common on macOS) otherwise splits into two arguments and the command
# either fails or, worse, silently scans the wrong tree.
SQ = shlex.quote
# systemctl prefixes non-running units with U+25CF. It must be stripped before the unit
# name can be read positionally, or the "unit name" comes back as the bullet itself.
BULLET = "\u25cf"
OUT = []
FAILED = []
STALE_READS = []  # databases that could only be opened via immutable=1 (see ro_connect)
# Module scope so it is unit-testable against real argv strings. See c_processes for the
# two verified false-detection bugs this pattern exists to prevent.
GW_RE = re.compile(r"hermes_cli\.main\b.*\bgateway\b.*\brun\b")

def ro_connect(path, timeout=8):
    """Read-only connect that survives WAL.

    sqlite3.connect is LAZY: `mode=ro` succeeds at connect time and only raises
    'unable to open database file' on the first query, because a WAL database
    needs to create a -shm sidecar that read-only mode forbids. So each candidate
    must be validated with a real query before being returned."""
    last = None
    # mode=ro first. immutable=1 is a LAST resort and is NOT equivalent: it ignores the
    # -wal file, so a live database is read from a potentially stale main image. That can
    # yield both false alarms and false confidence, so callers are told when it happened.
    for uri, mode in ((f"file:{path}?mode=ro", "ro"), (f"file:{path}?immutable=1", "immutable")):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=timeout)
            con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            if mode == "immutable":
                # Surfaced as a fact, not swallowed: every conclusion drawn from this
                # database may be based on a pre-WAL-checkpoint image.
                STALE_READS.append(Path(path).name)
            return con
        except Exception as e:
            last = e
    con = sqlite3.connect(str(path), timeout=timeout)
    con.execute("PRAGMA query_only=ON")
    con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    return con

def sh(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"ERR:{e}"

def section(name):
    OUT.append(f"\n## {name}")

def fact(k, v):
    OUT.append(f"{k}: {v}")

def collector(fn):
    def wrap():
        t0 = time.time()
        try:
            fn()
            OUT.append(f"_collector_ms: {int((time.time()-t0)*1000)}")
        except Exception as e:
            FAILED.append(fn.__name__)
            OUT.append(f"COLLECTOR_FAILED: {fn.__name__}: {type(e).__name__}: {e}")
    return wrap

def redact(text):
    """Replace the user's home prefix with ~ in anything we print.

    The report is handed to an LLM, pasted into chats, and committed to logs. Absolute
    paths carry the account name on every line for no diagnostic benefit — the relative
    structure is what matters. Longest prefix first so profile paths are not left with a
    dangling fragment.
    """
    s = str(text)
    for pref, repl in sorted(((str(H), "$HERMES_HOME"), (str(HOME), "~")),
                             key=lambda x: -len(x[0])):
        s = s.replace(pref, repl)
    return s

def cortex_db_path():
    """Locate the cortex database, or None.

    Cortex is frequently a SHARED store living at the root Hermes home even when
    HERMES_HOME points at a profile subdirectory. Checking only the profile path
    reported MISSING for a 1.5GB store that was present and healthy — a false alarm
    about the memory system, which is exactly the kind of noise that gets a monitor
    muted. Check the profile location first, then the root home.
    """
    roots = [H / "cortex"]
    root_home = HOME / ".hermes" / "cortex"
    if root_home not in roots:
        roots.append(root_home)
    for r in roots:
        for name in (".plugin.db", "cortex.db"):
            if (r / name).exists():
                return r / name
    return None

def expected_interval_h(job):
    """Approximate hours between runs for a job, or None if it can't be determined.

    Deliberately coarse: this feeds a staleness THRESHOLD, not a scheduler. Getting
    "weekly-ish" instead of "exactly 168h" is fine; treating a weekly job as if it
    should produce output every 48h is not, because that false alarm fires every
    single run and trains the reader to ignore the report.
    """
    sched = job.get("schedule")
    if isinstance(sched, dict):
        kind = (sched.get("kind") or "").lower()
        expr = str(sched.get("expr") or sched.get("display") or "").strip()
        if kind == "interval":
            for key, mult in (("hours", 1), ("minutes", 1 / 60), ("seconds", 1 / 3600), ("days", 24)):
                v = sched.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    return v * mult
        if kind == "once":
            return None
    else:
        expr = str(sched or "").strip()
    m = re.fullmatch(r"(?:every\s+)?(\d+)\s*([mhd])\w*", expr, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return n * {"m": 1 / 60, "h": 1, "d": 24}[unit]
    # 5-field cron. Estimate from the coarsest field that is actually constrained.
    parts = expr.split()
    if len(parts) == 5:
        minute, hour, dom, _mon, dow = parts
        if dow != "*" and dom == "*":
            return 168.0                      # weekly
        if dom != "*":
            return 720.0                      # monthly
        if hour != "*":
            # N specific hours per day
            n = len([h for h in hour.replace("-", ",").split(",") if h.strip()])
            if hour.startswith("*/"):
                try:
                    return float(hour[2:])
                except ValueError:
                    return 24.0
            return 24.0 / max(n, 1)
        if minute.startswith("*/"):
            try:
                return float(minute[2:]) / 60
            except ValueError:
                return 1.0
        if minute != "*":
            return 1.0                        # hourly at a fixed minute
        return 1 / 60                         # every minute
    return None

# ---------------------------------------------------------------- machine
@collector
def c_machine():
    section("MACHINE")
    is_mac = sys.platform == "darwin"
    target = "/System/Volumes/Data" if is_mac else "/"
    df = sh(f"df -k {target} | tail -1").split()
    if len(df) >= 5:
        total_g = int(df[1]) / 1048576
        free_g = int(df[3]) / 1048576
        fact("disk_target", target)
        fact("disk_total_gb", f"{total_g:.1f}")
        fact("disk_free_gb", f"{free_g:.1f}")
        fact("disk_pct_used", df[4])
    fact("uptime", sh("uptime | sed 's/.*up //;s/,.*load/ load/'"))
    if is_mac:
        vm = sh("vm_stat")
        m = re.search(r"Pages free:\s+(\d+)", vm)
        ps = re.search(r"page size of (\d+)", vm)
        if m and ps:
            fact("mem_free_gb", f"{int(m.group(1))*int(ps.group(1))/1073741824:.2f}")
        fact("mem_pressure_pct_free", sh("memory_pressure 2>/dev/null | tail -1 | grep -oE '[0-9]+%' | head -1"))
        fact("thermal", sh("pmset -g therm 2>/dev/null | grep -i 'CPU_Speed_Limit' | head -1") or "nominal")
    fact("clock_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    fact("tz_local", sh("date +%Z"))
    fact("tz_setting", sh("readlink /etc/localtime | sed 's|.*zoneinfo/||'"))
    drift = sh("sntp -t 3 time.apple.com 2>/dev/null | tail -1") if is_mac else sh("timedatectl 2>/dev/null | head -3")
    fact("clock_drift_probe", drift[:120] or "unavailable")

# ---------------------------------------------------------------- disk trajectory
@collector
def c_disk_trend():
    section("DISK_TRAJECTORY")
    state = H / "robustify" / "disk_history.db"
    state.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(state)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS disk(ts INTEGER PRIMARY KEY, free_gb REAL)")
    target = "/System/Volumes/Data" if sys.platform == "darwin" else "/"
    df = sh(f"df -k {target} | tail -1").split()
    free_gb = int(df[3]) / 1048576 if len(df) >= 4 else None
    now = int(time.time())
    if free_gb is not None:
        con.execute("INSERT OR REPLACE INTO disk VALUES(?,?)", (now, free_gb))
        con.commit()
    rows = con.execute("SELECT ts, free_gb FROM disk WHERE ts > ? ORDER BY ts", (now - 86400,)).fetchall()
    fact("samples_24h", len(rows))
    if len(rows) >= 2:
        span_h = (rows[-1][0] - rows[0][0]) / 3600
        delta = rows[-1][1] - rows[0][1]
        fact("free_gb_delta_24h", f"{delta:+.2f}")
        if span_h > 0.5:
            rate = delta / span_h
            fact("free_gb_per_hour", f"{rate:+.3f}")
            if rate < -0.5 and free_gb is not None:
                fact("hours_to_full_at_current_rate", f"{free_gb/abs(rate):.1f}")
    else:
        fact("note", "insufficient history for trajectory (needs 2+ samples)")
    con.execute("DELETE FROM disk WHERE ts < ?", (now - 30 * 86400,))
    con.commit(); con.close()

# ---------------------------------------------------------------- jobs
@collector
def c_jobs():
    section("SCHEDULED_JOBS")
    jf = H / "cron" / "jobs.json"
    jobs = json.loads(jf.read_text())
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    enabled = [j for j in jobs if j.get("enabled", True)]
    fact("jobs_total", len(jobs))
    fact("jobs_enabled", len(enabled))

    db = H / "cron" / "executions.db"
    ran_ids, statuses, oldest = set(), {}, None
    if db.exists():
        con = ro_connect(db)
        con.execute("PRAGMA query_only=ON")
        cols = [r[1] for r in con.execute("PRAGMA table_info(executions)")]
        idcol = "job_id" if "job_id" in cols else cols[0]
        tcol = next((c for c in ("started_at", "start_time", "created_at", "timestamp") if c in cols), None)
        for r in con.execute(f"SELECT DISTINCT {idcol} FROM executions"):
            ran_ids.add(r[0])
        if "status" in cols:
            for s, n in con.execute("SELECT status, COUNT(*) FROM executions GROUP BY status"):
                statuses[s] = n
            # a job stuck in 'running' looks identical to one legitimately in flight.
            # duration is the discriminator.
            if tcol:
                try:
                    stuck = con.execute(
                        f"SELECT {idcol}, {tcol} FROM executions WHERE status='running' ORDER BY {tcol}"
                    ).fetchall()
                    # A stale 'running' row is NOT evidence the job is stalled. Verified on
                    # this host: 3 of 4 long-running rows belonged to jobs that had since
                    # completed 31-120 times — orphan rows from a crash, harmless. The real
                    # signal is a stale row with NO later terminal execution for that job.
                    pidcol = "pid" if "pid" in cols else None
                    orphan, stalled = [], []
                    for jid, started in stuck:
                        try:
                            t = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                            age_h = (datetime.now(t.tzinfo) - t).total_seconds() / 3600
                        except Exception:
                            age_h = -1
                        if age_h < 2:
                            continue
                        later = con.execute(
                            f"SELECT COUNT(*) FROM executions WHERE {idcol}=? "
                            f"AND status IN ('completed','failed') AND {tcol} > ?",
                            (jid, started)).fetchone()[0]
                        alive = ""
                        if pidcol:
                            row = con.execute(
                                f"SELECT pid FROM executions WHERE {idcol}=? AND {tcol}=?",
                                (jid, started)).fetchone()
                            if row and row[0]:
                                alive = " owner_pid_alive" if sh(f"ps -p {row[0]} >/dev/null 2>&1 && echo y") == "y" else " owner_pid_dead"
                        if later > 0:
                            orphan.append((jid, age_h, later, alive))
                        else:
                            stalled.append((jid, age_h, alive))
                    OUT.append(f"jobs_stalled_no_later_completion: {len(stalled)}")
                    OUT.append(f"jobs_orphan_running_rows_benign: {len(orphan)}")
                    for jid, age_h, alive in stalled:
                        OUT.append(f"  STALLED_JOB: id={jid} running {age_h:.1f}h "
                                   f"with ZERO later completed/failed runs{alive}")
                    for jid, age_h, later, alive in orphan:
                        OUT.append(f"  ORPHAN_ROW: id={jid} row {age_h:.1f}h old but "
                                   f"{later} later terminal runs — job is healthy{alive}")
                except Exception:
                    pass
        if tcol:
            oldest = con.execute(f"SELECT MIN({tcol}), MAX({tcol}) FROM executions").fetchone()
            # An aggregate failure count is not actionable and is easy to misread. 46
            # failures spread over 11 jobs is a very different situation from 46 in one,
            # and a job that failed 20 times then recovered is not currently broken.
            # Attribute failures to jobs, and say whether the LAST run still failed.
            try:
                rows = con.execute(
                    f"SELECT {idcol}, COUNT(*) FROM executions WHERE status='failed' "
                    f"GROUP BY {idcol} ORDER BY COUNT(*) DESC LIMIT 8").fetchall()
                for jid, n in rows:
                    last = con.execute(
                        f"SELECT status FROM executions WHERE {idcol}=? "
                        f"ORDER BY {tcol} DESC LIMIT 1", (jid,)).fetchone()
                    still = (last or [""])[0]
                    OUT.append(f"  FAILING_JOB: id={jid} failures_in_window={n} "
                               f"latest_run={still or 'unknown'}"
                               f"{' STILL_FAILING' if still == 'failed' else ' (recovered)'}")
            except Exception:
                pass
        con.close()
    fact("execution_status_counts", statuses or "unavailable")
    if oldest and oldest[0]:
        fact("execution_history_range", f"{oldest[0]} .. {oldest[1]}")

    # CRITICAL: executions.db retains ~20h. "not in executions" does NOT mean
    # "never ran" — it usually means "ran before the retention window". Corroborate
    # against on-disk output before making that claim, or this reports false alarms.
    retention_h = None
    if oldest and oldest[0]:
        try:
            t0 = datetime.fromisoformat(str(oldest[0]).replace("Z", "+00:00"))
            retention_h = (datetime.now(t0.tzinfo) - t0).total_seconds() / 3600
            fact("execution_retention_hours", f"{retention_h:.1f}")
        except Exception:
            pass

    outdir = H / "cron" / "output"
    absent = [j for j in enabled if j.get("id") not in ran_ids]
    truly_never, ran_outside_window = [], []
    for j in absent:
        d = outdir / str(j.get("id"))
        files = [f for f in d.glob("*") if f.is_file()] if d.exists() else []
        if files:
            newest = max(f.stat().st_mtime for f in files)
            ran_outside_window.append((j, (time.time() - newest) / 3600))
        else:
            truly_never.append(j)

    fact("jobs_no_evidence_of_ever_running", len(truly_never))
    for j in truly_never:
        OUT.append(f"  NEVER_RAN: {str(j.get('name'))[:44]} | id={j.get('id')} | no execution row AND no output file")
    fact("jobs_last_ran_before_retention_window", len(ran_outside_window))
    for j, age in sorted(ran_outside_window, key=lambda x: -x[1]):
        OUT.append(f"  RAN_OUTSIDE_WINDOW: {str(j.get('name'))[:40]} | last output {age:.0f}h ago")

    # overdue: next_run_at in the past by more than a grace window
    now = datetime.now(timezone.utc)
    overdue = []
    for j in enabled:
        nr = j.get("next_run_at")
        if not nr:
            continue
        try:
            t = datetime.fromisoformat(str(nr).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            late = (now - t).total_seconds()
            if late > 3600:
                overdue.append((j.get("name"), late / 3600))
        except Exception:
            pass
    fact("jobs_overdue_count", len(overdue))
    for name, hrs in sorted(overdue, key=lambda x: -x[1])[:10]:
        OUT.append(f"  OVERDUE: {str(name)[:44]} | {hrs:.1f}h late")

# ---------------------------------------------------------------- job output freshness (silent failure)
@collector
def c_job_output():
    section("JOB_OUTPUT_FRESHNESS")
    outdir = H / "cron" / "output"
    if not outdir.exists():
        fact("note", "no cron output dir")
        return
    # map job id -> name so findings are actionable, not hex
    names = {}
    try:
        jj = json.loads((H / "cron" / "jobs.json").read_text())
        jj = jj if isinstance(jj, list) else jj.get("jobs", [])
        names = {j.get("id"): (j.get("name") or "?") for j in jj}
        enabled_ids = {j.get("id") for j in jj if j.get("enabled", True)}
        expected = {j.get("id"): expected_interval_h(j) for j in jj}
    except Exception:
        enabled_ids = set()
        expected = {}
    now = time.time()
    stale, empty = [], []
    dirs = [d for d in outdir.iterdir() if d.is_dir()]
    fact("job_output_dirs", len(dirs))
    for d in dirs:
        files = sorted(d.glob("*"), key=lambda f: f.stat().st_mtime if f.exists() else 0)
        if not files:
            empty.append(d.name); continue
        newest = files[-1]
        age_h = (now - newest.stat().st_mtime) / 3600
        size = newest.stat().st_size
        # A flat 48h threshold cries wolf on every weekly and monthly job, and a monitor
        # that cries wolf is ignored within a week. Compare against each job's OWN
        # cadence plus a grace multiplier; fall back to 48h only when cadence is unknown.
        exp = expected.get(d.name)
        threshold = (exp * 2.5) if exp else 48.0
        if age_h > threshold:
            stale.append((d.name, age_h, size, exp, threshold))
        elif size == 0:
            empty.append(d.name)
    fact("output_dirs_empty", len(empty))
    for e in empty[:8]:
        OUT.append(f"  NO_OUTPUT: {e} | {names.get(e,'?')[:40]}")
    # only stale-flag jobs that are still ENABLED — a disabled job's old output is expected
    stale_enabled = [s for s in stale if not enabled_ids or s[0] in enabled_ids]
    fact("output_stale_vs_own_cadence_enabled", len(stale_enabled))
    fact("output_stale_total", len(stale))
    for n, a, s, exp, th in sorted(stale_enabled, key=lambda x: -x[1])[:8]:
        basis = f"expected ~{exp:.0f}h" if exp else "cadence unknown, default 48h"
        OUT.append(f"  STALE_OUTPUT: {names.get(n,'?')[:38]} | {a:.0f}h old "
                   f"| {basis} | threshold {th:.0f}h | {s}b | id={n}")

# ---------------------------------------------------------------- logs
@collector
def c_logs():
    section("LOGS")
    logdir = H / "logs"
    if not logdir.exists():
        fact("note", "no log dir"); return
    files = [(f, f.stat().st_size) for f in logdir.rglob("*") if f.is_file()]
    total = sum(s for _, s in files)
    fact("log_files", len(files))
    fact("log_total_mb", f"{total/1048576:.1f}")
    big = sorted(files, key=lambda x: -x[1])[:6]
    for f, s in big:
        if s > 5 * 1048576:
            OUT.append(f"  LARGE_LOG: {f.name} | {s/1048576:.1f}MB")
    rotated = len([f for f, _ in files if re.search(r"\.\d+(\.gz)?$", f.name)])
    fact("rotated_files_present", rotated)

# ---------------------------------------------------------------- processes / gateway
@collector
def c_processes():
    section("PROCESSES")
    # Do NOT use pgrep here. Two verified failure modes, both found by running this
    # collector on the host it was monitoring:
    #
    #  1. pgrep EXCLUDES ITSELF AND ALL ITS ANCESTORS by default (see `man pgrep`, -a).
    #     When this collector is launched by an agent whose own gateway is the ancestor
    #     process, that gateway is invisible to pgrep. Verified: the monitored host's
    #     own gateway (--profile <self>) was silently absent from every report while
    #     four sibling gateways showed up fine. A monitor that cannot see the agent
    #     running it is worse than no monitor — it reports "healthy" about a blind spot.
    #  2. 'hermes.*gateway' is an unanchored substring match over the FULL argv, so any
    #     unrelated process whose command line happens to contain both words matches.
    #     Verified: a node test runner in a checkout under a path containing "hermes"
    #     was counted as two gateway processes, inflating the count from 5 to 6.
    #
    # `ps -axo` sees every process including our own ancestors, and matching on the
    # module invocation (`hermes_cli.main ... gateway run`) is specific to the real thing.
    procs = []
    for line in sh("ps -axo pid=,ppid=,etime=,args=").splitlines():
        parts = line.split(None, 3)
        if len(parts) != 4:
            continue
        pid, ppid, etime, argv = parts
        if not GW_RE.search(argv):
            continue
        if "ps -axo" in argv:  # never count the enumerating command itself
            continue
        procs.append((pid, ppid, etime, argv))
    fact("gateway_process_count", len(procs))
    # PID list alone can't distinguish duplicate supervisors from distinct profiles.
    # Emit parent + age + profile so that's a fact, not an inference.
    for pid, ppid, etime, argv in procs:
        prof = re.search(r"--profile\s+(\S+)", argv)
        OUT.append(f"  GATEWAY: pid={pid} ppid={ppid} age={etime} "
                   f"profile={prof.group(1) if prof else 'default'}")
    # Per-profile RSS, not just the first PID: one bloated gateway among several was
    # invisible when only head -1 was sampled.
    total_rss = 0
    for pid, _ppid, _etime, argv in procs:
        rss = sh(f"ps -o rss= -p {pid} 2>/dev/null").strip()
        if rss.isdigit():
            mb = int(rss) / 1024
            total_rss += mb
            prof = re.search(r"--profile\s+(\S+)", argv)
            OUT.append(f"  GATEWAY_RSS: profile={prof.group(1) if prof else 'default'} rss_mb={mb:.0f}")
    if procs:
        fact("gateway_rss_total_mb", f"{total_rss:.0f}")
    if sys.platform == "darwin":
        # Anchor on the reverse-DNS label prefix. An unanchored /hermes|ace/ matched
        # com.apple.f'ace'timemessagestored and appapl'ace'holdersyncd on a real host,
        # reporting two Apple system units as failing Hermes services.
        fact("launchd_hermes_units", sh("launchctl list 2>/dev/null | grep -c 'ai\\.hermes\\.'"))
        failing = sh("launchctl list 2>/dev/null | awk '$2!=0 && $2!=\"-\" && $3 ~ /^ai\\.hermes\\./ {print $3\"|\"$2}'")
        fact("launchd_failing_count", len([l for l in failing.splitlines() if l.strip()]))
        fact("launchd_note", "last_exit is the LAST run's code and may predate a manual fix; corroborate with the job's own log")
        for line in failing.splitlines():
            if "|" in line:
                lbl, code = line.rsplit("|", 1)
                # when did that unit last actually run? stdout/err mtime is the cheapest proxy
                age = ""
                for cand in (H/"logs"/f"{lbl.split('.')[-1]}.stdout.log", H/"logs"/f"{lbl.split('.')[-1]}.log"):
                    if cand.exists() and cand.stat().st_size >= 0:
                        age = f" last_run_log_age_h={(time.time()-cand.stat().st_mtime)/3600:.1f}"
                        break
                OUT.append(f"  LAUNCHD_NONZERO_EXIT: {lbl} last_exit={code}{age}")
    else:
        # systemd is the Linux analogue of launchd. Without this, a Linux host running
        # its gateway as a --user service had NO service-supervision coverage at all:
        # the process check alone cannot tell "unit stopped cleanly" from "unit failed
        # and is in a restart loop". Verified on a real fleet box carrying three failed
        # units that the collector reported nothing about.
        for scope in ("--user", "--system"):
            listing = sh(f"systemctl {scope} list-units --type=service --all --no-legend "
                         f"--no-pager 2>/dev/null")
            if not listing or listing.startswith("ERR"):
                continue
            units = [l for l in listing.splitlines() if l.strip()]
            hermes_units = [l for l in units if "hermes" in l.lower()]
            fact(f"systemd{scope.replace('--', '_')}_hermes_units", len(hermes_units))
            for l in hermes_units:
                f_ = l.replace(BULLET, " ").split()
                if len(f_) >= 4:
                    OUT.append(f"  SYSTEMD_UNIT: {f_[0]} load={f_[1]} active={f_[2]} sub={f_[3]}")
            # Failed units OUTSIDE hermes still matter: a failed portal/notification unit
            # is how "the agent cannot send you anything" starts. Report, don't diagnose.
            failed = [l.replace(BULLET, " ").split()[0]
                      for l in units if " failed " in f" {l} "]
            fact(f"systemd{scope.replace('--', '_')}_failed_count", len(failed))
            for u in failed[:8]:
                OUT.append(f"  SYSTEMD_FAILED: {u}")
    fact("chrome_procs", sh("pgrep -c -f 'Google Chrome' 2>/dev/null") or "0")

# ---------------------------------------------------------------- databases
@collector
def c_databases():
    section("DATABASES")
    cortex_db = cortex_db_path() or (H / "cortex" / ".plugin.db")
    for name, path in [("state", H / "state.db"), ("cortex", cortex_db),
                       ("executions", H / "cron" / "executions.db"), ("kanban", H / "kanban.db")]:
        if not path.exists():
            fact(f"{name}_db", "MISSING"); continue
        sz = path.stat().st_size / 1048576
        # quick_check on a multi-GB db costs ~40s. Cheap liveness probe on every run;
        # full integrity only when --deep is passed (daily), never on the 15-min tick.
        try:
            con = ro_connect(path)
            # quick_check costs ~40s on a multi-GB db, so it is skipped by default on
            # large files. The LABEL must say which check actually ran — an integrity
            # claim the reader cannot distinguish from a real one is the exact failure
            # this tool exists to catch.
            if DEEP or sz < 200:
                ic = con.execute("PRAGMA quick_check").fetchone()[0]
                if ic == "ok":
                    ic = "ok(quick_check)"
            else:
                con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                ic = "readable(deep-check-skipped)"
            con.close()
        except Exception as e:
            ic = f"ERR:{type(e).__name__}"
        fact(f"{name}_db", f"{sz:.1f}MB integrity={ic}")

# ---------------------------------------------------------------- cortex
@collector
def c_cortex():
    section("CORTEX")
    # Resolve the cortex DIRECTORY the same way as the database: it may be the shared
    # store at the root home rather than under the profile.
    dbp = cortex_db_path()
    cdir = dbp.parent if dbp else (H / "cortex")
    if not cdir.exists():
        fact("note", "no cortex dir"); return
    fact("cortex_dir", str(cdir))
    md = list(cdir.rglob("*.md"))
    fact("pages_on_disk", len(md))
    daily = cdir / "daily"
    if daily.exists():
        dl = list(daily.glob("*.md"))
        fact("daily_journals", len(dl))
        if dl:
            newest = max(dl, key=lambda f: f.stat().st_mtime)
            fact("newest_journal", f"{newest.name} ({(time.time()-newest.stat().st_mtime)/3600:.0f}h ago)")
        else:
            fact("newest_journal", "NONE — daily dir is empty")
            # where ARE journals landing? the configured store being empty while another
            # tree receives writes is the exact live bug on this host.
            alt = sh(f"find {SQ(str(H))} -maxdepth 4 "
                     f"-type d -name daily -not -path {SQ(str(H / 'cortex') + '/*')} "
                     f"2>/dev/null | head -3", timeout=10)
            if alt:
                for a in alt.splitlines():
                    n = sh(f"ls -1 '{a}'/*.md 2>/dev/null | wc -l").strip()
                    OUT.append(f"  ALT_JOURNAL_DIR: {a} ({n} md files)")
    db = cortex_db_path()
    if db:
        try:
            con = ro_connect(db)
            tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in ("pages", "documents", "chunks"):
                if t in tabs:
                    fact(f"rows_{t}", con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            fts = [t for t in tabs if "fts" in t.lower() and not t.endswith(("_data","_idx","_docsize","_config","_content"))]
            for t in fts:
                # 'integrity-check' is a WRITE statement in FTS5. On a read-only handle
                # it raises 'readonly database', which is NOT corruption. Distinguish.
                try:
                    con.execute(f"INSERT INTO {t}({t}) VALUES('integrity-check')")
                    fact(f"fts_{t}", "ok")
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "readonly" in msg or "attempt to write" in msg:
                        try:
                            n = con.execute(f"SELECT count(*) FROM {t} WHERE {t} MATCH 'the'").fetchone()[0]
                            fact(f"fts_{t}", f"queryable(matches={n}, write-check-skipped)")
                        except Exception as e2:
                            fact(f"fts_{t}", f"QUERY_FAILED: {str(e2)[:60]}")
                    else:
                        fact(f"fts_{t}", f"CORRUPT: {str(e)[:60]}")
                except Exception as e:
                    fact(f"fts_{t}", f"CORRUPT: {str(e)[:60]}")
            con.close()
        except Exception as e:
            fact("cortex_db_error", str(e)[:80])

# ---------------------------------------------------------------- integrations (passive)
@collector
def c_integrations():
    section("INTEGRATIONS_PASSIVE")
    # Never authenticates. Reads recent real traffic as evidence of a working path.
    db = H / "state.db"
    if db.exists():
        try:
            con = ro_connect(db, timeout=10)
            now = time.time()
            for label, src in [("telegram", "telegram"), ("cron", "cron"), ("cli", "cli")]:
                r = con.execute(
                    "SELECT MAX(m.timestamp) FROM messages m JOIN sessions s ON m.session_id=s.id WHERE s.source=?",
                    (src,)).fetchone()
                if r and r[0]:
                    fact(f"last_{label}_activity_hours_ago", f"{(now-r[0])/3600:.1f}")
                else:
                    fact(f"last_{label}_activity_hours_ago", "NEVER")
            con.close()
        except Exception as e:
            fact("traffic_probe_error", str(e)[:80])
    envf = H / ".env"
    if envf.exists():
        keys = [l.split("=")[0] for l in envf.read_text().splitlines()
                if l.strip() and not l.startswith("#") and "=" in l]
        fact("env_credential_keys_present", len(keys))
        fact("env_file_mode", oct(envf.stat().st_mode)[-3:])
        # names only, never values
        fact("integration_keys", ",".join(sorted(set(
            k.split("_")[0] for k in keys if k))[:18]))

# ---------------------------------------------------------------- user surface
@collector
def c_surface():
    section("USER_SURFACE")
    ports = sh("lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9\" \"$1}' | sort -u")
    listening = [l for l in ports.splitlines() if l.strip()]
    fact("listening_sockets", len(listening))
    for l in listening[:14]:
        OUT.append(f"  LISTEN: {l}")
    pm2 = sh("PM2_HOME=$HOME/.pm2 pm2 jlist 2>/dev/null")
    if pm2.startswith("["):
        try:
            procs = json.loads(pm2)
            fact("pm2_process_count", len(procs))
            now = time.time()
            for p in procs:
                st = p.get("pm2_env", {}) or {}
                # restart count alone is ambiguous (lifetime vs recent). Uptime
                # disambiguates: high restarts + long uptime = historical, not active.
                up = st.get("pm_uptime")
                up_h = f"{(now - up/1000)/3600:.1f}h" if up else "?"
                OUT.append(
                    f"  PM2: {p.get('name')} | {st.get('status')} | "
                    f"restarts={st.get('restart_time')} unstable={st.get('unstable_restarts')} "
                    f"current_uptime={up_h}")
        except Exception:
            fact("pm2", "unparseable")
    else:
        fact("pm2_process_count", "0 or unavailable")

# ---------------------------------------------------------------- config
@collector
def c_config():
    section("CONFIG")
    # The git checkout lives under the REAL home even when HERMES_HOME points at a
    # profile subdirectory, so resolve it from both and use whichever exists.
    src = next((c for c in (H / "hermes-agent", HOME / ".hermes" / "hermes-agent")
                if (c / ".git").is_dir()), None)
    if src:
        fact("install_path", str(src))
        ver = sh(f"cd {SQ(str(src))} && git describe --tags --always 2>/dev/null")
        if not ver or ver.startswith("ERR"):
            vpy = src / "venv" / "bin" / "python"
            if vpy.exists():
                ver = sh(f"{SQ(str(vpy))} -c "
                         "'import importlib.metadata as m; print(m.version(\"hermes-agent\"))' 2>/dev/null")
        fact("hermes_version", ver or "unknown")
        fact("install_git_sha", sh(f"cd {SQ(str(src))} && git rev-parse --short HEAD 2>/dev/null"))
        fact("install_git_branch", sh(f"cd {SQ(str(src))} && git rev-parse --abbrev-ref HEAD 2>/dev/null"))
    else:
        # Absence of a git checkout is a FACT about install method (pip/uv-tool), not a
        # failure. Say so explicitly rather than emitting a blank the reader must guess at.
        fact("install_path", "no git checkout found — likely pip/uv-tool install")
        fact("hermes_version", sh("hermes --version 2>/dev/null") or "unknown")
    cfg = H / "config.yaml"
    if cfg.exists():
        txt = cfg.read_text()
        lines_ = txt.splitlines()
        for i, line in enumerate(lines_):
            s = line.strip()
            if s.startswith("model:"):
                val = s.split(":", 1)[1].strip()
                if val:
                    fact("config_model", val[:70])
                else:
                    # parent block — emit its children, which hold the real values
                    kids = []
                    for nxt in lines_[i+1:i+8]:
                        if nxt.strip() and not nxt.startswith((" ", "\t")):
                            break
                        if ":" in nxt:
                            kids.append(nxt.strip()[:44])
                    fact("config_model_block", " | ".join(kids) or "empty")
                break
        for key in ("provider:", "timezone:"):
            for line in lines_:
                if line.strip().startswith(key):
                    v = line.strip().split(":", 1)[1].strip()
                    if key == "timezone:" and v in ("", "''", '""'):
                        fact("config_timezone", f"EMPTY — falls back to system tz ({sh('date +%Z')}); "
                                                f"scheduling depends on host, not config")
                    else:
                        fact(f"config_{key.rstrip(':')}", v)
                    break
        fact("config_bytes", len(txt))
    sk = H / "skills"
    if sk.exists():
        fact("skills_local", len([d for d in sk.iterdir() if d.is_dir()]))
    fact("config_profiles", len([d for d in (H / "profiles").iterdir() if d.is_dir()])
         if (H / "profiles").is_dir() else 0)

# ---------------------------------------------------------------- backups
@collector
def c_backups():
    section("BACKUPS")
    # A backup that has never been verified is a hope. On one real host a nightly
    # cortex backup failed 573 consecutive times over ~10 weeks and nothing noticed,
    # because the launchd job's exit code was never read by anything. Consecutive
    # trailing failures — not the exit code of the last run — is the signal.
    logdir = H / "logs"
    for log in sorted(logdir.glob("*backup*.log")) if logdir.exists() else []:
        # skip launchd stdout/stderr sidecars — they're empty by design and only add noise
        if log.name.endswith((".stderr.log", ".stdout.log")) or log.stat().st_size == 0:
            continue
        try:
            lines = log.read_text(errors="ignore").splitlines()[-4000:]
        except Exception as e:
            fact(f"{log.stem}_read_error", str(e)[:60]); continue
        ok = [l for l in lines if "✓" in l]
        bad = [l for l in lines if "✗" in l]
        if not ok and not bad:
            continue
        fact(f"{log.stem}_success_count", len(ok))
        fact(f"{log.stem}_failure_count", len(bad))
        last_ok = ok[-1] if ok else None
        last_bad = bad[-1] if bad else None
        if last_ok:
            m = re.match(r"\[([^\]]+)\]", last_ok)
            if m:
                raw = m.group(1)
                # normalize -0500 -> -05:00 for fromisoformat
                norm = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
                try:
                    t = datetime.fromisoformat(norm)
                    fact(f"{log.stem}_last_success_hours_ago",
                         f"{(datetime.now(t.tzinfo)-t).total_seconds()/3600:.1f}")
                except Exception:
                    fact(f"{log.stem}_last_success_raw", raw)
        else:
            fact(f"{log.stem}_last_success", "NEVER IN WINDOW")
        # consecutive trailing failures = the signal that matters
        trail = 0
        for l in reversed(lines):
            if "✗" in l: trail += 1
            elif "✓" in l: break
        fact(f"{log.stem}_consecutive_failures", trail)
        if last_bad and trail:
            OUT.append(f"  LAST_FAILURE: {last_bad[:130]}")

# ---------------------------------------------------------------- main
def main():
    # The hostname is genuinely useful when triaging a fleet (which box is this?), but
    # it is also identifying. Default to a stable short hash; --show-host opts in when
    # the operator wants the real name in their own private output.
    host = sh("hostname -s")
    if "--show-host" not in sys.argv:
        import hashlib
        host = "host-" + hashlib.sha256(host.encode()).hexdigest()[:8]
    print("# ROBUSTIFY COLLECTOR REPORT")
    print(f"host: {host}")
    print(f"collected_at: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    for fn in (c_machine, c_disk_trend, c_jobs, c_job_output, c_logs,
               c_processes, c_databases, c_cortex, c_integrations, c_surface,
               c_config, c_backups):
        fn()
    OUT.append("\n## COLLECTOR_SELF_HEALTH")
    OUT.append(f"collectors_failed: {len(FAILED)} {FAILED if FAILED else ''}")
    if STALE_READS:
        OUT.append(f"stale_immutable_reads: {sorted(set(STALE_READS))} — these databases "
                   f"could only be opened with immutable=1, which IGNORES the -wal file. "
                   f"Any conclusion drawn from them may reflect a pre-checkpoint image.")
    # Redact at the FINAL chokepoint: many findings are emitted via OUT.append directly
    # rather than through fact(), so redacting only in fact() would leak paths from
    # exactly the lines that matter most (failures, alt dirs, log excerpts).
    print(redact("\n".join(OUT)))

if __name__ == "__main__":
    main()
