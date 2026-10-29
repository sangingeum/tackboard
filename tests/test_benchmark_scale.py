"""Smoke test for the tracked scale benchmark script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_scale.py"


def test_benchmark_script_runs_on_a_small_board() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--tasks",
            "100",
            "--agents",
            "5",
            "--claims-per-agent",
            "2",
            "--iterations",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    summary = payload["results"][0]
    assert summary["tasks"] == 100
    assert summary["agents"] == 5
    assert summary["next_claim"]["claims"] == 10
    assert summary["next_claim"]["failures"] == []
    assert summary["board_brief"]["p50_ms"] > 0
    assert summary["graph_json"]["p50_ms"] > 0
