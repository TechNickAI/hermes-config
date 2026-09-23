"""Tests for the app-router funnel gate check in apply-tailscale-serve.sh.

A funnel'd Tailscale port is fully public. The config file has always carried a
comment saying only gated upstreams may live there, but a comment is enforced by
whoever happens to read it. These tests pin the behavior that the apply script
now refuses to compile a public route whose upstream is not declared gated.

The compile step is pure Python embedded in the shell script: it reads the
config and writes `tailscale ...` command lines to stdout, touching no network
and no tailnet. That lets these tests extract and run it directly.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "devops" / "app-router" / "scripts"
APPLY_SCRIPT = SCRIPTS / "apply-tailscale-serve.sh"
SERVE_CONFIG = SCRIPTS / "tailscale-serve.json"

HOST = "example-host.example.ts.net"


def _compiler_source() -> str:
    """The Python heredoc that compiles the JSON config into serve commands."""
    match = re.search(r"<<'PY'\n(.*?)\nPY\n", APPLY_SCRIPT.read_text(), re.DOTALL)
    assert match, "could not find the embedded Python compile block in the apply script"
    return match.group(1)


@pytest.fixture(scope="module")
def compiler(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("compiler") / "compile.py"
    path.write_text(_compiler_source())
    return path


def run_compiler(
    compiler: Path, config_text: str, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "tailscale-serve.json"
    config.write_text(config_text)
    return subprocess.run(
        [sys.executable, str(compiler), str(config), HOST],
        capture_output=True,
        text=True,
        check=False,
    )


def config_with(handler: dict, funnel: bool = True) -> str:
    return json.dumps(
        {
            "TCP": {"443": {"HTTPS": True}},
            "Web": {"${HOST}:443": {"Handlers": {"/": handler}}},
            "AllowFunnel": {"${HOST}:443": funnel},
        }
    )


PROXY = "http://127.0.0.1:8080"


def test_shipped_config_still_compiles(compiler: Path, tmp_path: Path) -> None:
    """The config this repo ships must pass its own guard."""
    result = run_compiler(compiler, SERVE_CONFIG.read_text(), tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"tailscale funnel --bg --https=443 {PROXY}" in result.stdout


@pytest.mark.parametrize("gate", ["password", "token"])
def test_gated_funnel_handler_is_allowed(
    compiler: Path, tmp_path: Path, gate: str
) -> None:
    result = run_compiler(
        compiler, config_with({"Proxy": PROXY, "Gate": gate}), tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert "tailscale funnel" in result.stdout


def test_passwordless_funnel_handler_is_refused(
    compiler: Path, tmp_path: Path
) -> None:
    """The exact failure the security rule exists to prevent."""
    result = run_compiler(
        compiler, config_with({"Proxy": PROXY, "Gate": "none"}), tmp_path
    )
    assert result.returncode != 0
    assert "tailscale funnel" not in result.stdout
    assert "passwordless" in result.stderr.lower()


def test_missing_gate_on_funnel_handler_fails_closed(
    compiler: Path, tmp_path: Path
) -> None:
    """An unannotated handler is one nobody reasoned about. Refuse it."""
    result = run_compiler(compiler, config_with({"Proxy": PROXY}), tmp_path)
    assert result.returncode != 0
    assert "tailscale funnel" not in result.stdout
    assert "no Gate" in result.stderr


def test_unknown_gate_value_is_refused(compiler: Path, tmp_path: Path) -> None:
    result = run_compiler(
        compiler, config_with({"Proxy": PROXY, "Gate": "basic-auth"}), tmp_path
    )
    assert result.returncode != 0
    assert "unknown Gate" in result.stderr


def test_tailnet_only_handler_needs_no_gate(compiler: Path, tmp_path: Path) -> None:
    """Only public routes are gated. Tailnet-only routes are unaffected."""
    result = run_compiler(compiler, config_with({"Proxy": PROXY}, funnel=False), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "tailscale serve" in result.stdout
    assert "tailscale funnel" not in result.stdout


def test_guard_runs_before_any_command_is_emitted(
    compiler: Path, tmp_path: Path
) -> None:
    """A rejected config must produce no commands at all.

    The apply script preflights the compiled commands against live state before
    `serve reset`. Emitting a partial list for a rejected config would apply
    some routes anyway, so the guard has to reject before the first line.
    """
    config = json.dumps(
        {
            "TCP": {"443": {"HTTPS": True}},
            "Web": {
                "${HOST}:443": {
                    "Handlers": {
                        "/": {"Proxy": PROXY, "Gate": "password"},
                        "/admin/": {"Proxy": "http://127.0.0.1:9999", "Gate": "none"},
                    }
                }
            },
            "AllowFunnel": {"${HOST}:443": True},
        }
    )
    result = run_compiler(compiler, config, tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_every_funnelled_handler_in_shipped_config_declares_a_gate() -> None:
    """Belt and braces: read the shipped config directly, not via the compiler."""
    raw = re.sub(r"(^|\s)//[^\n]*", r"\1", SERVE_CONFIG.read_text())
    cfg = json.loads(raw)
    for site, enabled in cfg.get("AllowFunnel", {}).items():
        if not enabled:
            continue
        handlers = cfg["Web"][site]["Handlers"]
        assert handlers, f"funnel'd site {site} has no handlers"
        for path, handler in handlers.items():
            assert handler.get("Gate") in {"password", "token"}, (
                f"{site}{path} is publicly funnel'd but not declared gated"
            )
