#!/usr/bin/env python3
"""A tracked, deliberately ungated scale benchmark for the board read paths.

Builds a board with a fixed seed, has a fixed number of agents claim tasks
through the real transition path, then times the three read surfaces every
agent and operator uses: ``next --claim``, ``board --brief`` and
``graph --json``. Results are published as JSON on stdout; there is no
pass/fail threshold, so a slow machine changes the number, not the build.

This is a development tool, not part of the package: it imports the project
from the checkout, like the other scripts under ``scripts/``.

Usage:
    python scripts/benchmark_scale.py
    python scripts/benchmark_scale.py --sizes 1000 10000
    python scripts/benchmark_scale.py --tasks 100 --agents 5 --iterations 1
    python scripts/benchmark_scale.py --out benchmark.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tackboard import claims, schema, views  # noqa: E402
from tackboard.models import PRIORITIES, task_id_for_sequence  # noqa: E402

STAMP = "2026-01-01T00:00:00Z"
ACTORS = [f"agent-{index:02d}" for index in range(1, 51)]


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of ``values`` in milliseconds."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index] * 1000.0, 3)


def _seed_board(path: Path, tasks: int, agents: list[str], seed: int) -> None:
    """Create a deterministic board of ``tasks`` rows with an acyclic DAG.

    The first fifth is ``done``, the next tenth is ``backlog`` gated on that
    done work, and the rest is ``ready`` and unblocked. That leaves a large
    eligible pool for the claiming threads while the graph carries real edges.
    """
    schema.init_database(path, label="benchmark")
    rng = random.Random(seed)
    done_until = max(1, tasks // 5)
    backlog_until = min(tasks, done_until + max(1, tasks // 10))
    conn = schema.open_database(path)
    try:
        rows = []
        for sequence in range(1, tasks + 1):
            if sequence <= done_until:
                status = "done"
            elif sequence <= backlog_until:
                status = "backlog"
            else:
                status = "ready"
            rows.append(
                (
                    task_id_for_sequence(sequence),
                    sequence,
                    f"benchmark task {sequence}",
                    "",
                    status,
                    PRIORITIES[sequence % len(PRIORITIES)],
                    None,
                    None,
                    STAMP,
                    STAMP,
                    STAMP if status == "done" else None,
                    1,
                )
            )
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO tasks(id, sequence, title, description, status, priority, "
            "assignee, blocked_reason, created_at, updated_at, completed_at, version) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        # Every backlog task is gated on two done tasks, so the graph has real
        # edges without blocking the ready pool the claimers draw from.
        edges = []
        for sequence in range(done_until + 1, backlog_until + 1):
            for _ in range(2):
                edges.append(
                    (
                        task_id_for_sequence(rng.randint(1, done_until)),
                        task_id_for_sequence(sequence),
                    )
                )
        conn.executemany(
            "INSERT OR IGNORE INTO dependencies(prerequisite_id, task_id) VALUES (?, ?)",
            edges,
        )
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'next_sequence'",
            (str(tasks + 1),),
        )
        # A few standing claims on ready work, so the claim-staleness path is
        # populated without blocking the eligible pool.
        for offset, actor in enumerate(agents):
            sequence = max(1, tasks - offset)
            if sequence <= backlog_until:
                continue
            conn.execute(
                "UPDATE tasks SET status = 'in_progress' WHERE id = ?",
                (task_id_for_sequence(sequence),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO claims(task_id, agent_id, claimed_at) VALUES(?, ?, ?)",
                (task_id_for_sequence(sequence), actor, STAMP),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _measure_claims(path: Path, agents: list[str], claims_per_agent: int) -> dict[str, Any]:
    """Run ``next --claim`` from concurrent connections and time each claim."""
    latencies: list[float] = []
    lock = threading.Lock()
    failures: list[str] = []
    claimed = 0

    def worker(actor: str) -> None:
        nonlocal claimed
        conn = schema.open_database(path)
        try:
            for _ in range(claims_per_agent):
                started = time.perf_counter()
                try:
                    result = claims.next_task(conn, actor=actor, claim=True)
                except Exception as exc:  # noqa: BLE001 - reported, never hidden
                    with lock:
                        failures.append(f"{actor}: {type(exc).__name__}: {exc}")
                    return
                elapsed = time.perf_counter() - started
                if result.get("task") is None:
                    return
                with lock:
                    latencies.append(elapsed)
                    claimed += 1
        finally:
            conn.close()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(actor,)) for actor in agents]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    total = time.perf_counter() - started
    return {
        "claims": claimed,
        "failures": failures,
        "seconds": round(total, 3),
        "claims_per_second": round(claimed / total, 2) if total else 0.0,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
    }


def _measure_reads(path: Path, iterations: int) -> dict[str, Any]:
    """Time ``board --brief`` and ``graph --json`` over fixed iterations."""
    conn = schema.open_database(path)
    try:
        board: list[float] = []
        graph: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter()
            document = views.board_document(conn)
            board.append(time.perf_counter() - started)
            assert document["counts"]["tasks"] > 0
            started = time.perf_counter()
            views.graph_document(conn)
            graph.append(time.perf_counter() - started)
    finally:
        conn.close()
    return {
        "iterations": iterations,
        "board_brief": {
            "p50_ms": _percentile(board, 0.50),
            "p95_ms": _percentile(board, 0.95),
        },
        "graph_json": {
            "p50_ms": _percentile(graph, 0.50),
            "p95_ms": _percentile(graph, 0.95),
        },
    }


def run_one(
    tasks: int,
    agents: list[str],
    claims_per_agent: int,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    """Build one board and return its benchmark summary."""
    with tempfile.TemporaryDirectory(prefix="tack-bench-") as directory:
        path = Path(directory) / "benchmark.sqlite3"
        _seed_board(path, tasks, agents, seed)
        summary = {
            "tasks": tasks,
            "agents": len(agents),
            "seed": seed,
            "database_bytes": path.stat().st_size,
            "next_claim": _measure_claims(path, agents, claims_per_agent),
        }
        summary.update(_measure_reads(path, iterations))
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tasks",
        type=int,
        action="append",
        dest="sizes",
        help="task count to benchmark (repeatable; default 1000 and 10000)",
    )
    parser.add_argument("--agents", type=int, default=50, help="distinct claiming agents")
    parser.add_argument(
        "--claims-per-agent", type=int, default=10, help="claims each agent attempts"
    )
    parser.add_argument("--iterations", type=int, default=5, help="read iterations")
    parser.add_argument("--seed", type=int, default=20261030, help="fixed seed")
    parser.add_argument("--out", type=Path, help="also write the JSON summary here")
    args = parser.parse_args(argv)

    sizes = args.sizes or [1000, 10000]
    agents = ACTORS[: max(1, min(args.agents, len(ACTORS)))]
    summaries = [
        run_one(size, agents, args.claims_per_agent, args.iterations, args.seed)
        for size in sizes
    ]
    payload = {
        "benchmark": "scale",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "config": {
            "agents": len(agents),
            "claims_per_agent": args.claims_per_agent,
            "iterations": args.iterations,
            "seed": args.seed,
        },
        "results": summaries,
    }
    text = json.dumps(payload, indent=2)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
