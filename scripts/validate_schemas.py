#!/usr/bin/env python3
"""Validate real ``tack`` output against the published JSON Schemas.

Every document shape in the contract is produced against a scratch board, then
validated with ``jsonschema`` against the schema of the same name. The CLI
documents are read from ``tack --json``; the HTTP API documents are read from a
real ``tack serve`` process over loopback, so the route itself is what is
validated. The schemas are the source of truth: a mismatch here is either a
schema bug or a contract change, and this script never fixes either.

This is a development/CI tool, not part of the package: ``jsonschema`` is a dev
dependency and the core package stays standard-library-only.

Usage:
    python scripts/validate_schemas.py                # uses `tack` from PATH
    python scripts/validate_schemas.py --tack "uv run tack"
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "src" / "tackboard" / "schemas"

#: Documents produced through one narrative, in the order they are produced.
#: ``board`` appears twice: with and without ``tasks``.
EXPECTED_DOCUMENTS: tuple[str, ...] = (
    "init",
    "task_mutation",
    "note",
    "show",
    "next",
    "next_claim",
    "board",
    "deps",
    "graph",
    "api_graph",
    "api_task",
    "history",
    "history_since",
    "history_follow",
    "claims",
    "agents",
    "memo",
    "memo_history",
    "memo_diff",
    "memo_write",
    "doctor",
    "plan_apply",
    "migrate",
    "backup",
    "error",
)


class ValidationFailure(Exception):
    """One document did not match its schema."""


def load_schemas(directory: Path) -> dict[str, dict[str, Any]]:
    """Read every schema file, keyed by document name."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    }


def build_registry(schemas: dict[str, dict[str, Any]]) -> Registry[Any]:
    """A registry keyed by the schemas' own ``$id`` URNs, so ``$ref`` resolves."""
    resources = [
        (document["$id"], Resource.from_contents(document))
        for document in schemas.values()
    ]
    return Registry().with_resources(resources)


class Harness:
    """Runs one ``tack`` command line against a scratch board."""

    def __init__(self, command: Sequence[str], database: Path) -> None:
        self.command = list(command)
        self.database = database
        self.environment = {**os.environ, "TACKBOARD_AGENT": "validate"}

    def argv(self, *args: str) -> list[str]:
        return [*self.command, "--db", str(self.database), *args]

    def run(self, *args: str, expect_ok: bool = True) -> str:
        completed = subprocess.run(
            self.argv(*args),
            capture_output=True,
            text=True,
            env=self.environment,
            check=False,
        )
        if expect_ok and completed.returncode != 0:
            raise SystemExit(
                f"`tack {' '.join(args)}` failed with exit "
                f"{completed.returncode}:\n{completed.stdout}{completed.stderr}"
            )
        return completed.stdout

    def document(self, *args: str, expect_ok: bool = True) -> dict[str, Any]:
        return json.loads(self.run(*args, expect_ok=expect_ok))

    def follow(self, *args: str, lines: int = 2) -> list[dict[str, Any]]:
        """Read the first ``lines`` NDJSON records of ``history --follow``."""
        process = subprocess.Popen(
            self.argv("history", "--follow", *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment,
        )
        records: list[dict[str, Any]] = []
        try:
            assert process.stdout is not None
            while len(records) < lines:
                line = process.stdout.readline()
                if not line:
                    break
                records.append(json.loads(line))
        finally:
            process.terminate()
            process.wait(timeout=10)
        return records


class ServedApi:
    """A real ``tack serve`` process, read over loopback as a browser would."""

    def __init__(self, command: Sequence[str], database: Path) -> None:
        self.command = list(command)
        self.database = database
        self.environment = {**os.environ, "TACKBOARD_AGENT": "validate"}
        self.process: subprocess.Popen[str] | None = None
        self.port = 0

    def __enter__(self) -> ServedApi:
        self.process = subprocess.Popen(
            [*self.command, "--db", str(self.database), "serve", "--port", "0", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment,
        )
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"`tack serve` printed no startup record: {line!r}") from exc
        if record.get("event") != "serve.start":
            raise SystemExit(f"`tack serve` printed no startup record: {line!r}")
        self.port = int(record["port"])
        return self

    def document(self, path: str) -> dict[str, Any]:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def __exit__(self, *exc_info: object) -> None:
        if self.process is None:  # pragma: no cover - defensive
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        finally:
            for stream in (self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()


def produce_documents(harness: Harness, workdir: Path) -> Iterable[tuple[str, Any]]:
    """Run the CLI and yield ``(document name, parsed document)`` pairs."""
    yield "init", harness.document("init", "--json")

    first = harness.document(
        "create",
        "Validation task",
        "--description",
        "the first task",
        "--priority",
        "high",
        "--label",
        "core",
        "--json",
    )
    yield "task_mutation", first
    task_id = first["task"]["id"]

    harness.run("create", "Second task", "--json")
    second_id = harness.document("board", "--json")["tasks"][1]["id"]

    yield "note", harness.document(
        "note",
        task_id,
        "a handoff note",
        "--ref",
        "commit:0123456",
        "--ref",
        "url:https://example.invalid/ref",
        "--json",
    )
    harness.run("status", task_id, "ready", "--json")

    yield "next", harness.document("next", "--json")
    yield "next_claim", harness.document("next", "--claim", "--json")
    yield "show", harness.document("show", task_id, "--json")
    yield "board", harness.document("board", "--json")
    yield "board", harness.document("board", "--count", "--json")

    harness.run("depend", "add", second_id, "--on", task_id, "--json")
    yield "deps", harness.document("deps", second_id, "--json")
    yield "graph", harness.document("graph", "--format", "json")

    # The HTTP documents come from the route itself, not from a re-derivation:
    # one served process answers both, against the same scratch board.
    with ServedApi(harness.command, harness.database) as api:
        yield "api_graph", api.document("/api/graph")
        yield "api_task", api.document(f"/api/tasks/{task_id}")
        yield "api_task", api.document(f"/api/tasks/{second_id}")

    yield "history", harness.document("history", task_id, "--json")
    yield "history_since", harness.document("history", "--since", "1", "--json")
    for record in harness.follow("--since", "0"):
        yield "history_follow", record

    yield "claims", harness.document("claims", "--json")
    yield "agents", harness.document("agents", "--json")

    yield "memo", harness.document("memo", "--json")
    harness.run(
        "memo", "the board memo", "--reason", "validation run", "--agent", "validate", "--json"
    )
    yield "memo_write", harness.document(
        "memo",
        "a second memo",
        "--reason",
        "validation run",
        "--if-rev",
        "1",
        "--agent",
        "validate",
        "--json",
    )
    yield "memo_write", harness.document(
        "memo",
        "a third memo",
        "--reason",
        "validation run",
        "--if-rev",
        "2",
        "--dry-run",
        "--agent",
        "validate",
        "--json",
    )
    yield "memo_history", harness.document("memo", "--history", "--json")
    yield "memo_diff", harness.document("memo", "--diff", "--json")

    harness.run("status", second_id, "ready", "--json")
    yield "doctor", harness.document("doctor", "--json")
    yield "migrate", harness.document("migrate", "--json")

    plan_path = workdir / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {"key": "one", "title": "Plan task one"},
                    {"key": "two", "title": "Plan task two", "depends_on": ["one"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    yield "plan_apply", harness.document("plan", "apply", str(plan_path), "--dry-run", "--json")

    backup_path = workdir / "backup.sqlite3"
    yield "backup", harness.document("backup", str(backup_path), "--json")

    yield "error", harness.document("show", "T-999999", "--json", expect_ok=False)


def validate_all(
    harness: Harness,
    workdir: Path,
    schemas: dict[str, dict[str, Any]],
    schema_dir: Path,
) -> int:
    """Validate every produced document; return the number of failures."""
    registry = build_registry(schemas)
    validators = {
        name: Draft202012Validator(document, registry=registry)
        for name, document in schemas.items()
    }
    seen: set[str] = set()
    failures: list[str] = []
    count = 0

    for name, document in produce_documents(harness, workdir):
        count += 1
        schema_path = schema_dir / f"{name}.json"
        validator = validators.get(name)
        if validator is None:
            failures.append(f"{name}: no schema at {schema_path}")
            continue
        seen.add(name)
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
        if errors:
            for error in errors:
                location = "/".join(str(part) for part in error.path) or "<root>"
                failures.append(
                    f"{name} document invalid against {schema_path}: "
                    f"{location}: {error.message}"
                )
        else:
            print(f"ok    {name:<15} {schema_path}")

    missing = sorted(set(EXPECTED_DOCUMENTS) - seen)
    for name in missing:
        failures.append(f"{name}: no document was produced (expected in the run)")

    # The listing command must name exactly the published documents.
    listing = json.loads(harness.run("schema", "--json"))
    listed = {entry["name"] for entry in listing["schemas"]}
    published = set(schemas)
    if listed != published:
        failures.append(
            "tack schema --json lists "
            f"{sorted(listed)} but the packaged schemas are {sorted(published)}"
        )

    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    print(f"\n{count} documents validated, {len(failures)} failure(s)")
    return len(failures)


def check_no_runtime_dependencies() -> list[str]:
    """The core package must declare no runtime dependency."""
    problems: list[str] = []
    pyproject = REPO_ROOT / "pyproject.toml"
    try:
        import tomllib

        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        dependencies = list(project.get("dependencies", []))
    except (OSError, KeyError, ValueError) as exc:
        return [f"cannot read {pyproject}: {exc}"]
    if dependencies:
        problems.append(f"core declares runtime dependencies: {dependencies}")

    try:
        from importlib.metadata import PackageNotFoundError, requires

        try:
            declared = requires("tackboard") or []
        except PackageNotFoundError:
            declared = []
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib
        declared = []
    for requirement in declared:
        if "extra ==" not in requirement:
            problems.append(f"installed metadata declares a runtime requirement: {requirement}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tack",
        default="tack",
        help="the tack command line to exercise (quoted, e.g. 'uv run tack')",
    )
    parser.add_argument(
        "--schemas",
        default=str(SCHEMA_DIR),
        help="directory of published schema files",
    )
    args = parser.parse_args(argv)

    schema_dir = Path(args.schemas)
    schemas = load_schemas(schema_dir)
    problems = check_no_runtime_dependencies()
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="tack-schemas-") as directory:
        workdir = Path(directory)
        harness = Harness(shlex.split(args.tack), workdir / "board.sqlite3")
        failures = validate_all(harness, workdir, schemas, schema_dir)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
