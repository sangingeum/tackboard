"""Tests for scripts/check_links.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_links  # noqa: E402


def _repo(root: Path, readme: str, skill: str = "ok\n") -> None:
    (root / "README.md").write_text(readme, encoding="utf-8")
    (root / "SKILL.md").write_text(skill, encoding="utf-8")


def test_relative_links_resolve(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("x", encoding="utf-8")
    (tmp_path / "examples").mkdir()
    _repo(
        tmp_path,
        "[guide](docs/guide.md) [frag](docs/guide.md#section) [dir](examples/)\n"
        "[site](https://example.com) [mail](mailto:a@b.c) [anchor](#top)\n",
    )
    assert check_links.check(tmp_path) == []


def test_missing_target_is_reported(tmp_path: Path) -> None:
    _repo(tmp_path, "[gone](docs/missing.md)\n")
    problems = check_links.check(tmp_path)
    assert len(problems) == 1
    assert "docs/missing.md" in problems[0]
    assert "README.md" in problems[0]


def test_missing_directory_is_reported(tmp_path: Path) -> None:
    _repo(tmp_path, "[gone](no-such-dir/)\n")
    problems = check_links.check(tmp_path)
    assert len(problems) == 1
    assert "no-such-dir" in problems[0]


def test_title_and_angle_brackets_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    _repo(tmp_path, '[one](a.md "a title") [two](<a.md>)\n')
    assert check_links.check(tmp_path) == []


def test_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    _repo(tmp_path, "[ok](a.md)\n")
    assert check_links.main(["--root", str(tmp_path)]) == 0
    assert "all relative targets resolve" in capsys.readouterr().out

    _repo(tmp_path, "[bad](missing.md)\n")
    assert check_links.main(["--root", str(tmp_path)]) == 1
    assert "missing.md" in capsys.readouterr().err


def test_the_repository_links_resolve() -> None:
    """The checked-in documents must not carry a broken relative link."""
    assert check_links.check(REPO_ROOT) == []
