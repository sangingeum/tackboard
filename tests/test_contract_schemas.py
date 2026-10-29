"""The published JSON Schemas are the CLI's machine-readable contract.

These tests pin three things: ``tack schema`` emits the packaged files
unchanged, every document shape has exactly one schema, and real CLI output
validates against its schema. The schemas describe what the code already
emits — when a schema and the code disagree, the code wins and the schema is
wrong.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import cli, contracts  # noqa: E402

DEFAULT_ACTOR = "dev"

#: Every document shape the SKILL.md contract table names.
DOCUMENT_SHAPES = (
    "agents",
    "backup",
    "board",
    "claims",
    "deps",
    "doctor",
    "error",
    "graph",
    "history",
    "history_follow",
    "history_since",
    "init",
    "memo",
    "memo_diff",
    "memo_history",
    "memo_write",
    "migrate",
    "next",
    "next_claim",
    "note",
    "plan_apply",
    "show",
    "task_mutation",
)

RunCli = Callable[..., tuple[int, str, str]]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never inherit project selection or an actor from the developer's shell."""
    monkeypatch.delenv("TACKBOARD_DB", raising=False)
    monkeypatch.delenv("TACKBOARD_AGENT", raising=False)


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    """Invoke the CLI in process, returning (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(contracts.SCHEMA_DIR.glob("*.json"))
    }


def _registry(schemas: dict[str, dict[str, Any]]) -> Registry[Any]:
    return Registry().with_resources(
        [(document["$id"], Resource.from_contents(document)) for document in schemas.values()]
    )


def _assert_valid(name: str, document: dict[str, Any]) -> None:
    schemas = _schemas()
    validator = Draft202012Validator(schemas[name], registry=_registry(schemas))
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    assert not errors, "\n".join(
        f"{name}: {'/'.join(str(part) for part in error.path)}: {error.message}"
        for error in errors
    )


def _document(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    assert "\n" not in text, f"stdout carries more than one document: {stdout!r}"
    return json.loads(text)


# --------------------------------------------------------------------------- #
# the schema command
# --------------------------------------------------------------------------- #


def test_schema_listing_names_every_published_schema(run: RunCli) -> None:
    code, out, err = run("schema", "--json")

    assert code == 0, err
    listing = _document(out)
    assert listing["schema_version"] == 1
    entries = listing["schemas"]
    assert {entry["name"] for entry in entries} == set(DOCUMENT_SHAPES) | {"task"}
    identifiers = [entry["$id"] for entry in entries]
    assert len(identifiers) == len(set(identifiers))
    for entry in entries:
        assert entry["$id"] == f"urn:tackboard:schema:{entry['name']}"
        assert entry["path"] == f"schemas/{entry['name']}.json"
        assert (REPO_ROOT / entry["path"]).is_file()


def test_schema_document_is_emitted_unchanged(run: RunCli) -> None:
    code, out, err = run("schema", "show", "--json")

    assert code == 0, err
    published = json.loads((contracts.SCHEMA_DIR / "show.json").read_text(encoding="utf-8"))
    assert _document(out) == published
    assert published["schema_version"] == 1


def test_shared_definitions_document_is_published(run: RunCli) -> None:
    code, out, err = run("schema", "task", "--json")

    assert code == 0, err
    assert set(_document(out)["$defs"]) >= {"task", "node", "event", "note", "blocker"}


def test_unknown_document_is_a_usage_error_naming_the_allowed_set(run: RunCli) -> None:
    code, out, err = run("schema", "nope", "--json")

    assert code == 2, err
    document = _document(out)
    assert document["error"]["code"] == "usage"
    assert "nope" in document["error"]["message"]
    for name in ("show", "board", "task_mutation"):
        assert name in document["error"]["message"]
    assert err == ""


# --------------------------------------------------------------------------- #
# the schema files themselves
# --------------------------------------------------------------------------- #


def test_every_document_shape_has_exactly_one_schema() -> None:
    assert contracts.document_shapes() == DOCUMENT_SHAPES
    assert len(DOCUMENT_SHAPES) == len(set(DOCUMENT_SHAPES))


def test_schemas_are_strict_version_one_documents() -> None:
    for name, document in _schemas().items():
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema", name
        assert document["$id"] == f"urn:tackboard:schema:{name}"
        assert document["schema_version"] == 1
        if name == "task":
            # The shared definitions document describes no instance itself.
            assert set(document) >= {"$defs"}
            continue
        assert document["type"] == "object"
        assert document["additionalProperties"] is False, name
        assert document["properties"]["schema_version"] == {"const": 1}, name


def test_every_reference_resolves_to_a_published_schema() -> None:
    schemas = _schemas()
    registry = _registry(schemas)
    for name, document in schemas.items():
        for reference in _walk_refs(document):
            if reference.startswith("#"):
                continue
            uri, _, pointer = reference.partition("#")
            resource = registry.get_or_retrieve(uri).value
            target = resource.contents
            for part in [part for part in pointer.split("/") if part]:
                target = target[part]
            assert target is not None, f"{name}: unresolved {reference}"


def _walk_refs(node: Any) -> list[str]:
    """Every ``$ref`` string inside one schema document."""
    if isinstance(node, dict):
        found = [value for key, value in node.items() if key == "$ref"]
        for value in node.values():
            found.extend(_walk_refs(value))
        return [str(item) for item in found]
    if isinstance(node, list):
        return [reference for item in node for reference in _walk_refs(item)]
    return []


# --------------------------------------------------------------------------- #
# shape agreement: real output against the schema
# --------------------------------------------------------------------------- #


def _create(run: RunCli, db: Path, title: str, *extra: str) -> dict[str, Any]:
    code, out, err = run(
        "create", title, "--agent", DEFAULT_ACTOR, "--db", str(db), "--json", *extra
    )
    assert code == 0, err
    return _document(out)["task"]


def test_show_board_history_and_error_documents_match_their_schemas(
    run: RunCli, db_path: Path
) -> None:
    task = _create(run, db_path, "Contract task", "--description", "first")
    code, out, err = run(
        "note", task["id"], "a note", "--agent", DEFAULT_ACTOR, "--db", str(db_path), "--json"
    )
    assert code == 0, err

    code, out, err = run("show", task["id"], "--db", str(db_path), "--json")
    assert code == 0, err
    show = _document(out)
    _assert_valid("show", show)
    assert show["task"]["description"] == "first"
    assert show["task"]["claimed_by"] is None
    assert show["task"]["claim_age_seconds"] is None

    code, out, err = run("board", "--db", str(db_path), "--json")
    assert code == 0, err
    board = _document(out)
    _assert_valid("board", board)
    assert "description" not in board["tasks"][0]

    code, out, err = run("board", "--count", "--db", str(db_path), "--json")
    assert code == 0, err
    _assert_valid("board", _document(out))

    code, out, err = run("history", "--db", str(db_path), "--json")
    assert code == 0, err
    _assert_valid("history", _document(out))

    code, out, err = run("show", "T-999999", "--db", str(db_path), "--json")
    assert code == 3, err
    _assert_valid("error", _document(out))


def test_empty_next_claim_is_null_and_still_valid(run: RunCli, db_path: Path) -> None:
    code, out, err = run(
        "next", "--claim", "--agent", DEFAULT_ACTOR, "--db", str(db_path), "--json"
    )

    assert code == 0, err
    document = _document(out)
    assert document["task"] is None
    _assert_valid("next_claim", document)


def test_task_mutation_document_matches_its_schema(run: RunCli, db_path: Path) -> None:
    code, out, err = run(
        "create", "Mutation task", "--agent", DEFAULT_ACTOR, "--db", str(db_path), "--json"
    )

    assert code == 0, err
    _assert_valid("task_mutation", _document(out))


# --------------------------------------------------------------------------- #
# packaging and the validation script
# --------------------------------------------------------------------------- #


def test_core_declares_no_runtime_dependency() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == []


def test_schemas_ship_inside_the_package() -> None:
    assert contracts.SCHEMA_DIR.is_dir()
    assert contracts.SCHEMA_DIR.parent.name == "tackboard"
    assert (REPO_ROOT / "schemas").is_dir()


def test_validation_script_names_the_failing_document_and_schema(tmp_path: Path) -> None:
    schemas = tmp_path / "schemas"
    shutil.copytree(contracts.SCHEMA_DIR, schemas)
    broken = json.loads((schemas / "show.json").read_text(encoding="utf-8"))
    broken["required"] = [*broken["required"], "not_a_declared_field"]
    (schemas / "show.json").write_text(json.dumps(broken), encoding="utf-8")

    environment = {**os.environ, "PYTHONPATH": str(SRC)}
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_schemas.py"),
            "--tack",
            f"{sys.executable} -m tackboard",
            "--schemas",
            str(schemas),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout
    assert "FAIL  show document invalid against" in completed.stderr
    assert str(schemas / "show.json") in completed.stderr
    assert "not_a_declared_field" in completed.stderr
