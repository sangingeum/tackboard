"""Tests for the tiered guide: the core budget, and the topics behind it.

The guide is an agent's entry point, so it is a contract like the JSON
documents: the core must stay one page and byte-identical to the README
quickstart, every topic must print exactly its section of ``SKILL.md``, and the
generated module must never drift from those two documents.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"
for entry in (str(SRC), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import build_docs  # noqa: E402

from tackboard import cli, guide  # noqa: E402

#: The core is the always-loaded text. One page is the point; this is the
#: ceiling that stops it growing back into a manual.
CORE_BUDGET_BYTES = 6 * 1024

#: Topics the reference has to offer, independent of the order they print in.
REQUIRED_TOPICS = frozenset(
    {
        "commands",
        "documents",
        "plans",
        "refs",
        "reject",
        "memo",
        "force",
        "doctor",
        "web",
        "schemas",
    }
)

RunCli = Callable[..., tuple[int, str, str]]


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    """Invoke the CLI in process, returning (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def test_the_topic_inventory_lists_every_topic(run: RunCli) -> None:
    code, out, err = run("guide", "--topics")
    assert code == 0
    assert err == ""
    listed = [line.split()[0] for line in out.splitlines() if line.strip()]
    assert listed == list(guide.TOPIC_ORDER)
    assert set(listed) >= REQUIRED_TOPICS
    assert len(listed) == len(set(listed)), "a topic is listed twice"


def test_every_topic_prints_its_section(run: RunCli) -> None:
    for name in guide.TOPIC_ORDER:
        code, out, err = run("guide", "--topic", name)
        assert code == 0, f"{name}: {err}"
        assert err == ""
        body = guide.TOPICS[name]
        assert body.strip(), f"topic {name!r} is empty"
        assert out == body.rstrip("\n") + "\n"
        assert not out.startswith("\n"), f"topic {name!r} starts with a blank line"


def test_an_unknown_topic_is_a_usage_error_naming_the_set(run: RunCli) -> None:
    code, out, err = run("guide", "--topic", "no-such-topic")
    assert code == 2
    assert out == ""
    assert "Unknown guide topic" in err
    for name in guide.TOPIC_ORDER:
        assert name in err


def test_topics_and_topic_cannot_be_combined(run: RunCli) -> None:
    code, out, _ = run("guide", "--topics", "--topic", "commands")
    assert code == 2
    assert out == ""


def test_json_does_not_change_guide_output(run: RunCli) -> None:
    code, out, _ = run("guide", "--topic", "memo", "--json")
    assert code == 0
    assert out == guide.TOPICS["memo"].rstrip("\n") + "\n"


def test_the_core_is_the_readme_quickstart() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert cli.GUIDE_TEXT in readme
    assert cli.GUIDE_TEXT == guide.CORE_GUIDE


def test_the_core_stays_within_its_byte_budget() -> None:
    size = len(cli.GUIDE_TEXT.encode("utf-8"))
    assert size < CORE_BUDGET_BYTES, (
        f"the always-loaded guide is {size} bytes, over the {CORE_BUDGET_BYTES}-byte "
        "budget; move the new material into a topic"
    )
    lines = cli.GUIDE_TEXT.count("\n")
    assert lines < 100, f"the core is {lines} lines; it is meant to stay about one page"


def test_the_generated_module_matches_the_documents() -> None:
    """guide.py is generated from docs/guide; it must be current."""
    expected = build_docs.render()
    actual = (SRC / "tackboard" / "guide.py").read_text(encoding="utf-8")
    assert actual == expected, "run `uv run python scripts/build_docs.py`"


def test_skill_documents_every_topic() -> None:
    """Every topic is reachable from the SKILL.md first screen."""
    skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for name in guide.TOPIC_ORDER:
        assert f"--topic {name}" in skill, f"SKILL.md does not point at {name!r}"
