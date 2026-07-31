"""Shared loader for importing the cortex plugin inside tests.

Why this exists: Hermes itself ships a top-level ``plugins`` package. Inside a
real Hermes install that package **shadows** this repo's ``plugins/`` namespace
directory, so ``import plugins.memory.cortex`` resolves to the wrong thing and
fails with ``ModuleNotFoundError``. Path order can't fix it — a real package
beats a namespace package regardless of ``sys.path`` order.

So we load the plugin from its file path, which is exactly how Hermes loads
plugins at runtime. Works identically with or without Hermes installed.

Most cortex tests import the leaf modules directly (``from store import
CortexStore``) and don't need this. Use ``load_cortex_plugin()`` only when the
test exercises the package ``__init__`` — i.e. the ``CortexMemoryProvider``
tool-call surface.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = "cortex_plugin_under_test"


def load_cortex_plugin() -> ModuleType:
    """Import the cortex plugin package from disk and return the module.

    Cached in ``sys.modules`` so repeated calls across a test session return the
    same module object rather than re-executing the package.
    """
    cached = sys.modules.get(MODULE_NAME)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {PLUGIN_DIR}")

    module = importlib.util.module_from_spec(spec)
    # Register before exec so intra-package relative imports resolve.
    sys.modules[MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(MODULE_NAME, None)
        raise
    return module
