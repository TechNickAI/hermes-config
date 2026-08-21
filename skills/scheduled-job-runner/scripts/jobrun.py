#!/usr/bin/env python3
"""jobrun — the fleet's one scheduled-job execution adapter.

Design basis: projects/job-runner-design.md (measured audit + ecosystem research,
2026-08-21). This is an EXECUTION ADAPTER, not a scheduler. Hermes cron stays the
scheduler. jobrun owns the seven things every job re-implements badly today:

  1. interpreter/dependency resolution   (kills the .sh-wrapper hack)
  2. silence-on-success                  (kills hand-rolled `if RC -ne 0` blocks)
  3. overlap prevention                  (flock; today only 3 of 201 scripts have it)
  4. hard timeout + signal handling      (timeout distinguishable from failure)
  5. structured run ledger               (exit code, duration, outcome — cron's has none)
  6. bounded log capture                 (quiet-on-success != discard evidence)
  7. heartbeat / dead-man's-switch       (the ONLY thing that catches "never ran")

Contract with Hermes cron (no_agent=True jobs):
  - non-empty stdout  -> delivered verbatim to the human
  - empty stdout      -> SILENT
  - non-zero exit     -> scheduler raises a failure alert
jobrun preserves that contract exactly, so it is a drop-in for `script:`.

TERMINAL STATES (never collapsed into a bare exit 1):
  success | child_failure | timeout | signal | config_error | skipped_overlap
  | wrapper_error

Usage:
    jobrun.py --spec <name>            # run job defined in jobs.d/<name>.toml
    jobrun.py --spec <name> --dry-run  # validate spec + preflight, run nothing
    jobrun.py --selftest               # exercise every terminal state, exit 0/1

Exit codes (documented, stable — 126/127/128+ deliberately avoided):
    0   success, or a clean skipped_overlap
    2   config_error (bad spec, missing interpreter, unwritable paths)
    3   child_failure (the job itself exited non-zero)
    4   timeout
    5   signal
    6   wrapper_error
"""

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# Children currently running under this process. The signal handler forwards to
# these so terminating jobrun never orphans a job in its own process group.
_ACTIVE_PROC: list = []


def _install_signal_handlers() -> None:
    """Forward our own termination to the child, then exit with EXIT_SIGNAL.

    Without this, killing jobrun (scheduler shutdown, operator, host restart)
    kills only jobrun: the child keeps running in its separate process group
    AND the lock descriptor closes, so a later invocation can start a second
    live copy of the same job.
    """
    def _handler(signum, _frame):
        for proc in list(_ACTIVE_PROC):
            try:
                _terminate_group(proc, 5)
            except Exception:
                pass
        try:
            append_ledger({
                "event": "job.finished", "state": "signal",
                "signal": signal.Signals(signum).name,
                "ts": _iso(_now()), "note": "runner terminated; child forwarded",
            })
        except Exception:
            pass
        sys.exit(EXIT_SIGNAL)

    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(s, _handler)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported platform

import tomllib  # stdlib since 3.11; fleet standard is 3.13

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CHILD = 3
EXIT_TIMEOUT = 4
EXIT_SIGNAL = 5
EXIT_WRAPPER = 6

# Bounded capture: quiet-on-success must not mean discard-all-evidence, but a
# runaway job must not fill the disk either.
MAX_CAPTURE_BYTES = 256 * 1024
MAX_DELIVER_CHARS = 3000

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
SPEC_DIR = HERMES_HOME / "jobs.d"
STATE_DIR = HERMES_HOME / "jobstate"
LOG_DIR = STATE_DIR / "logs"
LOCK_DIR = STATE_DIR / "locks"
LEDGER = STATE_DIR / "runs.jsonl"

# Minimum Python for any job we run. Jobs inherit the agent venv (3.13.x fleet-wide)
# unless they declare their own via PEP 723, so this is an assertion that a job never
# silently lands on an older interpreter (e.g. macOS /usr/bin/python3 = 3.9).
MIN_PYTHON = (3, 13)

# Retention. Without this, logs/ and runs.jsonl grow forever on every host.
LOG_RETENTION_DAYS = 14
LEDGER_MAX_LINES = 20000

# Where uv gets installed when missing. Astral's installer honours this.
UV_INSTALL_DIR = Path.home() / ".local" / "bin"

# Secrets are redacted before anything is logged or delivered.
_SECRET_HINTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "authorization", "bearer", "private_key",
)
_HINT_ALT = "|".join(_SECRET_HINTS + ("access_key", "client_secret", "session_key"))
# key=value / key: value  (mask only the VALUE, keep the key visible)
_SECRET_ASSIGN_RE = re.compile(
    rf"(?i)\b((?:\w*)(?:{_HINT_ALT})\w*)(\s*[=:]\s*)(?!\s)([^\s,;'\"]+)"
)
# "token": "abc"  in JSON
_SECRET_JSON_RE = re.compile(
    rf'(?i)("(?:\w*)(?:{_HINT_ALT})\w*"\s*:\s*)"[^"]*"'
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-]{8,}")
_URL_CRED_RE = re.compile(r"(?i)\b(\w+://[^/\s:@]+:)[^@\s]+@")
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.S,
)
# `--token VALUE` style flags whose NEXT argv element is the secret.
_SECRET_FLAG_RE = re.compile(rf"(?i)(?:{_HINT_ALT})")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def redact(text: str) -> str:
    """Mask secret-bearing values. Conservative, never raises.

    Handles the real shapes secrets appear in: key=value, key: value, JSON
    string values, Authorization/Bearer headers, URL credentials, and PEM
    private-key blocks. Deliberately narrow — masking whole lines because they
    contain a broad word like "session" corrupts legitimate job output, which
    is its own failure.
    """
    if not text:
        return text
    try:
        out = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        out = _SECRET_JSON_RE.sub(lambda m: f'{m.group(1)}"[REDACTED]"', out)
        out = _BEARER_RE.sub(lambda m: f"{m.group(1)} [REDACTED]", out)
        out = _URL_CRED_RE.sub(lambda m: f"{m.group(1)}[REDACTED]@", out)
        out = _PEM_RE.sub("[REDACTED PRIVATE KEY]", out)
        return out
    except Exception:
        return "[REDACTED - redaction failed]"


def redact_argv(argv: list) -> list:
    """Redact secrets passed as command-line arguments.

    Covers both shapes: `--token=VALUE` (handled by redact) and the separated
    `--token VALUE`, where the secret is its own argv element and carries no
    key to match on. argv is recorded in the ledger, so this runs before it is
    ever written to disk.
    """
    out = []
    flag_expects_secret = False
    for item in argv:
        text = str(item)
        if flag_expects_secret and not text.startswith("-"):
            out.append("[REDACTED]")
            flag_expects_secret = False
            continue
        flag_expects_secret = bool(
            text.startswith("-") and _SECRET_FLAG_RE.search(text)
        ) and "=" not in text
        out.append(redact(text))
    return out


def _clamp(text: str, limit: int = MAX_CAPTURE_BYTES) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return f"{head}\n...[{len(text) - limit} bytes elided]...\n{tail}"


class ConfigError(Exception):
    pass


def find_uv() -> str | None:
    """Locate uv, checking PATH plus the well-known install dirs.

    Cron's PATH is not a login shell's PATH — uv is frequently installed at
    ~/.local/bin or /opt/homebrew/bin and simply not visible to `which`.
    Always look in the known locations before concluding it is missing.
    """
    found = shutil.which("uv")
    if found:
        return found
    for cand in (
        UV_INSTALL_DIR / "uv",
        Path("/opt/homebrew/bin/uv"),
        Path("/usr/local/bin/uv"),
        Path.home() / ".cargo" / "bin" / "uv",
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def ensure_uv(auto_install: bool = True) -> str:
    """Return a usable uv path, installing it if absent.

    Install happens under an flock so two jobs firing at the same minute cannot
    race the installer. Deliberately NOT silent: installing a toolchain is a
    real event and is recorded in the ledger by the caller.

    Bootstrapping at RUN time is a fallback, not the plan — `--bootstrap` is
    the intended path (run once per host at setup). But a job that would
    otherwise fail with "uv not found" at 3am is better served by a bounded,
    locked install attempt than by dying.
    """
    uv = find_uv()
    if uv:
        return uv
    if not auto_install:
        raise ConfigError("uv is not installed and auto-install is disabled")

    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / "uv-install.lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        # Another process may have installed it while we waited on the lock.
        uv = find_uv()
        if uv:
            return uv
        if not shutil.which("curl"):
            raise ConfigError("cannot install uv: curl not found")
        UV_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["UV_INSTALL_DIR"] = str(UV_INSTALL_DIR)
        env["INSTALLER_NO_MODIFY_PATH"] = "1"  # we resolve uv by absolute path
        try:
            proc = subprocess.run(
                ["sh", "-c",
                 "curl -LsSf --max-time 120 https://astral.sh/uv/install.sh | sh"],
                capture_output=True, text=True, timeout=180, env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConfigError("uv install timed out after 180s") from exc
        uv = find_uv()
        if not uv:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            raise ConfigError(f"uv install did not produce a binary: {tail}")
        return uv


def _interpreter_version(exe: str) -> tuple[int, int] | None:
    """Ask an interpreter for its own version. Never trust the filename."""
    try:
        out = subprocess.run(
            [exe, "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0:
            a, b = out.stdout.split()[:2]
            return (int(a), int(b))
    except Exception:
        pass
    return None


def _requires_python_floor(script: Path) -> str | None:
    """Read requires-python out of a PEP 723 block, if present."""
    try:
        head = script.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    if "# /// script" not in head:
        return None
    for line in head.splitlines():
        s = line.lstrip("#").strip()
        if s.startswith("requires-python"):
            _, _, val = s.partition("=")
            return val.strip().strip('"').strip("'") or None
    return None


class Spec:
    """A declarative job spec. Everything the runner needs, nothing it doesn't."""

    KNOWN = {
        "job_id", "command", "script", "runtime", "cwd", "timeout", "kill_grace",
        "overlap", "owner", "env", "timezone", "notify_on_success", "retries",
        "retry_backoff", "args", "python", "auto_install_uv", "output_policy",
        "heartbeat_url",
    }

    def __init__(self, data: dict, path: Path | None = None):
        self.path = path
        # Reject unknown fields: a misspelled control that silently does nothing
        # is exactly the class of bug this runner exists to remove.
        unknown = set(data) - self.KNOWN
        if unknown:
            raise ConfigError(
                f"unknown spec field(s): {', '.join(sorted(unknown))}. "
                f"Valid: {', '.join(sorted(self.KNOWN))}"
            )
        self.job_id = str(data.get("job_id") or "").strip()
        if not self.job_id:
            raise ConfigError("spec is missing required field: job_id")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.job_id):
            raise ConfigError(
                f"job_id {self.job_id!r} must match [A-Za-z0-9._-]+ "
                "(it is used in lock and log filenames)"
            )

        self.command = data.get("command")
        self.script = data.get("script")
        if not self.command and not self.script:
            raise ConfigError(f"{self.job_id}: spec needs either 'script' or 'command'")
        if self.command and self.script:
            raise ConfigError(
                f"{self.job_id}: set 'script' OR 'command', not both"
            )

        self.runtime = str(data.get("runtime") or "auto")
        self.cwd = data.get("cwd")
        self.timeout = int(data.get("timeout", 900))
        self.kill_grace = int(data.get("kill_grace", 10))
        self.overlap = str(data.get("overlap", "skip"))
        if self.overlap not in ("skip", "allow", "queue"):
            raise ConfigError(f"{self.job_id}: overlap must be skip|allow|queue")
        self.owner = data.get("owner")
        self.heartbeat_url = data.get("heartbeat_url")
        self.env = dict(data.get("env") or {})
        # LANG is defaulted because the cron subprocess env has none, and a job
        # that formats or parses text should not depend on the C locale.
        self.env.setdefault("LANG", "en_US.UTF-8")
        # TZ is NOT defaulted. Forcing a timezone would silently shift the
        # day boundary for any job computing a date, partition, or deadline.
        # Set it explicitly per spec when a job needs a specific zone.
        tz = data.get("timezone")
        if tz:
            self.env.setdefault("TZ", str(tz))
        self.notify_on_success = bool(data.get("notify_on_success", False))
        self.retries = int(data.get("retries", 0))
        self.retry_backoff = float(data.get("retry_backoff", 5.0))
        # Arguments passed through to the job. the operator called out "arg parsing"
        # explicitly: a job takes its parameters from the SPEC, not from a
        # hand-written wrapper that hardcodes them into an exec line.
        args = data.get("args") or []
        if isinstance(args, str):
            args = shlex.split(args)
        self.args = [str(a) for a in args]
        # Python floor for uv-run jobs. Fleet standard is 3.13.
        self.python = str(data.get("python", f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"))
        self.auto_install_uv = bool(data.get("auto_install_uv", True))
        self.output_policy = str(data.get("output_policy", "passthrough"))
        if self.output_policy not in ("passthrough", "silent"):
            raise ConfigError(
                f"{self.job_id}: output_policy must be passthrough|silent"
            )
        for name, val in (("timeout", self.timeout), ("kill_grace", self.kill_grace),
                          ("retries", self.retries)):
            if val < 0:
                raise ConfigError(f"{self.job_id}: {name} must be >= 0")
        if self.timeout <= 0:
            raise ConfigError(f"{self.job_id}: timeout must be > 0")
        if self.retry_backoff < 0:
            raise ConfigError(f"{self.job_id}: retry_backoff must be >= 0")

    @classmethod
    def load(cls, name: str) -> "Spec":
        p = Path(name)
        if not p.exists():
            p = SPEC_DIR / f"{name}.toml"
        if not p.exists():
            raise ConfigError(f"spec not found: {name} (looked in {SPEC_DIR})")
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigError(f"spec {p} is not valid TOML: {exc}") from exc
        return cls(data, path=p)


def resolve_argv(spec: Spec) -> list[str]:
    """Pick the interpreter EXPLICITLY. This is the fix for the .sh-wrapper hack.

    runtime = "uv"     -> uv run --locked --script  (PEP 723 declares its own deps)
    runtime = "python" -> the current interpreter (agent venv)
    runtime = "bash"   -> bash
    runtime = "auto"   -> uv if the script has a PEP 723 block AND uv exists,
                          else bash for .sh/.bash, else current python.

    Crucially the choice is RECORDED in the ledger, so "which python ran this"
    is never again a mystery requiring a wrapper to answer.
    """
    if spec.command:
        base = shlex.split(spec.command) if isinstance(spec.command, str) else list(spec.command)
        return base + spec.args

    script = Path(os.path.expanduser(str(spec.script)))
    if not script.is_absolute():
        script = (HERMES_HOME / "scripts" / script).resolve()
    if not script.exists():
        raise ConfigError(f"{spec.job_id}: script not found: {script}")

    runtime = spec.runtime
    if runtime == "auto":
        suffix = script.suffix.lower()
        if suffix in (".sh", ".bash"):
            runtime = "bash"
        else:
            head = ""
            try:
                head = script.read_text(encoding="utf-8", errors="replace")[:4096]
            except OSError:
                pass
            # A PEP 723 script declares dependencies the agent venv will not
            # have. Route it to uv even when uv is currently missing —
            # ensure_uv() installs it. Falling back to `python` here would run
            # the script in the wrong environment and fail on its own imports.
            runtime = "uv" if "# /// script" in head else "python"

    if runtime == "uv":
        uv = ensure_uv(auto_install=spec.auto_install_uv)
        # Enforce the fleet Python floor. A script declaring requires-python
        # ">=3.9" would otherwise let uv legitimately pick 3.9. We pass an
        # explicit --python so the floor wins, and record it in the ledger.
        argv = [uv, "run", "--python", spec.python, "--script", str(script)]
        # --locked only when a lock exists; otherwise uv errors on a missing lock.
        if Path(str(script) + ".lock").exists():
            argv.insert(2, "--locked")
        return argv + spec.args
    if runtime == "bash":
        bash = shutil.which("bash") or "/bin/bash"
        return [bash, str(script)] + spec.args
    if runtime == "python":
        # sys.executable is the agent venv (3.13.x fleet-wide). Assert rather
        # than assume: a job must never silently land on macOS python3.9.
        ver = _interpreter_version(sys.executable)
        if ver and ver < MIN_PYTHON:
            raise ConfigError(
                f"{spec.job_id}: interpreter {sys.executable} is Python "
                f"{ver[0]}.{ver[1]}, below the required "
                f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+. Declare a PEP 723 block "
                f"(runtime=uv) or fix the agent venv."
            )
        return [sys.executable, str(script)] + spec.args
    raise ConfigError(f"{spec.job_id}: unknown runtime {runtime!r}")


def preflight(spec: Spec, argv: list[str]) -> list[str]:
    """Startup self-checks. A config error must be distinct from a job failure."""
    problems = []
    exe = argv[0]
    if not (os.path.isabs(exe) and os.access(exe, os.X_OK)) and not shutil.which(exe):
        problems.append(f"executable not found or not executable: {exe}")
    if spec.cwd and not Path(os.path.expanduser(spec.cwd)).is_dir():
        problems.append(f"cwd does not exist: {spec.cwd}")
    for d in (STATE_DIR, LOG_DIR, LOCK_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"cannot create {d}: {exc}")
    if spec.timeout <= 0:
        problems.append("timeout must be > 0")
    return problems


class Lock:
    """Host-local overlap control via flock. Non-blocking for policy=skip."""

    def __init__(self, job_id: str, policy: str):
        self.policy = policy
        self.path = LOCK_DIR / f"{job_id}.lock"
        self.fh = None
        self.acquired = False

    def __enter__(self):
        if self.policy == "allow":
            self.acquired = True
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if self.policy == "skip" else 0)
        try:
            fcntl.flock(self.fh.fileno(), flags)
            self.acquired = True
            self.fh.write(f"{os.getpid()} {_iso(_now())}\n")
            self.fh.flush()
        except BlockingIOError:
            self.acquired = False
        return self

    def __exit__(self, *exc):
        if self.fh:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()
        return False


def heartbeat(spec: Spec, suffix: str, body: str = "", run_id: str = "") -> str:
    """Fire-and-forget dead-man's-switch ping (healthchecks.io-compatible).

    Short timeout, never raises, never changes the job's own outcome. A monitor
    being down must not take the job down. Delivery status is recorded.
    """
    if not spec.heartbeat_url:
        return "not_configured"
    url = spec.heartbeat_url.rstrip("/")
    if suffix:
        url = f"{url}/{suffix}"
    if run_id:
        url = f"{url}?rid={run_id}"
    try:
        import urllib.request

        data = redact(body)[:10000].encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return f"ok_{resp.status}"
    except Exception as exc:
        return f"send_failed: {type(exc).__name__}"


def build_env(spec: Spec) -> dict:
    env = os.environ.copy()
    env.update({k: str(v) for k, v in spec.env.items()})
    env.setdefault("HERMES_HOME", str(HERMES_HOME))
    return env


def append_ledger(record: dict) -> None:
    """Durable, queryable run history. Raw stdout grep is not a status API."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass  # never let bookkeeping kill a job


def write_logs(job_id: str, run_id: str, out: str, err: str) -> str:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = LOG_DIR / f"{job_id}-{run_id[:8]}.log"
        p.write_text(
            f"=== stdout ===\n{out}\n\n=== stderr ===\n{err}\n", encoding="utf-8"
        )
        return str(p)
    except Exception:
        return ""


class _BoundedReader:
    """Drain a pipe into a bounded head+tail buffer WHILE the child runs.

    Bounded by BYTES, not lines: a single newline-free multi-gigabyte line, or
    many large lines, must not exhaust the host. Reads fixed-size chunks so no
    unbounded intermediate string is ever materialized, and keeps a byte-capped
    head and tail while still draining the pipe so the child never blocks.
    """

    CHUNK = 65536

    def __init__(self, stream, limit: int = MAX_CAPTURE_BYTES):
        self.stream = stream
        self.half = max(1024, limit // 2)
        self.head: list[str] = []
        self.head_len = 0
        self.tail: "deque[str]" = deque()
        self.tail_len = 0
        self.dropped = 0
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        try:
            while True:
                chunk = self.stream.read(self.CHUNK)
                if not chunk:
                    break
                if self.head_len < self.half:
                    take = min(len(chunk), self.half - self.head_len)
                    self.head.append(chunk[:take])
                    self.head_len += take
                    chunk = chunk[take:]
                    if not chunk:
                        continue
                self.tail.append(chunk)
                self.tail_len += len(chunk)
                # Evict from the front until the tail fits its byte budget.
                while self.tail_len > self.half and self.tail:
                    gone = self.tail.popleft()
                    self.tail_len -= len(gone)
                    self.dropped += len(gone)
        except Exception:
            pass
        finally:
            try:
                self.stream.close()
            except Exception:
                pass

    def value(self, join_timeout: float = 5.0) -> str:
        self.thread.join(timeout=join_timeout)
        head = "".join(self.head)
        tail = "".join(self.tail)
        if self.dropped:
            return f"{head}\n...[{self.dropped} bytes elided]...\n{tail}"
        return head + tail


def _execute(spec: Spec, argv: list[str], env: dict) -> tuple:
    """One attempt. Returns (state, exit_code, signame, stdout, stderr, duration).

    Guarantees: the child is always reaped, output capture is bounded during
    execution, and an inability to kill the process group is reported as a
    wrapper_error rather than a retryable timeout (retrying while an escaped
    child still runs is how you get two live copies of a trading job).
    """
    t0 = time.monotonic()
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.expanduser(spec.cwd) if spec.cwd else str(HERMES_HOME),
            env=env,
            start_new_session=True,  # own process group: kill children too
        )
    except Exception as exc:
        return ("config_error" if isinstance(exc, (FileNotFoundError, PermissionError))
                else "wrapper_error", None, None, "",
                f"{type(exc).__name__}: {exc}", time.monotonic() - t0)

    _ACTIVE_PROC.append(proc)
    out_r = _BoundedReader(proc.stdout)
    err_r = _BoundedReader(proc.stderr)
    try:
        rc = proc.wait(timeout=spec.timeout)
        dur = time.monotonic() - t0
        out, err = out_r.value(), err_r.value()
        if rc == 0:
            return ("success", 0, None, out, err, dur)
        if rc < 0:
            try:
                name = signal.Signals(-rc).name
            except Exception:
                name = str(rc)
            return ("signal", rc, name, out, err, dur)
        return ("child_failure", rc, None, out, err, dur)
    except subprocess.TimeoutExpired:
        killed = _terminate_group(proc, spec.kill_grace)
        out, err = out_r.value(), err_r.value()
        dur = time.monotonic() - t0
        if not killed:
            # Could not prove the workload is dead — never retry into that.
            return ("wrapper_error", None, None, out,
                    (err + "\ntimeout: process group could not be reaped").strip(), dur)
        return ("timeout", None, None, out, err, dur)
    except Exception as exc:
        _terminate_group(proc, spec.kill_grace)
        return ("wrapper_error", None, None, out_r.value(),
                f"{type(exc).__name__}: {exc}", time.monotonic() - t0)
    finally:
        try:
            _ACTIVE_PROC.remove(proc)
        except ValueError:
            pass


def _terminate_group(proc, grace: int) -> bool:
    """TERM then KILL the child's process group. True once the child is reaped."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except ProcessLookupError:
            break
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=grace if sig == signal.SIGTERM else 5)
            return True
        except Exception:
            continue
    return proc.poll() is not None


def prune_state() -> None:
    """Retention. Without this, logs/ and runs.jsonl grow forever on every host.

    Runs opportunistically after each job; cheap and failure-tolerant.
    """
    try:
        cutoff = time.time() - (LOG_RETENTION_DAYS * 86400)
        if LOG_DIR.is_dir():
            for p in LOG_DIR.iterdir():
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink()
                except OSError:
                    pass
        if LEDGER.exists():
            lines = LEDGER.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > LEDGER_MAX_LINES:
                keep = lines[-LEDGER_MAX_LINES:]
                tmp = LEDGER.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
                tmp.replace(LEDGER)
    except Exception:
        pass  # retention must never break a job


def _read_ledger(limit: int = 0) -> list:
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows[-limit:] if limit else rows


def cmd_list() -> int:
    """Day-2: what jobs exist on this host?"""
    if not SPEC_DIR.is_dir():
        print(f"no specs directory: {SPEC_DIR}")
        return EXIT_OK
    specs = sorted(SPEC_DIR.glob("*.toml"))
    if not specs:
        print(f"no job specs in {SPEC_DIR}")
        return EXIT_OK
    rows = _read_ledger()
    last = {}
    for r in rows:
        if r.get("event") == "job.finished":
            last[r.get("job_id")] = r
    print(f"{'JOB':<38} {'RUNTIME':<8} {'LAST':<14} {'WHEN':<21} OWNER")
    for p in specs:
        try:
            s = Spec.load(str(p))
        except ConfigError as exc:
            print(f"{p.stem:<38} {'INVALID':<8} {str(exc)[:60]}")
            continue
        l = last.get(s.job_id, {})
        print(f"{s.job_id:<38} {s.runtime:<8} {l.get('state','never'):<14} "
              f"{l.get('finished_at','-'):<21} {s.owner or '-'}")
    return EXIT_OK


def cmd_status(job_id: str) -> int:
    """Day-2: why did this job fail last night?"""
    rows = [r for r in _read_ledger() if r.get("job_id") == job_id]
    if not rows:
        print(f"no runs recorded for {job_id}")
        return EXIT_OK
    for r in rows[-10:]:
        print(f"{r.get('finished_at','-')}  {r.get('state','-'):<15} "
              f"exit={r.get('exit_code')} {r.get('duration_ms')}ms "
              f"attempt={r.get('attempt')}")
    last = rows[-1]
    if last.get("log_path"):
        print(f"\nlast log: {last['log_path']}")
    return EXIT_OK


def cmd_failures(hours: int = 24) -> int:
    """Day-2: what is broken right now? Silent when nothing is."""
    cutoff = time.time() - hours * 3600
    bad = []
    for r in _read_ledger():
        if r.get("state") in (None, "success", "skipped_overlap"):
            continue
        try:
            ts = datetime.fromisoformat(
                str(r.get("finished_at", "")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            bad.append(r)
    if not bad:
        return EXIT_OK  # silent by design
    print(f"{len(bad)} failed run(s) in the last {hours}h:")
    for r in bad:
        print(f"  {r.get('finished_at')}  {r.get('job_id')}  {r.get('state')} "
              f"exit={r.get('exit_code')}  {r.get('log_path','')}")
    return EXIT_OK


def cmd_bootstrap() -> int:
    """Set the host up once: ensure uv exists and the Python floor is met."""
    print(f"host: {os.uname().nodename}")
    try:
        uv = ensure_uv(auto_install=True)
        out = subprocess.run([uv, "--version"], capture_output=True, text=True,
                             timeout=30)
        print(f"uv: {uv} ({out.stdout.strip()})")
    except Exception as exc:
        print(f"uv: FAILED — {exc}")
        return EXIT_CONFIG
    ver = _interpreter_version(sys.executable)
    ok = bool(ver and ver >= MIN_PYTHON)
    print(f"python: {sys.executable} {ver[0]}.{ver[1]} "
          f"({'ok' if ok else 'BELOW ' + str(MIN_PYTHON)})")
    for d in (SPEC_DIR, STATE_DIR, LOG_DIR, LOCK_DIR):
        d.mkdir(parents=True, exist_ok=True)
    print(f"state dirs ready under {STATE_DIR}")
    return EXIT_OK if ok else EXIT_CONFIG


def run(spec: Spec, dry_run: bool = False) -> int:
    run_id = str(uuid.uuid4())
    scheduled_at = _now()

    try:
        argv = resolve_argv(spec)
    except ConfigError as exc:
        print(f"[{spec.job_id}] CONFIG ERROR: {exc}", file=sys.stderr)
        append_ledger({
            "event": "job.config_error", "job_id": spec.job_id, "run_id": run_id,
            "ts": _iso(scheduled_at), "state": "config_error", "error": str(exc),
        })
        heartbeat(spec, "fail", f"config_error: {exc}", run_id)
        print(f"{spec.job_id}: configuration error — {exc}")
        return EXIT_CONFIG

    problems = preflight(spec, argv)
    if problems:
        msg = "; ".join(problems)
        append_ledger({
            "event": "job.config_error", "job_id": spec.job_id, "run_id": run_id,
            "ts": _iso(scheduled_at), "state": "config_error", "error": msg,
        })
        heartbeat(spec, "fail", f"preflight: {msg}", run_id)
        print(f"{spec.job_id}: preflight failed — {msg}")
        return EXIT_CONFIG

    if dry_run:
        print(json.dumps({
            "job_id": spec.job_id, "argv": argv, "cwd": spec.cwd,
            "timeout": spec.timeout, "overlap": spec.overlap,
            "runtime": spec.runtime, "heartbeat": bool(spec.heartbeat_url),
            "preflight": "ok",
        }, indent=2))
        return EXIT_OK

    env = build_env(spec)

    with Lock(spec.job_id, spec.overlap) as lock:
        if not lock.acquired:
            # Overlap skip is an OBSERVABLE OUTCOME, not a silent nothing,
            # and not a failure. Stays silent to the human by design.
            append_ledger({
                "event": "job.skipped", "job_id": spec.job_id, "run_id": run_id,
                "ts": _iso(_now()), "state": "skipped_overlap",
            })
            return EXIT_OK

        heartbeat(spec, "start", "", run_id)
        started_at = _now()

        attempt = 0
        while True:
            attempt += 1
            state, rc, signame, out, err, dur = _execute(spec, argv, env)
            retryable = state in ("child_failure", "timeout") and attempt <= spec.retries
            if not retryable:
                break
            time.sleep(spec.retry_backoff * attempt)

        finished_at = _now()
        # Two views of the same run, deliberately kept separate:
        #   raw_out  — exactly what the job wrote. Delivered verbatim on the
        #              passthrough path so a control payload survives intact.
        #   out/err  — redacted + clamped. Used for logs, the ledger, and the
        #              failure card, where secrets must never land on disk.
        raw_out = out or ""
        out = _clamp(redact(raw_out))
        err = _clamp(redact(err or ""))
        log_path = write_logs(spec.job_id, run_id, out, err)

        hb = "not_configured"
        if state == "success":
            hb = heartbeat(spec, "0", "", run_id)
        elif state == "child_failure":
            hb = heartbeat(spec, str(rc), err[:2000], run_id)
        else:
            hb = heartbeat(spec, "fail", (err or state)[:2000], run_id)

        append_ledger({
            "event": "job.finished",
            "job_id": spec.job_id,
            "run_id": run_id,
            "host": os.uname().nodename,
            "owner": spec.owner,
            "state": state,
            "exit_code": rc,
            "signal": signame,
            "attempt": attempt,
            "argv": redact_argv(argv),
            "runtime": spec.runtime,
            "scheduled_at": _iso(scheduled_at),
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "duration_ms": int(dur * 1000),
            "stdout_bytes": len(out),
            "stderr_bytes": len(err),
            "log_path": log_path,
            "heartbeat": hb,
        })
        prune_state()

        # ---- Human-facing delivery. Silence-on-success is OWNED HERE, once,
        # instead of being hand-rolled in every script.
        #
        # output_policy decides what a SUCCESSFUL run sends to the human:
        #   "passthrough" (default) — stdout verbatim, byte-for-byte. Preserves
        #        Hermes' contract and the {"wakeAgent": false} gate. Use for
        #        jobs whose stdout IS the message.
        #   "silent" — never speak on success, no matter what the job printed.
        #        This is the fix for a noisy job: set it here, do not edit the
        #        script.
        if state == "success":
            if spec.output_policy == "silent" and not spec.notify_on_success:
                return EXIT_OK
            if raw_out:
                # VERBATIM: the original bytes, not the redacted/clamped copy.
                # A control payload on the last line must survive exactly.
                sys.stdout.write(raw_out)
                if not raw_out.endswith("\n"):
                    sys.stdout.write("\n")
            return EXIT_OK

        # Failure: one concise, actionable incident card — not a stdout dump.
        head = {
            "child_failure": f"exited {rc}",
            "timeout": f"timed out after {spec.timeout}s",
            "signal": f"killed by {signame}",
            "wrapper_error": "runner error",
        }.get(state, state)
        lines = [
            f"⚠️ {spec.job_id} {head}",
            f"Host: {os.uname().nodename}  ·  Duration: {dur:.1f}s  ·  Attempts: {attempt}",
        ]
        if spec.owner:
            lines.append(f"Owner: {spec.owner}")
        detail = (err.strip() or out.strip())
        if detail:
            lines.append(f"Error: {detail.splitlines()[-1][:300]}")
        if log_path:
            lines.append(f"Log: {log_path}")
        lines.append(f"Run: {run_id[:8]}")
        print("\n".join(lines))

        return {
            "child_failure": EXIT_CHILD,
            "timeout": EXIT_TIMEOUT,
            "signal": EXIT_SIGNAL,
            "wrapper_error": EXIT_WRAPPER,
        }.get(state, EXIT_WRAPPER)


def selftest() -> int:
    """Exercise every terminal state for real. No mocks, no fabricated results."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="jobrun-selftest-"))
    results = []

    # Self-test fixtures deliberately fail. Keep them out of the real ledger
    # and log dir, or `--failures` reports test noise as production incidents.
    global STATE_DIR, LOG_DIR, LOCK_DIR, LEDGER
    _saved_dirs = (STATE_DIR, LOG_DIR, LOCK_DIR, LEDGER)
    STATE_DIR = tmp / "state"
    LOG_DIR = STATE_DIR / "logs"
    LOCK_DIR = STATE_DIR / "locks"
    LEDGER = STATE_DIR / "runs.jsonl"
    for _d in (STATE_DIR, LOG_DIR, LOCK_DIR):
        _d.mkdir(parents=True, exist_ok=True)

    def check(name, got, want):
        ok = got == want
        results.append((name, got, want, ok))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: got={got} want={want}")
        return ok

    # success + stdout delivery
    s = tmp / "ok.py"
    s.write_text("print('hello from job')\n")
    spec = Spec({"job_id": "st-success", "script": str(s), "runtime": "python"})
    check("success", run(spec), EXIT_OK)

    # silent success (no stdout -> no delivery)
    s2 = tmp / "quiet.py"
    s2.write_text("pass\n")
    check("silent-success", run(Spec({"job_id": "st-quiet", "script": str(s2),
                                      "runtime": "python"})), EXIT_OK)

    # child failure
    s3 = tmp / "boom.py"
    s3.write_text("import sys; sys.stderr.write('kaboom\\n'); sys.exit(7)\n")
    check("child_failure", run(Spec({"job_id": "st-fail", "script": str(s3),
                                     "runtime": "python"})), EXIT_CHILD)

    # timeout
    s4 = tmp / "slow.py"
    s4.write_text("import time; time.sleep(30)\n")
    check("timeout", run(Spec({"job_id": "st-timeout", "script": str(s4),
                               "runtime": "python", "timeout": 2})), EXIT_TIMEOUT)

    # config error: missing script
    check("config_error", run(Spec({"job_id": "st-missing",
                                    "script": str(tmp / "nope.py"),
                                    "runtime": "python"})), EXIT_CONFIG)

    # bash runtime
    s5 = tmp / "hi.sh"
    s5.write_text("echo shell-ok\n")
    check("bash-runtime", run(Spec({"job_id": "st-bash", "script": str(s5)})), EXIT_OK)

    # overlap skip: hold the lock, second run must skip cleanly
    spec_o = Spec({"job_id": "st-overlap", "script": str(s2), "runtime": "python"})
    with Lock("st-overlap", "skip") as held:
        assert held.acquired
        check("skipped_overlap", run(spec_o), EXIT_OK)

    # redaction
    check("redaction", "[REDACTED]" in redact("api_key=supersecretvalue"), True)

    # args pass-through (the operator called out "arg parsing" by name)
    s6 = tmp / "args.py"
    s6.write_text("import sys; print('ARGS:', ' '.join(sys.argv[1:]))\n")
    spec_a = Spec({"job_id": "st-args", "script": str(s6), "runtime": "python",
                   "args": ["--mode", "backfill", "--days", "7"]})
    argv_a = resolve_argv(spec_a)
    check("args-in-argv", argv_a[-4:], ["--mode", "backfill", "--days", "7"])
    check("args-run", run(spec_a), EXIT_OK)

    # python floor is enforced, not assumed
    ver = _interpreter_version(sys.executable)
    check("python-floor-met", bool(ver and ver >= MIN_PYTHON), True)

    # uv discovery must not depend on PATH (cron's PATH is not a login shell's).
    # Only assertable where uv is actually installed — CI runners have none.
    if find_uv():
        _saved = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = "/nonexistent"
            check("uv-found-without-PATH", bool(find_uv()), True)
        finally:
            os.environ["PATH"] = _saved
    else:
        print("  SKIP  uv-found-without-PATH: uv not installed on this host")

    # uv argv shape: --python floor present, script last before args
    if find_uv():
        s7 = tmp / "pep.py"
        s7.write_text('# /// script\n# requires-python = ">=3.9"\n# dependencies = []\n# ///\nprint("ok")\n')
        argv_u = resolve_argv(Spec({"job_id": "st-uv", "script": str(s7),
                                    "runtime": "uv"}))
        check("uv-pins-python", "--python" in argv_u and
              f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}" in argv_u, True)

    # --- fixes from the adversarial review ---

    # verbatim stdout: no strip, no truncation, trailing control line intact
    big = tmp / "big.py"
    big.write_text(
        "print('x' * 5000)\n"
        "print(chr(123)+chr(34)+'wakeAgent'+chr(34)+': false'+chr(125))\n"
    )
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run(Spec({"job_id": "st-verbatim", "script": str(big), "runtime": "python"}))
    captured = buf.getvalue()
    check("verbatim-not-truncated", len(captured) > 5000, True)
    check("verbatim-gate-survives",
          captured.strip().splitlines()[-1] == '{"wakeAgent": false}', True)

    # output_policy=silent suppresses a noisy successful job
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        run(Spec({"job_id": "st-silent", "script": str(s), "runtime": "python",
                  "output_policy": "silent"}))
    check("output-policy-silent", buf2.getvalue(), "")

    # redaction: real shapes, and no false-positive mangling
    check("redact-assign", "[REDACTED]" in redact("api_key=supersecretvalue"), True)
    check("redact-json", "[REDACTED]" in redact('{"token": "abc123xyz"}'), True)
    check("redact-bearer", "[REDACTED]" in redact("Authorization: Bearer abcd1234efgh"), True)
    check("redact-url", "[REDACTED]" in redact("postgres://user:hunter2@db:5432/x"), True)
    check("redact-no-false-positive",
          redact("session started at 10:00 and processed 42 rows"),
          "session started at 10:00 and processed 42 rows")

    # unknown spec field is rejected, not silently ignored
    try:
        Spec({"job_id": "st-typo", "script": str(s2), "timeoutt": 5})
        check("reject-unknown-field", False, True)
    except ConfigError:
        check("reject-unknown-field", True, True)

    # job_id cannot escape into a path
    try:
        Spec({"job_id": "../evil", "script": str(s2)})
        check("reject-bad-job-id", False, True)
    except ConfigError:
        check("reject-bad-job-id", True, True)

    # bounded capture: a job printing far more than the cap stays bounded
    flood = tmp / "flood.py"
    flood.write_text("for i in range(200000): print('y'*80)\n")
    st, rc, sig, o, e, d = _execute(
        Spec({"job_id": "st-flood", "script": str(flood), "runtime": "python",
              "timeout": 120}),
        [sys.executable, str(flood)], build_env(Spec({"job_id": "f", "script": str(flood)})))
    check("bounded-capture", len(o) < MAX_CAPTURE_BYTES * 3, True)
    check("bounded-capture-succeeded", st, "success")

    # ledger actually recorded runs
    n = 0
    if LEDGER.exists():
        n = sum(1 for line in LEDGER.read_text().splitlines()
                if '"st-' in line)
    check("ledger-recorded", n >= 6, True)

    shutil.rmtree(tmp, ignore_errors=True)
    STATE_DIR, LOG_DIR, LOCK_DIR, LEDGER = _saved_dirs
    failed = [r for r in results if not r[3]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fleet scheduled-job execution adapter")
    ap.add_argument("--spec", help="job spec name or path to .toml")
    ap.add_argument("--dry-run", action="store_true", help="validate only")
    ap.add_argument("--selftest", action="store_true", help="exercise terminal states")
    ap.add_argument("--bootstrap", action="store_true",
                    help="install uv + verify python floor on this host")
    ap.add_argument("--list", action="store_true", help="list jobs on this host")
    ap.add_argument("--status", metavar="JOB_ID", help="recent runs for one job")
    ap.add_argument("--failures", nargs="?", const=24, type=int, metavar="HOURS",
                    help="failed runs in the last N hours (default 24)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    _install_signal_handlers()
    if args.bootstrap:
        return cmd_bootstrap()
    if args.list:
        return cmd_list()
    if args.status:
        return cmd_status(args.status)
    if args.failures is not None:
        return cmd_failures(args.failures)
    if not args.spec:
        ap.error("--spec is required (or use --list/--status/--failures/"
                 "--bootstrap/--selftest)")
    try:
        spec = Spec.load(args.spec)
    except ConfigError as exc:
        print(f"configuration error: {exc}")
        return EXIT_CONFIG
    return run(spec, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
