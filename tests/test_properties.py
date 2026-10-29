"""Property tests for the transition contract, cycle detection and flags.

The example base stays deliberately small (25 examples per property) so the
suite's wall time stays close to the example tests while still covering the
whole status matrix and random DAGs.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from itertools import pairwise
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tackboard import claims as claim_rules
from tackboard import schema, service
from tackboard.dependencies import find_cycle, find_cycle_in_edges
from tackboard.errors import ClaimConflictError, InvalidTransitionError, ValidationError
from tackboard.models import utcnow_iso

STATUSES = ["backlog", "ready", "in_progress", "review", "done", "cancelled"]
UNCLAIMED_STATUSES = ["backlog", "ready", "cancelled", "done"]
TARGETS = ["backlog", "ready", "in_progress", "review", "done", "cancelled"]

SETTINGS = settings(
    deadline=None,
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _board(tmp_path: Path) -> sqlite3.Connection:
    """A fresh, initialized board for one example."""
    path = tmp_path / "board.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    schema.init_database(path, label="board")
    return schema.open_database(path)


def _seed(conn: sqlite3.Connection, status: str, blocked_reason: str | None = None) -> str:
    """Insert a task in an arbitrary status through the raw row path."""
    task = service.create_task(conn, title="seed", actor="seed", status="backlog")["task"]
    conn.execute(
        "UPDATE tasks SET status = ?, blocked_reason = ? WHERE id = ?",
        (status, blocked_reason, task["id"]),
    )
    conn.commit()
    return str(task["id"])


# --------------------------------------------------------------------------- #
# every status command lands or refuses
# --------------------------------------------------------------------------- #


@given(status=st.sampled_from(STATUSES), target=st.sampled_from(TARGETS))
@SETTINGS
def test_every_status_command_lands_or_refuses(
    tmp_path: Path, status: str, target: str
) -> None:
    """A status command either lands on the target or leaves the row untouched."""
    with closing(_board(tmp_path)) as conn:
        task_id = _seed(conn, status)
        try:
            service.set_status(conn, task_id, target, actor="actor")
        except (InvalidTransitionError, ValidationError):
            after = service.get_task(conn, task_id)
            assert after["status"] == status
        else:
            after = service.get_task(conn, task_id)
            assert after["status"] == target


@given(status=st.sampled_from(UNCLAIMED_STATUSES), force=st.booleans())
@SETTINGS
def test_claim_lands_or_refuses(tmp_path: Path, status: str, force: bool) -> None:
    """Claim on an unclaimed task takes it or refuses, never a third state."""
    with closing(_board(tmp_path)) as conn:
        task_id = _seed(conn, status)
        try:
            claim_rules.claim(conn, task_id, actor="actor", force=force)
        except (InvalidTransitionError, ValidationError, ClaimConflictError):
            after = service.get_task(conn, task_id)
            assert after["claimed_by"] is None
        else:
            after = service.get_task(conn, task_id)
            assert after["claimed_by"] == "actor"


# --------------------------------------------------------------------------- #
# cycle detection against a reachability oracle
# --------------------------------------------------------------------------- #


def _brute_force_cycle(nodes: list[str], edges: dict[str, list[str]]) -> bool:
    """An independent oracle: a node reachable from itself means a cycle."""
    for start in nodes:
        seen: set[str] = set()
        stack = list(edges.get(start, []))
        while stack:
            node = stack.pop()
            if node == start:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(edges.get(node, []))
    return False


@st.composite
def _edge_sets(draw: st.DrawFn) -> tuple[list[str], list[tuple[str, str]]]:
    size = draw(st.integers(min_value=2, max_value=6))
    nodes = [f"n{index}" for index in range(size)]
    pairs = draw(
        st.lists(
            st.tuples(
                st.sampled_from(nodes),
                st.sampled_from(nodes),
            ),
            max_size=8,
            unique=True,
        )
    )
    return nodes, [(source, target) for source, target in pairs if source != target]


@SETTINGS
@given(_edge_sets())
def test_cycle_detection_matches_the_reachability_oracle(
    tmp_path: Path, edge_case: tuple[list[str], list[tuple[str, str]]]
) -> None:
    """``find_cycle`` agrees with the oracle, in memory and on the board."""
    nodes, pairs = edge_case
    edges: dict[str, list[str]] = {}
    for source, target in pairs:
        edges.setdefault(source, []).append(target)
    oracle = _brute_force_cycle(nodes, edges)

    found = find_cycle_in_edges(edges)
    assert (found is not None) is oracle
    if found is not None:
        assert found[0] == found[-1]
        for source, target in pairwise(found):
            assert target in edges.get(source, [])

    with closing(_board(tmp_path)) as conn:
        by_node = {node: _seed(conn, "backlog") for node in nodes}
        for source, target in pairs:
            conn.execute(
                "INSERT INTO dependencies (prerequisite_id, task_id) VALUES (?, ?)",
                (by_node[source], by_node[target]),
            )
        conn.commit()
        found_on_board = find_cycle(conn)
        assert (found_on_board is not None) is oracle
        if found_on_board is not None:
            assert found_on_board[0] == found_on_board[-1]


# --------------------------------------------------------------------------- #
# derived flags and counts against a fresh derivation
# --------------------------------------------------------------------------- #


@st.composite
def _boards(draw: st.DrawFn) -> dict[str, object]:
    size = draw(st.integers(min_value=1, max_value=4))
    return {
        "size": size,
        "statuses": [draw(st.sampled_from(STATUSES)) for _ in range(size)],
        "explicit": [draw(st.booleans()) for _ in range(size)],
        "claimed": [draw(st.booleans()) for _ in range(size)],
        "stale": [draw(st.booleans()) for _ in range(size)],
        "edges": [
            (source, target)
            for source in range(size)
            for target in range(source + 1, size)
            if draw(st.booleans())
        ],
    }


@SETTINGS
@given(_boards())
def test_derived_flags_and_counts_match_a_fresh_derivation(
    tmp_path: Path, board: dict[str, object]
) -> None:
    """Every derived flag, and the counts over them, agree with raw rows."""
    statuses = board["statuses"]
    explicit = board["explicit"]
    claimed = board["claimed"]
    stale = board["stale"]
    edges = board["edges"]
    assert isinstance(statuses, list) and isinstance(explicit, list)
    assert isinstance(claimed, list) and isinstance(stale, list)
    assert isinstance(edges, list)
    size = int(board["size"])  # type: ignore[arg-type]
    threshold = claim_rules.stale_claim_seconds()

    with closing(_board(tmp_path)) as conn:
        task_ids: list[str] = []
        for index in range(size):
            task_id = _seed(
                conn,
                statuses[index],
                "explicit block" if explicit[index] else None,
            )
            task_ids.append(task_id)
            if claimed[index]:
                stamp = "2020-01-01T00:00:00Z" if stale[index] else utcnow_iso()
                conn.execute(
                    "INSERT INTO claims (task_id, agent_id, claimed_at) VALUES (?, ?, ?)",
                    (task_id, "holder", stamp),
                )
        for source, target in edges:
            conn.execute(
                "INSERT INTO dependencies (prerequisite_id, task_id) VALUES (?, ?)",
                (task_ids[source], task_ids[target]),
            )
        conn.commit()

        projections = service.list_tasks(conn)
        for task in projections:
            index = task_ids.index(str(task["id"]))
            prerequisite_states = [
                statuses[source] for source, target in edges if target == index
            ]
            dependency_ready = all(state == "done" for state in prerequisite_states)
            blocked_expected = (
                statuses[index] not in {"done", "cancelled"}
                and (
                    any(state != "done" for state in prerequisite_states)
                    or explicit[index]
                )
            )
            row = conn.execute(
                "SELECT claimed_at FROM claims WHERE task_id = ?", (task["id"],)
            ).fetchone()
            claim_stale = (
                row is not None
                and claim_rules._age_seconds(str(row["claimed_at"])) >= threshold
            )
            eligible_expected = (
                statuses[index] == "ready"
                and dependency_ready
                and not explicit[index]
                and row is None
            )
            reviewable_expected = (
                statuses[index] == "review"
                and dependency_ready
                and not explicit[index]
                and row is None
            )
            assert bool(task["dependency_ready"]) is dependency_ready, task["id"]
            assert bool(task["blocked"]) is blocked_expected, task["id"]
            assert bool(task["claim_stale"]) is claim_stale, task["id"]
            assert bool(task["eligible"]) is eligible_expected, task["id"]
            assert bool(task["reviewable"]) is reviewable_expected, task["id"]

        summary = service.counts(conn, projections)
        assert summary["tasks"] == size
        for status in STATUSES:
            assert summary[status] == statuses.count(status)
        for key, flag in (
            ("eligible", "eligible"),
            ("reviewable", "reviewable"),
            ("blocked", "blocked"),
        ):
            assert summary[key] == sum(1 for task in projections if task[flag])
        assert summary["claimed"] == sum(
            1 for task in projections if task["claimed_by"] is not None
        )
