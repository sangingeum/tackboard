"""The published JSON Schema documents for the CLI's ``--json`` output.

The schemas are the machine-readable form of the CLI contract (DESIGN.md
section 7): one draft 2020-12 schema per document shape, plus ``task.json``,
which holds the shared task projections every other schema references.

They ship inside the package (``src/tackboard/schemas``), so ``tack schema``
reads them from an installed wheel exactly as it does from a source checkout.
The repository root carries a ``schemas`` symlink to that directory, which is
where the published contract is documented.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import UsageError

#: Directory holding the schema files, inside the installed package.
SCHEMA_DIR = Path(__file__).with_name("schemas")

#: The shared definitions document. It describes no command output on its own:
#: every other schema references it, and it is published so a consumer can build
#: a resolver for those references.
SHARED_SCHEMA = "task"

#: Repository-relative directory, for documents and listings.
SCHEMA_PATH_ROOT = "schemas"


@dataclass(frozen=True, slots=True)
class SchemaEntry:
    """One published schema: its document name, stable ``$id`` and path."""

    name: str
    identifier: str
    path: str


def _schema_files() -> list[Path]:
    return sorted(SCHEMA_DIR.glob("*.json"))


def document_names() -> tuple[str, ...]:
    """Every published schema ``tack schema`` can name, in stable order."""
    return tuple(path.stem for path in _schema_files())


def document_shapes() -> tuple[str, ...]:
    """The document shapes: the published schemas minus the shared definitions."""
    return tuple(name for name in document_names() if name != SHARED_SCHEMA)


def load_schema(name: str) -> dict[str, Any]:
    """Load one schema document by name, unchanged, or raise ``UsageError``."""
    if name not in document_names():
        allowed = ", ".join(document_names())
        raise UsageError(
            f"Unknown document {name!r}; available documents: {allowed}."
        )
    path = SCHEMA_DIR / f"{name}.json"
    return _read(path)


def schema_entries() -> tuple[SchemaEntry, ...]:
    """Every published schema, including the shared definitions document."""
    entries: list[SchemaEntry] = []
    for path in _schema_files():
        document = _read(path)
        entries.append(
            SchemaEntry(
                name=path.stem,
                identifier=str(document["$id"]),
                path=f"{SCHEMA_PATH_ROOT}/{path.name}",
            )
        )
    return tuple(entries)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "SCHEMA_DIR",
    "SCHEMA_PATH_ROOT",
    "SHARED_SCHEMA",
    "SchemaEntry",
    "document_names",
    "document_shapes",
    "load_schema",
    "schema_entries",
]
