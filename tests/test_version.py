"""The release number has one hand-written copy, and every surface agrees with it.

At 0.1.1 the version was bumped in ``pyproject.toml`` and ``SKILL.md`` but
missed in ``src/tackboard/__init__.py``, which was hand-maintained, so the
release reported itself as 0.1.0. ``__version__`` now reads the installed
distribution metadata instead; these tests fail if a hand-written copy, the
packaged metadata, or the version the CLI reports drifts from ``pyproject.toml``.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest

from tackboard import __version__, cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def _declared_version() -> str:
    """The single hand-written release number: ``pyproject.toml``."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _skill_version() -> str:
    """The ``version:`` field of SKILL.md's YAML frontmatter."""
    text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md carries no frontmatter block"
    frontmatter = text.split("---\n", 2)[1]
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "version":
            return value.strip()
    raise AssertionError("SKILL.md frontmatter carries no version field")


def test_every_copy_of_the_release_number_agrees() -> None:
    """pyproject.toml is the only hand-written copy; the installed metadata and
    the skill frontmatter must equal it, so a bump cannot leave one file
    behind as the 0.1.1 release did."""
    declared = _declared_version()
    assert __version__ == declared
    assert distribution_version("tackboard") == declared
    assert _skill_version() == declared


def test_the_cli_reports_the_declared_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``tack --version`` prints the version the distribution declares."""
    code = cli.main(["--version"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == f"tack (tackboard) {_declared_version()}"
