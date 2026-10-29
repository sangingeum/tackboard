"""Run the adoption examples end to end.

The examples are documentation that executes: if the CLI's contract changes
under them, these tests fail here rather than in a reader's terminal. Each is a
subprocess run in its own temporary directory, with the board and any repository
created from scratch — no network, no daemons.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"

CASE_TIMEOUT = 90

#: example folder -> its runnable script
SCRIPTS = {
    "worktree-per-claim": "worktree_per_claim.py",
    "supervisor-loop": "supervisor_loop.py",
    "planner-workers-reviewer": "planner_workers_reviewer.py",
}


def _run_example(folder: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = EXAMPLES / folder / SCRIPTS[folder]
    assert script.is_file(), f"{script} is missing"
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=CASE_TIMEOUT,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "TACK_CMD": f"{sys.executable} -m tackboard",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )


def test_worktree_per_claim_runs_end_to_end() -> None:
    result = _run_example("worktree-per-claim")
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "alice claimed T-000001" in out
    assert "task/T-000001" in out
    assert "claim_conflict" in out, "the refusal path did not run"
    assert "alice completed T-000001: changed=True" in out
    assert "bob's next claim: T-000002" in out


def test_supervisor_loop_terminates_and_resumes() -> None:
    result = _run_example("supervisor-loop", "--max-passes", "3")
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "pass 1" in out and "pass 3" in out
    assert "task.completed" not in out  # events are described, not dumped
    assert "T-000001 completed -> 2 dependent(s)" in out
    assert "note the handoff" in out, "no status_changed reaction"
    assert "cursor persisted at" in out
    assert out.count("pass ") == 3, "the loop ran more passes than allowed"


def test_planner_workers_reviewer_runs_the_rejection_round() -> None:
    result = _run_example("planner-workers-reviewer")
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "planner applied 3 tasks" in out
    assert "reviewer rejected" in out
    assert "worker re-claimed" in out
    assert "'done': 3" in out


@pytest.mark.parametrize(
    "folder",
    sorted(SCRIPTS),
)
def test_every_example_names_its_run_command(folder: str) -> None:
    readme = (EXAMPLES / folder / "README.md").read_text(encoding="utf-8")
    assert f"examples/{folder}/{SCRIPTS[folder]}" in readme
