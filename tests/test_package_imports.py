"""Every module in the package tree must import cleanly with no side effects.

This proves the complete runtime tree remains import-safe. CLAUDE.md 5.4 forbids
network calls in constructors and scattered environment reads, and the first
violation of either usually appears as an import that needs credentials.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import gable

MODULE_NAMES: list[str] = [
    name for _, name, _ in pkgutil.walk_packages(gable.__path__, prefix="gable.")
]


def test_package_tree_is_complete() -> None:
    """The layout in CLAUDE.md section 6 exists in full."""
    expected = {
        "gable.config",
        "gable.logging_setup",
        "gable.runtime",
        "gable.cli",
        "gable.sheets.client",
        "gable.sheets.repository",
        "gable.photos.enhance",
        "gable.photos.store",
        "gable.slackapp.app",
        "gable.slackapp.runtime",
        "gable.pipeline.orchestrator",
    }
    assert expected <= set(MODULE_NAMES), f"missing: {sorted(expected - set(MODULE_NAMES))}"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_module_imports(module_name: str) -> None:
    """Importing a module must not require credentials, a network, or a file."""
    importlib.import_module(module_name)
