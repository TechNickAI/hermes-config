#!/usr/bin/env python3
"""
Run the standalone jobrun check suites under pytest.

Those three suites are script-style: they execute their assertions at import
and call sys.exit(1) on failure, which crashes pytest's collector if it imports
them directly. They live in tests/standalone/ and run here as subprocesses so
CI covers them without pytest trying to collect them as modules.

They are written that way on purpose — they double as executable diagnostics an
operator can run on a fleet host with nothing but a Python interpreter, no
pytest installed. Keeping that property is worth this small wrapper.
"""

import subprocess
import sys
from pathlib import Path

import pytest

STANDALONE = Path(__file__).resolve().parent / "standalone"

SUITES = [
    ("severity contract", "jobrun_severity_checks.py"),
    ("repair dispatcher", "jobrun_repair_checks.py"),
    ("wall-clock soak", "jobrun_soak_checks.py"),
]


@pytest.mark.parametrize("label,script", SUITES, ids=[s[1] for s in SUITES])
def test_standalone_suite(label, script):
    path = STANDALONE / script
    assert path.is_file(), f"missing suite: {path}"
    proc = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout or "").splitlines()[-40:])
        pytest.fail(f"{label} suite failed:\n{tail}\n{proc.stderr[-2000:]}")
    assert "passed" in proc.stdout, proc.stdout[-500:]
