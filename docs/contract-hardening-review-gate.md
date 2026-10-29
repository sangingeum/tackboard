# Contract hardening — combined review and gate

Card: T-000074 (reviewer/gate: vera). Implementation card: T-000068 (athena).
Repository: `/home/keum/dev/athena/tackboard`, branch `main`.
Verified tip: `c3cc348856fa592c6307dadb7e7da4af79ee2bdc` (`c3cc348`), working
tree clean at review time.
Range under review: `095fee2..c3cc348` — seven commits:

```text
c3cc348 test: model the board beside an in-memory state machine
6979f31 docs: record the contract-hardening surface
942a4b7 ci: migrate every released schema version and doctor the result
9ec7906 test: add the tracked scale benchmark
6b62e00 test: property-test the transition table, cycles and derived flags
e9bc5df test: pin reject's refusal under a foreign claim
e40f088 feat: expand doctor over notes, memo events and plan events
```

Diffstat: 19 files, +1944 / -10.

## Verdict

| Axis | Verdict |
| --- | --- |
| 1 — repo standards | **PASS** |
| 2 — spec fidelity (A–F) | **PASS** (A, B, C, D, E, F all PASS; gaps named in §7) |
| Declared scale ceiling | **Accepted** as an honest, policy-documented ceiling; **not a defect to file**. One documentation gap named (README carries no measured contention ceiling). |

No test dodge, no runtime dependency, no un-tracked script, no silent green.
Two real weaknesses in the property suite are named honestly in §7 (they are
coverage limits, not cover-ups).

## 1. Axis 1 — repository standards

### Runtime dependencies

`pyproject.toml` unchanged in the axis that matters: `dependencies = []` at
line 19. `hypothesis>=6.100` is in `[dependency-groups] dev` (line 36), beside
`pytest`, `ruff`, `mypy`, `pip-audit`, `pytest-cov`, `jsonschema`. `uv.lock`
grew by 61 lines, all dev-group resolution. `scripts/validate_schemas.py` still
asserts the empty core dependency set (26 documents validated, see §5).

### No silent test weakening

`rg -n "skip|xfail|pytest\.mark" tests/ src/ scripts/` returns only
`@pytest.mark.parametrize` uses and unrelated identifiers (`skip_host` in the
web client, the `skip-link` accessibility anchor, `skip` in a CLI help string).
There is **no** `xfail`, no `pytest.mark.skip`, no `skipif` anywhere in the
suite. Nothing from this round is quarantined: 519 tests collected, 519
executed.

### Every new script is checked in

Three new scripts, all real files under `scripts/` with docstrings, a
documented invocation and no inline `python -c`:

- `scripts/benchmark_scale.py` (274 lines) — tracked benchmark, ungated.
- `scripts/build_legacy_board.py` (78 lines) — the migration fixture builder.
- `scripts/check_doctor_clean.py` (35 lines) — report parser used by CI.

The two scripts referenced by tests are invoked by path via
`sys.executable` (`tests/test_benchmark_scale.py`, `tests/test_legacy_migration.py`),
so a broken script fails the suite rather than sitting unexercised.

### CI style

`.github/workflows/ci.yml` keeps the existing pattern exactly: pinned
`actions/checkout@11bd7190…` (v4.2.2) and `astral-sh/setup-uv@d4b2f3b6…`
(v5.4.2) with `version: 0.12.5`, `python-version: "3.12"`, and
`uv sync --frozen` before any step that needs the environment. The new
`migration` job (lines 45–75) is a matrix over `schema-version: [1, 2]` and
runs offline-friendly, checked-in commands only. Nothing in the new job is
secret-bearing and nothing needs a hosted service.

Note: the `test` job's pytest step is `uv run pytest -q` without `-W error`,
but `pyproject.toml` sets `filterwarnings = ["error"]`, so the warn-clean bar
is enforced by configuration in CI and by flag in this gate.

### Commit hygiene

Seven commits, each a single artifact, messages in the repo's
`type: summary` form (`feat`, `test`, `docs`, `ci`). All seven are
signature-verified (`%G? == G`) and authored by the repo-local identity
`sangingeum <sangingeum@gmail.com>`. No VM paths, agent names or credential
material in the new content; no version bump (`0.6.0` unchanged); no schema
migration (`user_version` stays 2).

Terminology check (`ruff` is the project's lint; it passes over
`src tests scripts`): new names are natural language — `benchmark_scale`,
`build_legacy_board`, `check_doctor_clean`, `test_model`, `test_properties`,
`test_doctor_contract`. No round/ADR/coordinate labels.

## 2. Axis 2 — spec fidelity

### A. Property-based tests — PASS

`tests/test_properties.py` (274 lines), `hypothesis` dev-group only, three
properties at `max_examples=25`, `deadline=None`:

- **Transition table** — `test_every_status_command_lands_or_refuses`: all
  6 statuses × 6 targets = the full `set_status` pair matrix, asserting the
  row either lands on the target or is unchanged (no silent third state).
  `test_claim_lands_or_refuses` covers claim on the four unclaimed statuses
  with and without `force`.
- **Cycle detection against an oracle** — `_brute_force_cycle` is an
  independent reachability-from-self oracle over 2–6 node DAGs with up to 8
  unique edges; `find_cycle_in_edges` (in memory) and `find_cycle` (on a real
  board, edges inserted raw) are both asserted against it, and a found cycle is
  checked edge-by-edge (`pairwise`) to be a real closed path.
- **Derived-flag consistency** — random boards (1–4 tasks, random statuses,
  explicit block, claim, staleness, DAG edges) are re-derived from raw rows:
  `dependency_ready`, `blocked`, `claim_stale`, `eligible`, `reviewable` each
  compared, and `service.counts` compared to a recount of the projection
  (per status, `eligible`, `reviewable`, `blocked`, `claimed`).

**Strategy bounds are stated** both in the module docstring (25 examples) and
in the composite strategies (`min_value`/`max_value` per size, `max_size=8`
edges).

**Runtime ratio measured, not asserted:** see §6 — 107 s against a 93 s
baseline measured in a clean worktree, **1.151×**, inside the ~2× ceiling.

### B. State-machine model test — PASS

`tests/test_model.py` (426 lines). A plain `dict` of tasks plus a `set` of
prerequisite edges (`ModelError` carrying the board's error kind) is driven by
the **same** generated command sequence as the SQLite board, 20 examples ×
4–8 commands over 2–4 tasks:

- Command kinds: `status`, `claim`, `release`, `recover`, `depend` — the five
  state-changing verbs the transition function serves.
- **`--force` paths are exercised by design, not by luck.** `_force_for`
  enumerates exactly the audited cases (recover a dead leg, take over or
  recover a foreign claim, recover an unclaimed `in_progress` task) and the
  model independently refuses a force used outside them; the docstring states
  that a forced start with unfinished prerequisites is never generated, so the
  sequence cannot manufacture an inconsistent board.
- **Rejections are compared by kind**, not just "both failed":
  `assert board_error == model_error` maps `InvalidTransitionError`,
  `ClaimConflictError`, `CycleError` and `ValidationError` to distinct strings.
- **Projection after every step**: status, claim, edges, `dependency_ready`,
  `eligible`, `reviewable`, `blocked`, `claim_stale`.
- **End of sequence**: `counts` equals a recount, and
  `schema.doctor(path)` must be `ok is True` **and** `warnings == []`.

### C. Scale benchmark — PASS (tracked, not gated)

`scripts/benchmark_scale.py`: fixed seed (`--seed`, default `20261030`),
fixed iteration count (`--iterations`, default 5), 50 distinct agents, p50/p95
via nearest-rank percentile for `next --claim`, `board --brief`
(`views.board_document`) and `graph --json` (`views.graph_document`), JSON
summary on stdout with an optional `--out`. The claim path measures the real
`claims.next_task(..., claim=True)` transition, concurrently, over 50 threads
and 10 attempts each.

Ungated is verified two ways. `tests/test_benchmark_scale.py` only asserts the
script runs at 100 tasks and that the claim count is 10 with no failures — no
timing assertion anywhere. `rg` over the suite and CI finds no threshold on any
`p50_ms`/`p95_ms`. README ("Scale benchmark") states "tracked, not gated" and
"no timing threshold fails CI"; DESIGN §9 says the same.

Published numbers are recorded, not asserted: the round's 10k figures live in
the card note, my 1k re-measurement in §5.

### D. Doctor expansion — PASS

`src/tackboard/schema.py`. Findings are appended to `warnings`; `errors` stays
the only source of `ok: False`, and `cli._run_doctor` returns
`6 if result["errors"] else 0` — exit 6 is still reserved for an unusable
board (proved in §5 against a non-database file).

- **Stale claims** — `_doctor_claim_states` reads `service.derived_flags(...)`
  with the caller-resolved `stale_after_seconds` (the CLI passes
  `claims.stale_claim_seconds()`), so doctor, the task projection and
  `claims --stale` share one rule and one threshold. It also warns on
  `claim_on_inactive_task`, `in_progress_without_claim` and
  `inconsistent_blocked_state`.
- **Note refs** — `_doctor_note_refs` warns `note_refs_exceeded` (>20),
  `note_ref_invalid` (unknown kind, empty value, control characters, non-hex
  commit, over the per-kind length limit) and `note_ref_missing_task`;
  `note_payload_invalid` covers a non-JSON payload. Each finding carries
  `task_id` and `event_id`, plus `kind`/`value`/`limit` where they apply.
- **Memo/event pointer consistency** — `_doctor_memo_events`:
  `memo_event_missing_revision` (event's `rev` not in `memo_revisions`),
  `memo_event_rev_mismatch` (newest event's `rev` != `max(rev)` in the table,
  with `expected`/`found`) and `memo_event_invalid` (no `rev` at all).
- **Plan events** — `_doctor_plan_events`: `plan_event_missing_task` (an
  `ids` entry is not a task on the board, with `event_id` + `task_id`) and
  `plan_event_invalid` (no id list).

Documented in README's doctor section and in the repo `SKILL.md`, both by
warning code, with the corruption-means-out-of-band-write rationale and the
"a warning never changes the exit code" rule.

**Proved to fire, at the CLI, on a genuinely corrupted scratch board**
(`/tmp/vera_gate_doctor_corrupt.py`; board built by `tack init`/`tack create`,
then three event rows inserted with raw SQL):

```text
corrupted exit: 0
codes: ['memo_event_missing_revision', 'memo_event_rev_mismatch',
        'note_ref_invalid', 'note_ref_missing_task', 'note_refs_exceeded',
        'plan_event_missing_task']
errors: []
ok: True
note findings name task/event: [('T-000001', 3)]
clean exit: 0
clean warnings: []
clean errors: []
RESULT: PASS
```

So five of the new checks fire on one board, carry the locating ids, and keep
`ok: True` / exit 0. A freshly built clean board produces **no** warnings (no
false positives). `stale_claim` was proved separately on a claimed task whose
`claimed_at` was aged by raw SQL: `codes: ['stale_claim']`, exit 0. The same
run shows a non-database file yielding `exit: 6` with `unreadable`, i.e. exit 6
untouched.

The in-suite proof is `tests/test_doctor_contract.py` (209 lines, 6 tests):
each surface warns, ids and expected/found values are asserted, and one test
asserts the valid-refs board is quiet.

### E. Migration matrix in CI — PASS

`.github/workflows/ci.yml` `migration` job, matrix `schema-version: [1, 2]`:
build the board → `tack migrate` → `tack doctor --json` →
`check_doctor_clean.py`. The v1 fixture is genuinely pre-migration, not
already-migrated, and this was verified by reading the builder **and** by
inspecting the built database:

`scripts/build_legacy_board.py` builds with the current code path, then for
`--version 1` drops `memo_revisions`' update/delete triggers, drops the table,
and sets `PRAGMA user_version=1`. Since the only released migration is
`1 → 2 = _migrate_to_v2` (create the memo DDL), removing the memo surface is
exactly what makes the migration do real work. The script's docstring says so.

Gate evidence (`/tmp/vera_gate_migration.py`, CLI only):

```text
--- v1 ---
pre-migrate user_version: 1
pre-migrate has memo_revisions: False
migrate exit: 0 migrated: True
post user_version: 2
post has memo_revisions: True
doctor exit: 0 ok: True
doctor errors: []
doctor warnings: []
check_doctor_clean exit: 0 doctor is clean: 1
--- v2 ---
pre-migrate user_version: 2
pre-migrate has memo_revisions: True
migrate exit: 0 migrated: False
post user_version: 2
doctor exit: 0 ok: True
doctor warnings: []
RESULT: PASS
```

### F. Reject under a foreign claim — PASS

`tests/test_reject.py::test_reject_under_a_foreign_claim_is_refused_until_the_claim_is_released`
pins both halves: `service.reject_task` by a reviewer against a task claimed by
the author raises `ClaimConflictError` naming the holder, and the task is
untouched (`status == review`, `claimed_by == author`); after
`claims.release(..., force=True, reason=...)` the reject succeeds and lands
`review -> in_progress` with `claimed_by == reviewer`, `rejections == 1` and
the reason recorded. No supervisor override was added. README states the
policy in the reject section ("`reject` inherits the claim policy … there is no
supervisor override").

## 3. The declared ceiling — adjudication

**Declared:** at 10,000 tasks with 50 concurrent claimers, 10 of 50 threads
exhausted the bounded retry budget and surfaced `LockedError`; recorded p50
153.5 ms / p95 13.65 s for `next --claim`, `board --brief` 68.7 ms, `graph`
4.4 s (2.27 MB).

**Ruling: acceptable ceiling, not a defect. Not filed.**

Reasons, in order of weight:

1. **It is the documented policy, not an accident.** DESIGN §6 specifies a
   busy timeout plus a *bounded* retry with backoff and then `LockedError` —
   "there is no unbounded retry loop" — mapped to exit 5 with
   `retryable: true`. `db.py` implements exactly that: `BUSY_RETRIES = 5`,
   `BUSY_BACKOFF_SECONDS = 0.05`, 5 s busy timeout, so a thread can spend
   ~27 s contending before it gives up. A coordination board must fail loudly
   rather than block forever or double-claim; the contract tells the caller to
   retry, and the caller owns that retry.
2. **The failure is safe.** `next --claim` selects and transitions inside one
   `BEGIN IMMEDIATE` transaction, so exhaustion produces *no* claim and *no*
   event — a retry cannot double-assign. `tests/test_concurrency.py` races
   eight processes for one claim to pin that.
3. **The failure is visible and structured.** The benchmark records every
   failure with actor, exception type and message instead of dropping it, and
   the round note names the 10/50 figure. Nothing is hidden to make the number
   look better.
4. **20% (10/50) at 10k with 50-way contention is plausible for one SQLite
   file with a single writer.** SQLite has one write lock; 500 claims across
   50 threads is a serialized write stream. 1k is clean: my re-measurement has
   500/500 claims with **zero** failures (§5), so the cliff is a scale effect,
   not a defect at the board's normal size.
5. **Cost of "fixing" it is worse.** Raising the budget or retrying unbounded
   converts a 13-second p95 into an unbounded stall — the failure mode
   DESIGN §6 explicitly rejects — for a board that is not meant to run
   thousands of simultaneous writers.

**Conditional, and the one thing I would change:** the ceiling is documented
only in the card note and this report. README's benchmark section says
"publishes a ceiling" without the number, and DESIGN §6 documents the retry
policy without the measured contention limit. A future operator reading
README cannot learn that 50-way claiming at 10k is expected to shed ~20% of
threads. That is a **documentation gap, non-blocking** — named in §7 item 4 —
not a reason to fail the round and not a code defect.

## 4. Named weaknesses in the new tests (honest gaps)

1. **A is state-invariant, not contract-exact.** The status property accepts
   *either* `InvalidTransitionError` or `ValidationError` and does not consult
   an oracle of documented `(from, to)` pairs; it proves "never a silent third
   state", not "this exact pair is the documented one". The exact pair table
   lives in the parametrized examples in `tests/test_service.py`
   (`ALLOWED`/`REJECTED`), so the property adds breadth, not a second opinion.
   The claim property covers four unclaimed statuses, not all six.
2. **B's force decision is the harness's, not the generator's.** `_force_for`
   chooses when to force, so the "force used where no enumerated case applies"
   rejection branch in `_model_step` is reachable only in principle; no
   generated sequence exercises a *bad* force. The enumerated force paths
   themselves are genuinely exercised.
3. **B models five verbs.** `note`, `update`, `memo`, `plan`, and the
   `complete`/`cancel`/`reopen`/`restore` shortcuts are outside the model
   (they reach the same transition function but mutate fields the model does
   not carry).
4. **C measures the read model, not the process.** `board --brief` and
   `graph --json` are timed as `views.*_document`, excluding CLI startup and
   JSON serialization; and the sample is small (5 read iterations) with no run
   variance, so p95 is a single-run figure.
5. **E's v1 fixture is a reconstruction.** It is faithful only because
   `1 → 2` is exactly "create the memo table". If a future migration touches
   anything the current `init` already applies, this fixture will stop
   exercising it; the honest fix then is a checked-in historical fixture.
6. **D's note/memo/plan checks are gated on the current schema version**
   (`if version == SCHEMA_VERSION`), so a v1 board reports only the older
   checks. Correct today; worth a comment if a v3 lands.

## 5. Gate rows (each run once, output captured)

| # | Gate | Command | Result |
| --- | --- | --- | --- |
| 1 | Suite, warn-clean | `uv run pytest -W error -q > /tmp/hardening-gate-pytest.log 2>&1` | **PASS** — `519 passed in 106.90s (0:01:46)`, exit 0, 0 skipped, 0 xfailed |
| 2a | Lint | `uvx ruff check src tests scripts` | **PASS** — `All checks passed!` |
| 2b | Types | `uv run mypy src` | **PASS** — `Success: no issues found in 17 source files` |
| 3 | Schema contracts | `uv run python scripts/validate_schemas.py --tack "uv run tack"` | **PASS** — `26 documents validated, 0 failure(s)` |
| 4 | Doctor clean on a fresh board | `/tmp/vera_gate_doctor_corrupt.py` (clean-board half) | **PASS** — `clean warnings: []`, `clean errors: []`, exit 0 |
| 5 | Benchmark smoke, 1k | `uv run python scripts/benchmark_scale.py --tasks 1000` | **PASS** (smoke; ungated) — see numbers below |
| 6 | Migration matrix | `/tmp/vera_gate_migration.py` (v1 and v2) | **PASS** — v1 `migrated: True`, `user_version 1 → 2`, `memo_revisions` created; doctor clean both rows |

Gate 1 counts: 519 collected / 519 passed — 15 more tests than the 504-test
baseline. Log: `/tmp/hardening-gate-pytest.log`.

Gate 5, 1k, seed `20261030`, 50 agents × 10 claim attempts, 5 read iterations:

```text
tasks 1000  agents 50  db 315392 bytes
next --claim : 500 claims, 0 failures, 7.12s, 70.22 claims/s,
               p50 10.416 ms, p95 3647.705 ms
board --brief: p50 8.544 ms, p95 11.078 ms
graph --json : p50 50.973 ms, p95 54.952 ms
```

This corroborates the recorded 1k figure (p50 9.6 ms / p95 3.5 s) within
run-to-run noise, and shows the collapse point is above 1k: at 1k the retry
budget is never exhausted. I did not re-run the 10k benchmark — the ruling in
§3 rests on the recorded numbers plus this spot check.

## 6. Measured suite runtime and ratio

Two runs, same command, same machine, clean trees:

- baseline `095fee2` in a detached worktree after `uv sync --frozen`:
  `504 passed in 92.85s`, exit 0 (`/tmp/baseline-pytest.log`);
- this tip `c3cc348`: `519 passed in 106.90s`, exit 0.

Ratio: 106.90 / 92.85 = **1.151×**. Ceiling was ~2× — met with margin.
(Butler's recorded 1.185× against 107.22 s / 90.5 s is consistent with this;
the small difference is host noise, not a different measurement.)

## 7. Named gaps

1. **Docs carry no measured contention ceiling.** README/DESIGN document the
   bounded-retry policy but not the 10k/50-way shedding figure; it lives only
   in the card note and here. Recommend one line in README's benchmark section
   after this round.
2. **Property tests prove the shape of the contract, not its exact table**
   (§4 item 1); the exhaustive pair oracle remains example-only.
3. **Model test's force selection is harness-driven** (§4 item 2) and covers
   five verbs, not the full CLI surface (§4 item 3).
4. **Benchmark is a read-model measurement**, small-sample, single-run (§4
   item 4); it does not measure CLI process overhead.
5. **v1 fixture is a reconstruction**, sound only while `1 → 2` is memo-only
   (§4 item 5).
6. **CI was reproduced locally only** — hosted-runner execution of the new
   `migration` job was not observed in this gate.
7. **Not run in this gate, by design:** coverage report (no threshold is set
   for this card), wheel/sdist build and `twine check`, the web UI beyond the
   suite, and the 10k benchmark.
8. **Two scratch gate scripts** used here
   (`/tmp/vera_gate_doctor_corrupt.py`, `/tmp/vera_gate_migration.py`,
   `/tmp/vera_gate_stale.py`) are gate evidence in `/tmp`, not repo artifacts;
   the repo's own equivalents are the checked-in scripts and tests cited above.

## 8. Routing

Verification passed on all six deliverables and both axes. Board state is the
butler's; this card is not marked complete here. Report commit: the commit that
adds this file.
