"""Typed exceptions for the tackboard domain layer.

Spec: section 10.6 (exit codes), section 15 (error handling). The library never
formats tracebacks — that is the CLI's ``--verbose`` job.
"""

from __future__ import annotations

from typing import Any


class TackError(Exception):
    """Base class for every tackboard error.

    Subclasses carry a stable ``code`` (the machine-readable error code from
    spec section 10.5), an ``exit_code``, and a ``retryable`` flag.
    """

    code: str = "internal"
    exit_code: int = 1
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})
        if retryable is not None:
            self.retryable = retryable

    def to_json(self) -> dict[str, Any]:
        """Return the spec 10.5 error envelope, without ``schema_version``.

        The CLI adds ``schema_version`` when it serializes the envelope.
        """
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "exit_code": self.exit_code,
                "retryable": self.retryable,
                "details": self.details,
            }
        }

    def __str__(self) -> str:
        return self.message


class UsageError(TackError):
    """Invalid command line, option combination, or missing actor."""

    code = "usage"
    exit_code = 2


class NotFoundError(TackError):
    """Task, project, or database not found / not configured."""

    code = "not_found"
    exit_code = 3


class ValidationError(TackError):
    """A value or operation is not valid."""

    code = "validation"
    exit_code = 4


class InvalidTransitionError(TackError):
    """A status transition is not allowed by the transition table."""

    code = "invalid_transition"
    exit_code = 4


class CycleError(TackError):
    """A dependency edge would create a cycle."""

    code = "cycle"
    exit_code = 4


class ClaimConflictError(TackError):
    """A claim is held by another agent."""

    code = "claim_conflict"
    exit_code = 5


class StaleVersionError(TackError):
    """The ``--if-version`` guard did not match the stored version."""

    code = "stale_version"
    exit_code = 5


class StaleRevError(TackError):
    """The ``--if-rev`` guard did not match the memo's current revision.

    A snapshot conflict, exactly like ``stale_version``: exit 5 and **not**
    retryable — a blind retry would overwrite the other writer's revision.
    Only ``locked`` carries ``retryable: true``.
    """

    code = "stale_rev"
    exit_code = 5


class LockedError(TackError):
    """SQLite stayed locked after the bounded retry budget."""

    code = "locked"
    exit_code = 5
    retryable = True


class SchemaError(TackError):
    """The database is not a tackboard database, or its schema is unsupported."""

    code = "schema"
    exit_code = 6


class MemoTableMissingError(SchemaError):
    """A board claims schema version 2 but has no ``memo_revisions`` table.

    The same condition ``doctor`` reports as ``memo_table_missing``: a corrupt
    board, not a crash, so a memo read or write fails the way the documented
    exit-code table says a schema error fails (exit 6) instead of leaking a raw
    ``sqlite3.OperationalError`` as an internal error.
    """

    code = "memo_table_missing"


class InternalError(TackError):
    """Unexpected internal failure."""

    code = "internal"
    exit_code = 1


__all__ = [
    "ClaimConflictError",
    "CycleError",
    "InternalError",
    "InvalidTransitionError",
    "LockedError",
    "MemoTableMissingError",
    "NotFoundError",
    "SchemaError",
    "StaleRevError",
    "StaleVersionError",
    "TackError",
    "UsageError",
    "ValidationError",
]
