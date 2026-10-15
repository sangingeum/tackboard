"""Multi-process concurrency (spec 8.3, 16).

These tests use real separate OS processes: eight workers race for one task and
exactly one must win; racing ``next --claim`` workers must each receive a
distinct task. Thread-only tests do not satisfy section 16.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tackboard import claims, schema, service

TESTS_DIR = Path(__file__).resolve().parent
CLAIM_WORKER = str(TESTS_DIR / "_worker_claim.py")
NEXT_WORKER = str(TESTS_DIR / "_worker_next.py")

TIMEOUT_SECONDS = 120


def run_workers(script: str, invocations: list[list[str]]) -> list[dict[str, object]]:
    """Start every worker at once and collect their JSON results."""
    processes = [
        subprocess.Popen(
            [sys.executable, script, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for arguments in invocations
    ]
    results: list[dict[str, object]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
        assert process.returncode in {0, 2, 3, 4, 5}, stderr
        results.append(json.loads(stdout.strip()))
    return results


def test_eight_processes_race_for_one_claim(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Contested", status="ready")["task"]
    conn.close()

    agents = [f"agent-{index}" for index in range(8)]
    results = run_workers(CLAIM_WORKER, [[str(db_path), task["id"], agent] for agent in agents])

    winners = [result for result in results if result["claimed"]]
    losers = [result for result in results if not result["claimed"]]
    assert len(winners) == 1, results
    assert len(losers) == 7
    assert {result["code"] for result in losers} == {"claim_conflict"}
    assert {result["exit_code"] for result in losers} == {5}

    conn = schema.open_database(db_path)
    try:
        winner = winners[0]["agent"]
        active = claims.active_claim(conn, task["id"])
        assert active is not None
        assert active["agent_id"] == winner
        assert service.get_task(conn, task["id"])["status"] == "in_progress"
        assert len(claims.list_claims(conn)["items"]) == 1
    finally:
        conn.close()


def test_racing_next_claim_workers_each_get_a_distinct_task(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    created = [
        service.create_task(conn, actor="dev", title=f"Ready {index}", status="ready")["task"]
        for index in range(6)
    ]
    conn.close()

    agents = [f"agent-{index}" for index in range(6)]
    results = run_workers(NEXT_WORKER, [[str(db_path), agent] for agent in agents])

    claimed = [result["task_id"] for result in results]
    assert None not in claimed, results
    assert len(set(claimed)) == len(claimed) == 6
    assert set(claimed) == {task["id"] for task in created}

    conn = schema.open_database(db_path)
    try:
        assert len(claims.list_claims(conn)["items"]) == 6
    finally:
        conn.close()


def test_next_claim_with_a_single_task_has_one_winner(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Only one", status="ready")["task"]
    conn.close()

    agents = [f"agent-{index}" for index in range(4)]
    results = run_workers(NEXT_WORKER, [[str(db_path), agent] for agent in agents])

    claimed = [result["task_id"] for result in results]
    assert claimed.count(task["id"]) == 1
    assert claimed.count(None) == 3
