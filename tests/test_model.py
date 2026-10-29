"""A state-machine model test: the board and an in-memory model, one sequence.

The model is a plain dict of tasks plus a set of prerequisite edges. It applies
the documented transition table and claim policy itself; the board applies the
same command through ``apply_transition``. After every step the observable
projection — status, claim, edges, derived flags and counts — must agree, and a
command the board refuses must be refused by the model with the same kind of
error. The sequence deliberately exercises the audited ``--force`` paths:
``release --force``, ``claim --force`` and ``recover``.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tackboard import claims as claim_rules
from tackboard import dependencies, schema, service
from tackboard.errors import (
    ClaimConflictError,
    CycleError,
    InvalidTransitionError,
    ValidationError,
)
from tackboard.models import ALLOWED_TRANSITIONS, PREREQUISITE_GATED_STATUSES

ACTORS = ("a1", "a2")
COMMAND_KINDS = ("status", "claim", "release", "recover", "depend")
TERMINAL = {"done", "cancelled"}


class ModelError(Exception):
    """A refusal by the model, tagged with the board's error kind."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _error_kind(exc: Exception) -> str | None:
    if isinstance(exc, InvalidTransitionError):
        return "invalid_transition"
    if isinstance(exc, ClaimConflictError):
        return "claim_conflict"
    if isinstance(exc, CycleError):
        return "cycle"
    if isinstance(exc, ValidationError):
        return "validation"
    return None


def _board(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    path = tmp_path / "model.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    schema.init_database(path, label="model")
    return schema.open_database(path), path


# --------------------------------------------------------------------------- #
# the model
# --------------------------------------------------------------------------- #


def _force_for(
    kind: str, task: dict[str, str | None], actor: str, target: str | None
) -> tuple[bool, str | None]:
    """Choose the audited force path a command needs, if any.

    Only the enumerated cases are ever forced: recovering an unclaimed
    ``in_progress`` task, taking over or recovering a foreign claim, and
    ``recover`` itself. A forced start with unfinished prerequisites is never
    generated, so a clean sequence leaves no forced inconsistency behind.
    """
    status = str(task["status"])
    claim = task["claim"]
    if kind == "recover":
        return True, "recovering a dead leg"
    if kind == "status":
        if status == "in_progress" and claim is None and target != "in_progress":
            return True, "recovering an unclaimed in_progress task"
        return False, None
    if kind == "claim":
        if claim is not None or status == "in_progress":
            return True, "taking over or recovering a claim"
        return False, None
    if kind == "release":
        if claim is not None and claim != actor:
            return True, "releasing a claim held by another agent"
        if claim is None and status == "in_progress":
            return True, "recovering an unclaimed in_progress task"
        return False, None
    return False, None


def _model_step(
    model: dict[str, dict[str, str | None]],
    edges: dict[str, set[str]],
    *,
    kind: str,
    task_id: str,
    target: str | None,
    prerequisite: str | None,
    actor: str,
    force: bool,
) -> None:
    task = model[task_id]
    status = str(task["status"])
    claim = task["claim"]

    if kind == "status":
        assert target is not None
        if target == status:
            return
        if target not in ALLOWED_TRANSITIONS[status]:
            raise ModelError("invalid_transition")
        if target in PREREQUISITE_GATED_STATUSES:
            unfinished = [p for p in edges.get(task_id, ()) if model[p]["status"] != "done"]
            if unfinished:
                # Only an in_progress start is forceable, and this harness only
                # forces recovery paths, so any gated start refuses.
                raise ModelError("validation")
        leaving = status in {"in_progress", "review"} and target != "in_progress"
        if claim is not None and claim != actor and (leaving or target == "in_progress"):
            raise ModelError("claim_conflict")
        if leaving and claim is None and status == "in_progress" and not force:
            raise ModelError("validation")
        if force and not (leaving and claim is None and status == "in_progress"):
            raise ModelError("validation")
        task["status"] = target
        if target == "in_progress":
            task["claim"] = actor
        elif status in {"in_progress", "review"} and target != "in_progress":
            task["claim"] = None
        return

    if kind == "claim":
        if claim == actor:
            return
        used_force = False
        if claim is not None:
            if not force:
                raise ModelError("claim_conflict")
            used_force = True
        if status not in {"ready", "in_progress", "review"}:
            raise ModelError("invalid_transition")
        if status == "ready":
            eligible = all(model[p]["status"] == "done" for p in edges.get(task_id, ()))
            if not eligible:
                # Forced starts are not generated; a non-eligible ready task
                # refuses.
                raise ModelError("validation")
            new_status = "in_progress"
        elif status == "review":
            reviewable = all(model[p]["status"] == "done" for p in edges.get(task_id, ()))
            if not reviewable:
                raise ModelError("validation")
            new_status = "review"
        else:  # unclaimed in_progress: recovery only
            if not force:
                raise ModelError("validation")
            used_force = True
            new_status = "in_progress"
        if force and not used_force:
            raise ModelError("validation")
        task["status"] = new_status
        task["claim"] = actor
        return

    if kind == "release":
        if claim is None:
            if status == "in_progress":
                if not force:
                    raise ModelError("validation")
                task["status"] = "ready"
            return
        if claim != actor:
            if not force:
                raise ModelError("claim_conflict")
        elif force:
            raise ModelError("validation")
        task["claim"] = None
        if status == "in_progress":
            task["status"] = "ready"
        return

    if kind == "recover":
        if claim is None:
            if status == "in_progress":
                task["status"] = "ready"
            return
        if claim == actor:
            raise ModelError("validation")
        task["claim"] = None
        if status == "in_progress":
            task["status"] = "ready"
        return

    if kind == "depend":
        assert prerequisite is not None
        if task_id == prerequisite:
            raise ModelError("validation")
        if prerequisite in edges.get(task_id, set()):
            return
        if claim is not None and claim != actor:
            raise ModelError("claim_conflict")
        if status == "cancelled":
            raise ModelError("validation")
        if status == "done" and model[prerequisite]["status"] != "done":
            raise ModelError("validation")
        if status in {"in_progress", "review"} and model[prerequisite]["status"] != "done":
            raise ModelError("validation")
        # A new edge cycles when the new prerequisite can already reach the task.
        seen: set[str] = set()
        stack = [prerequisite]
        while stack:
            node = stack.pop()
            if node == task_id:
                raise ModelError("cycle")
            if node in seen:
                continue
            seen.add(node)
            stack.extend(edges.get(node, ()))
        edges.setdefault(task_id, set()).add(prerequisite)
        return

    raise AssertionError(f"unknown command {kind!r}")


# --------------------------------------------------------------------------- #
# the shared command sequence
# --------------------------------------------------------------------------- #


@st.composite
def _scenarios(draw: st.DrawFn) -> dict[str, object]:
    size = draw(st.integers(min_value=2, max_value=4))
    statuses = [draw(st.sampled_from(["backlog", "ready"])) for _ in range(size)]
    edges = [
        (target, source)
        for source in range(size)
        for target in range(source + 1, size)
        if draw(st.booleans())
    ]
    commands = [
        (
            draw(st.sampled_from(COMMAND_KINDS)),
            draw(st.integers(min_value=0, max_value=size - 1)),
            draw(
                st.sampled_from(
                    ["backlog", "ready", "in_progress", "review", "done", "cancelled"]
                )
            ),
            draw(st.integers(min_value=0, max_value=size - 1)),
            draw(st.integers(min_value=0, max_value=size - 1)),
            draw(st.sampled_from(ACTORS)),
        )
        for _ in range(draw(st.integers(min_value=4, max_value=8)))
    ]
    return {"size": size, "statuses": statuses, "edges": edges, "commands": commands}


def _observe(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    observed: dict[str, dict[str, object]] = {}
    for task in service.list_tasks(conn):
        observed[str(task["id"])] = {
            "status": task["status"],
            "claim": task["claimed_by"],
            "edges": set(task["depends_on"]),
            "dependency_ready": task["dependency_ready"],
            "eligible": task["eligible"],
            "reviewable": task["reviewable"],
            "blocked": task["blocked"],
            "claim_stale": task["claim_stale"],
        }
    return observed


def _expected(
    model: dict[str, dict[str, str | None]], edges: dict[str, set[str]]
) -> dict[str, dict[str, object]]:
    expected: dict[str, dict[str, object]] = {}
    for task_id, task in model.items():
        status = str(task["status"])
        prerequisites = edges.get(task_id, set())
        ready = all(model[p]["status"] == "done" for p in prerequisites)
        blockers = any(model[p]["status"] != "done" for p in prerequisites)
        expected[task_id] = {
            "status": status,
            "claim": task["claim"],
            "edges": set(prerequisites),
            "dependency_ready": ready,
            "eligible": status == "ready" and ready and task["claim"] is None,
            "reviewable": status == "review" and ready and task["claim"] is None,
            "blocked": status not in TERMINAL and blockers,
            "claim_stale": False,
        }
    return expected


def _recount(observed: dict[str, dict[str, object]]) -> dict[str, int]:
    tally = {"tasks": len(observed), "eligible": 0, "reviewable": 0, "blocked": 0, "claimed": 0}
    for status in ("backlog", "ready", "in_progress", "review", "done", "cancelled"):
        tally[status] = 0
    for task in observed.values():
        tally[str(task["status"])] += 1
        for key in ("eligible", "reviewable", "blocked"):
            tally[key] += int(bool(task[key]))
        tally["claimed"] += int(task["claim"] is not None)
    return tally


@settings(
    deadline=None,
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(_scenarios())
def test_board_and_model_agree_over_a_random_command_sequence(
    tmp_path: Path, scenario: dict[str, object]
) -> None:
    statuses = scenario["statuses"]
    edges_spec = scenario["edges"]
    commands = scenario["commands"]
    assert isinstance(statuses, list) and isinstance(edges_spec, list)
    assert isinstance(commands, list)
    size = int(scenario["size"])  # type: ignore[arg-type]

    conn, path = _board(tmp_path)
    with closing(conn):
        task_ids: list[str] = []
        model: dict[str, dict[str, str | None]] = {}
        for index in range(size):
            task = service.create_task(
                conn, title=f"task {index}", actor=ACTORS[0], status=str(statuses[index])
            )["task"]
            task_ids.append(str(task["id"]))
            model[str(task["id"])] = {"status": str(statuses[index]), "claim": None}
        board_edges: dict[str, set[str]] = {}
        for target, source in edges_spec:
            dependencies.add_dependency(
                conn,
                task_ids[int(target)],
                task_ids[int(source)],
                actor=ACTORS[0],
            )
            board_edges.setdefault(task_ids[int(target)], set()).add(task_ids[int(source)])

        for raw in commands:
            kind, task_index, target, prerequisite_index, _, actor = raw
            kind = str(kind)
            task_id = task_ids[int(task_index)]
            force, reason = _force_for(
                kind,
                model[task_id],
                str(actor),
                str(target) if kind == "status" else None,
            )
            model_error: str | None = None
            try:
                _model_step(
                    model,
                    board_edges,
                    kind=kind,
                    task_id=task_id,
                    target=str(target),
                    prerequisite=task_ids[int(prerequisite_index)],
                    actor=str(actor),
                    force=force,
                )
            except ModelError as exc:
                model_error = exc.kind

            board_error: str | None = None
            try:
                if kind == "status":
                    service.set_status(
                        conn,
                        task_id,
                        str(target),
                        actor=str(actor),
                        force=force,
                        reason=reason,
                    )
                elif kind == "claim":
                    claim_rules.claim(
                        conn, task_id, actor=str(actor), force=force, reason=reason
                    )
                elif kind == "release":
                    claim_rules.release(
                        conn, task_id, actor=str(actor), force=force, reason=reason
                    )
                elif kind == "recover":
                    claim_rules.recover(
                        conn, task_id, actor=str(actor), reason=reason or "recovering"
                    )
                else:
                    dependencies.add_dependency(
                        conn,
                        task_id,
                        task_ids[int(prerequisite_index)],
                        actor=str(actor),
                    )
            except (
                ValidationError,
                InvalidTransitionError,
                ClaimConflictError,
                CycleError,
            ) as exc:
                board_error = _error_kind(exc)

            assert board_error == model_error, (
                f"{kind} on {task_id}: board {board_error!r}, model {model_error!r}"
            )
            assert _observe(conn) == _expected(model, board_edges)

        observed = _observe(conn)
        projections = service.list_tasks(conn)
        assert service.counts(conn, projections) == _recount(observed)
        report = schema.doctor(path)
        assert report["ok"] is True, report["errors"]
        assert report["warnings"] == [], report["warnings"]
