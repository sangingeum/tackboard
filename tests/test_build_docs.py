"""Tests for the inverted guide sources in ``docs/guide`` and the builder.

The sources are now the contract: ``manifest.toml`` names the core and the
ordered topics, the Markdown files hold the prose, and ``scripts/build_docs.py``
renders ``src/tackboard/guide.py`` from them byte-for-byte.  These tests keep
the sources, the manifest and the generated module in agreement.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for entry in (str(SRC), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import build_docs  # noqa: E402

from tackboard import guide  # noqa: E402

DOCS = REPO_ROOT / "docs" / "guide"
MANIFEST = DOCS / "manifest.toml"
GUIDE_PATH = SRC / "tackboard" / "guide.py"


def _manifest() -> dict:
    with MANIFEST.open("rb") as handle:
        return build_docs.tomllib.load(handle)


def test_manifest_lists_the_expected_topics_in_order() -> None:
    manifest = _manifest()
    names = [topic["name"] for topic in manifest["topic"]]
    assert names == list(guide.TOPIC_ORDER)
    assert len(names) == len(set(names)), "a topic is listed twice"
    for topic in manifest["topic"]:
        assert topic["title"] == guide.TOPIC_TITLES[topic["name"]]
        assert (DOCS / topic["source"]).is_file()


def test_core_source_matches_the_generated_module_and_readme() -> None:
    core = (DOCS / "core.md").read_text(encoding="utf-8").rstrip("\n")
    assert core == guide.CORE_GUIDE
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert core in readme


def test_topic_sources_match_the_generated_module() -> None:
    for topic in _manifest()["topic"]:
        name = topic["name"]
        body = build_docs._topic_body(DOCS, topic)
        assert body == guide.TOPICS[name]


def test_render_matches_the_checked_in_module() -> None:
    expected = build_docs.render()
    actual = GUIDE_PATH.read_text(encoding="utf-8")
    assert actual == expected, "run `uv run python scripts/build_docs.py`"


def test_check_reports_current(capsys: pytest.CaptureFixture[str]) -> None:
    assert build_docs.check() == 0
    assert "matches docs/guide" in capsys.readouterr().out


def test_check_detects_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "guide.py"
    stale.write_text("stale\n", encoding="utf-8")
    assert build_docs.check(target_path=stale, docs_dir=DOCS) == 1
    captured = capsys.readouterr()
    assert "stale:" in captured.err
    assert "run scripts/build_docs.py" in captured.err
    assert "stale" in captured.err


def test_render_rejects_a_triple_single_quote(tmp_path: Path) -> None:
    docs = tmp_path / "guide"
    shutil.copytree(DOCS, docs)
    (docs / "topics" / "commands.md").write_text("'''\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="triple single quote"):
        build_docs.render(docs_dir=docs)


def test_render_rejects_an_empty_manifest(tmp_path: Path) -> None:
    docs = tmp_path / "guide"
    shutil.copytree(DOCS, docs)
    (docs / "manifest.toml").write_text(
        '[core]\nsource = "core.md"\nbudget_bytes = 6144\nbudget_lines = 100\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="no topics"):
        build_docs.render(docs_dir=docs)


def test_sync_guide_wrapper_delegates_to_the_builder() -> None:
    import sync_guide

    assert sync_guide.build_text() == build_docs.render()
    assert sync_guide.main(["--check"]) == 0


def test_readme_regions_match_the_generator() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert build_docs.render_readme() == readme


def test_skill_regions_match_the_generator() -> None:
    skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert build_docs.render_skill() == skill


def test_check_all_reports_current(capsys: pytest.CaptureFixture[str]) -> None:
    assert build_docs.check_all() == 0
    captured = capsys.readouterr()
    assert "src/tackboard/guide.py" in captured.out
    assert "README.md" in captured.out
    assert "SKILL.md" in captured.out


def test_check_all_detects_a_mutated_region(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    readme = tmp_path / "README.md"
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    mutated = text.replace("tack init [--db PATH]", "tack init --db PATH", 1)
    assert mutated != text
    readme.write_text(mutated, encoding="utf-8")
    assert build_docs.check_all(readme_path=readme) == 1
    captured = capsys.readouterr()
    assert "stale: " + str(readme) in captured.err


def test_marker_integrity_failures_name_the_file(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    broken = text.replace("<!-- END GENERATED: commands -->\n", "", 1)
    readme.write_text(broken, encoding="utf-8")
    with pytest.raises(SystemExit, match=str(readme)):
        build_docs.render_readme(readme_path=readme)


def test_quickstart_region_is_the_core_guide() -> None:
    bodies = build_docs._readme_region_bodies(build_docs.DOCS_DIR)
    assert bodies["quickstart"] == "```text\n" + guide.CORE_GUIDE + "\n```"
