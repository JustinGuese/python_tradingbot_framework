"""
Whole-tree import smoke test.

This is the safety net for the package-root migration (rootless `from utils.X`
-> package-qualified `from tradingbot.utils.X`). A rewrite of ~100 import lines
across 43 files has exactly one failure mode that no unit test covers: a module
that no longer imports. Nothing else in the suite imports most of the bots.

It is safe to import every module because every top-level bot module guards its
entry point behind `if __name__ == "__main__":` — importing one must not trade.
If that guard is ever dropped, this test starts placing orders, which is a
second reason to keep it: it fails loudly rather than silently.
"""

import importlib
import pkgutil

import pytest

import tradingbot

MODULES = sorted(m.name for m in pkgutil.walk_packages(tradingbot.__path__, "tradingbot."))


def test_module_discovery_found_the_tree():
    """Guard against the parametrisation silently collapsing to zero modules."""
    assert len(MODULES) > 40, f"only discovered {len(MODULES)} modules: {MODULES}"


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    """
    Every module in the package must import cleanly.

    Catches the imports that no grep for `utils`/`livetrade` will find — notably
    `adaptivemeanreversionbtcbot`, which imports its parent strategy as a bare
    sibling (`from adaptivemeanreversionbot import ...`) and is a live nightly
    bot.
    """
    importlib.import_module(module_name)


def test_importing_the_package_does_not_dual_load_a_flat_copy():
    import sys

    for module_name in MODULES:
        importlib.import_module(module_name)

    flat_copies = sorted(m for m in sys.modules if m.split(".")[0] in {"utils", "livetrade"})
    assert not flat_copies, f"{len(flat_copies)} modules loaded twice under the flat root: {flat_copies}"
