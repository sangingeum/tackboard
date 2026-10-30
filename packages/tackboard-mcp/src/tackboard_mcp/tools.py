"""The tool layer: one function per board operation, each a ``tack`` call.

There is deliberately no domain logic here. Every tool shells out to the CLI
with ``--json`` and returns that JSON document unchanged, so the adapter can
never disagree with the board: the CLI is the only implementation of claims,
transitions, validation and exit codes. A non-zero exit becomes a
``TackCommandError`` carrying the CLI's own error document, so an MCP client
sees exactly what an agent at a terminal would see.

The runner is injectable (``runner=``) so tests can exercise argument building
without a board; production always spawns the real executable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

#: A runner takes the full argv and stdin text and returns
#: ``(returncode, stdout, stderr)``. The default spawns a process.
Runner = Callable[[Sequence[str], str | None], tuple[int, str, str]]

#: A ``tack --json`` document.
Document = dict[str, Any]


class TackCommandError(RuntimeError):
    """A ``tack`` call failed; carries the CLI's error document when it sent one."""

    def __init__(
        self,
        argv: Sequence[str],
        returncode: int,
        message: str,
        document: Document | None = None,
    ) -> None:
        super().__init__(f"tack exited {returncode}: {' '.join(argv)}")
        self.argv = tuple(argv)
        self.returncode = returncode
        self.stderr = message
        self.document = document


def _subprocess_runner(argv: Sequence[str], stdin: str | None) -> tuple[int, str, str]:
    completed = subprocess.run(
        list(argv), input=stdin, capture_output=True, text=True, check=False
    )
    return completed.returncode, completed.stdout, completed.stderr


def _parse(argv: Sequence[str], returncode: int, text: str) -> Document:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TackCommandError(argv, returncode, f"stdout is not JSON: {text[:200]!r}") from exc
    if not isinstance(document, dict):
        raise TackCommandError(
            argv, returncode, f"stdout is not a JSON object: {text[:200]!r}"
        )
    return document


@contextmanager
def _environment(actor: str | None) -> Iterator[None]:
    """Set ``TACKBOARD_AGENT`` for one call and restore the process state after."""
    saved = os.environ.get("TACKBOARD_AGENT")
    if actor is None:
        os.environ.pop("TACKBOARD_AGENT", None)
    else:
        os.environ["TACKBOARD_AGENT"] = actor
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TACKBOARD_AGENT", None)
        else:
            os.environ["TACKBOARD_AGENT"] = saved


def _default_command() -> tuple[str, ...]:
    """Prefer the installed core module, falling back to the console script.

    The adapter depends on ``tackboard``, so the core is importable from the
    same environment in the normal case: running ``sys.executable -m tackboard``
    avoids depending on ``tack`` being on ``PATH``. When the adapter lives in a
    different environment from the CLI, ``tackboard`` is not importable and the
    historical ``tack`` lookup remains the fallback. ``TACK_CMD`` still takes
    first precedence in ``from_environment``.
    """
    if find_spec("tackboard") is not None:
        return (sys.executable, "-m", "tackboard")
    return ("tack",)


@dataclass(frozen=True, slots=True)
class TackCli:
    """A configured ``tack`` invocation: command prefix, board and actor.

    ``command`` is the executable plus any fixed prefix, e.g. ``("tack",)`` or
    ``(sys.executable, "-m", "tackboard")``. ``db`` and ``actor`` become
    ``--db`` and ``TACKBOARD_AGENT`` on every call.
    """

    command: tuple[str, ...] = ("tack",)
    db: Path | None = None
    actor: str | None = None
    runner: Runner = _subprocess_runner

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, runner: Runner | None = None
    ) -> TackCli:
        """Build from ``TACK_CMD``, ``TACKBOARD_DB`` and ``TACKBOARD_AGENT``."""
        env = os.environ if environ is None else environ
        command = tuple(env.get("TACK_CMD", "").split()) or _default_command()
        db_text = env.get("TACKBOARD_DB")
        return cls(
            command=command,
            db=Path(db_text) if db_text else None,
            actor=env.get("TACKBOARD_AGENT"),
            runner=runner or _subprocess_runner,
        )

    def _argv(self, *argv: str) -> list[str]:
        taken = [*self.command, *argv, "--json"]
        if self.db is not None:
            taken.extend(["--db", str(self.db)])
        return taken

    def run(self, *argv: str, stdin: str | None = None) -> Document:
        """Run one ``tack`` command and return its JSON document unchanged."""
        full = self._argv(*argv)
        with _environment(self.actor):
            returncode, stdout, stderr = self.runner(full, stdin)
        text = stdout.strip()
        if returncode != 0:
            document = None
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                document = parsed if isinstance(parsed, dict) else None
            raise TackCommandError(full, returncode, stderr.strip() or text, document)
        if not text:
            raise TackCommandError(full, returncode, "tack printed no JSON document")
        return _parse(full, returncode, text)

    # -- the board operations ------------------------------------------------

    def next_claim(self, stage: str | None = None) -> Document:
        """Claim and return the next eligible task (``task`` may be ``null``)."""
        argv: list[str] = ["next", "--claim"]
        if stage is not None:
            argv.extend(["--stage", stage])
        return self.run(*argv)

    def show(self, task_id: str) -> Document:
        """One task in full, with its notes."""
        return self.run("show", task_id)

    def note(self, task_id: str, text: str, refs: Sequence[str] = ()) -> Document:
        """Append a handoff note; ``refs`` are ``KIND:VALUE`` strings."""
        argv = ["note", task_id, text]
        for ref in refs:
            argv.extend(["--ref", ref])
        return self.run(*argv)

    def complete(self, task_id: str) -> Document:
        """Finish a task; its dependents become eligible."""
        return self.run("complete", task_id)

    def release(self, task_id: str, reason: str | None = None) -> Document:
        """Release a claim; a reason forces (audited) rather than plain release."""
        argv = ["release", task_id]
        if reason is not None:
            argv.extend(["--force", "--reason", reason])
        return self.run(*argv)

    def create(
        self,
        title: str,
        *,
        description: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        labels: Sequence[str] = (),
        depends_on: Sequence[str] = (),
        status: str | None = None,
    ) -> Document:
        """Create a task, optionally with its dependency edges."""
        argv = ["create", title]
        if description is not None:
            argv.extend(["--description", description])
        if priority is not None:
            argv.extend(["--priority", priority])
        if assignee is not None:
            argv.extend(["--assignee", assignee])
        if status is not None:
            argv.extend(["--status", status])
        for label in labels:
            argv.extend(["--label", label])
        for prerequisite in depends_on:
            argv.extend(["--depends-on", prerequisite])
        return self.run(*argv)

    def board_brief(self) -> Document:
        """The counts-only board document: the cheapest look at a board."""
        return self.run("board", "--brief")


__all__ = ["Document", "Runner", "TackCli", "TackCommandError"]
