"""Pytest configuration shared by the whole repo.

Two jobs:

1. **Make the repo importable from a bare clone.** Tests import both
   ``plugins.memory.cortex`` (package form) and the plugin's own modules
   (``from store import CortexStore``), so the repo root goes on ``sys.path``.

2. **Isolate tests from the developer's environment.** Cortex reads
   ``CORTEX_*`` env vars for its embedding and rerank endpoints, and Hermes
   reads ``HERMES_HOME``. If a contributor happens to have those exported —
   which anyone actually *running* a Hermes agent will — tests that assert the
   "unconfigured" default would fail against their live infrastructure, and
   worse, could issue real network calls. We strip them for every test session.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Env vars that would leak a developer's live Hermes/Cortex config into tests.
ISOLATED_ENV_VARS = (
    "CORTEX_EMBED_URL",
    "CORTEX_EMBED_MODEL",
    "CORTEX_EMBED_KEY",
    "CORTEX_EMBED_DIM",
    "CORTEX_RERANK_URL",
    "CORTEX_RERANK_MODEL",
    "CORTEX_RERANK_KEY",
    "HERMES_HOME",
)


@pytest.fixture(autouse=True)
def isolate_hermes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient Hermes/Cortex configuration for the duration of a test."""
    for var in ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
