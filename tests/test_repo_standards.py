"""Executable versions of the rules in CLAUDE.md that are cheap to check.

These are not placeholder tests. Each one guards a rule that is easy to break
silently and expensive to discover late:

* `.env` must be ignored before commit #1 (CLAUDE.md section 3).
* No file may exceed 800 lines (CLAUDE.md section 5.1).
* Nothing outside `gable.slackapp` may import from it, because that is what
  keeps the pipeline runnable with Slack absent (CLAUDE.md section 6).

They run in milliseconds and fail the moment a rule is violated, which is the
point — a standard nobody checks is a suggestion.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SRC_ROOT: Path = REPO_ROOT / "src" / "gable"

#: CLAUDE.md 5.1. 300-500 is the target and 800 is the hard ceiling; only the
#: ceiling is machine-checkable without arguing about docstring density.
MAX_FILE_LINES: int = 800

#: Directories that are not ours to police.
EXCLUDED_DIRS: frozenset[str] = frozenset({".venv", ".git", ".idea", "__pycache__", "build"})


def _repo_files(suffixes: tuple[str, ...]) -> list[Path]:
    """Collect repository files with the given suffixes, skipping vendored trees."""
    return [
        path
        for path in REPO_ROOT.rglob("*")
        if path.suffix in suffixes
        and path.is_file()
        and not any(part in EXCLUDED_DIRS for part in path.parts)
    ]


def test_env_is_gitignored() -> None:
    """`.env` must be ignored, and `.env.example` must not be."""
    patterns = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert ".env" in patterns
    assert "!.env.example" in patterns


def test_no_real_env_file_present() -> None:
    """A committed `.env` is the failure this whole rule exists to prevent."""
    assert not (REPO_ROOT / ".env").exists() or ".env" in (REPO_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_no_service_account_key_in_tree() -> None:
    """Service-account JSON lives outside the repo (ARCHITECTURE.md section 7)."""
    keys = [p for p in _repo_files((".json",)) if "service" in p.name and "account" in p.name]
    assert keys == [], f"service-account key inside the repo tree: {keys}"


@pytest.mark.parametrize("path", _repo_files((".py", ".md")), ids=lambda p: str(p.name))
def test_file_under_line_ceiling(path: Path) -> None:
    """No file exceeds the 800-line hard ceiling."""
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= MAX_FILE_LINES, f"{path} is {lines} lines (ceiling {MAX_FILE_LINES})"


def _imported_modules(path: Path) -> set[str]:
    """Return every module name imported by a Python file, via AST not regex."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_pipeline_does_not_import_slack() -> None:
    """Only `gable.slackapp` may import `gable.slackapp` — CLAUDE.md section 6.

    This is what makes `cli.py` able to run the whole pipeline without a live
    Slack workspace, which is how Phase 1 gets developed at all.
    """
    banned = ("gable.slackapp", "slack_bolt", "slack_sdk")
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if "slackapp" in path.parts:
            continue
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)} imports {module}"
            for module in _imported_modules(path)
            if module.startswith(banned)
        )
    assert offenders == [], "\n".join(offenders)


def test_every_source_module_has_a_docstring() -> None:
    """CLAUDE.md 5.3: a module docstring on every file, saying what it assumes."""
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC_ROOT.rglob("*.py")
        if ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) is None
    ]
    assert missing == [], f"modules without a docstring: {missing}"
