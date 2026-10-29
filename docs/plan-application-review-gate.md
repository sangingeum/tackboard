# Atomic plan application — review + verification gate

Combined two-axis review and independent QA gate over the atomic plan
application round, plus the two follow-ups folded in from the previous gate
(the `history --since` end-of-feed tests and the event-feed poll-interval docs).
One report, both axes, one verdict.

- Repository: `tackboard`, branch `main`.
- Baseline (release 0.6.0): `88b8d981236356e48f2986e180a30c9e4d21d21e`.
- Verified tip: `0d0e424351d7dac6fa7c87b3de66ea3a495d62bd`
  (`0d0e424` "feat: apply a whole task plan atomically").
- Commit range: `88b8d98..0d0e424`, two commits —
  `7674f58` "test: cover history --since past the end of the feed" and
  `0d0e424` "feat: apply a whole task plan atomically". Eleven files,
  +1073/−67.
- Tree clean at gate time; `git ls-remote origin refs/heads/main` equals the
  tip. Version deliberately not bumped (`pyproject.toml` still `0.6.0`,
  unchanged in the range).

Method: every behavioural probe ran against throwaway boards created with
`tack init` in a temporary directory, from the checkout (`uv run tack`); no
live board was opened for write. Probe scripts and logs were kept outside the
repository; commands below use `<tmp>` for that directory. The suite was run
once with the full output redirected to a log and read back.

## Verdict

- **Axis 1 (repo standards): PASS.** One domain layer, parameterized SQL only,
  UTC `Z` timestamps, one transaction and one event set per batch, no
  half-implemented surface, no schema change, build-green intermediate commit
  (independently re-run, not inferred from the message).
- **Axis 2 (spec fidelity): PASS on behaviour, FAIL on one documentation
  clause.** Eight of the nine contract points are fully met and verified. The
  ninth is met for behaviour and for `README.md`, but `SKILL.md` describes the
  plan file format in prose without the "small complete example" the contract
  asks for in both documents (see contract point 1 and the fix list).
- **Aggregate: behaviour verified PASS; one documentation clause open.** The
  defect is documentation-only, with no behavioural consequence; it is named in
  the fix list rather than softened, and was not edited here because it is an
  addition, not a typo in a line already being corrected.

## Axis 1 — repo standards

| Bar | Ruling | Evidence |
|---|---|---|
| One domain layer (CLI thin; validation and transaction in the service) | PASS | `cli._run_plan` reads the text, calls `plans.load_plan` and `plans.apply_plan`, then renders; it opens no transaction and repeats no validation. Field validation reuses the service's own checks (`_check_title`, `_check_initial_status`, `_check_priority`, `_check_assignee`, `_normalize_labels`), not copies. |
| Parameterized SQL only | PASS | New statements in `service._insert_task_row` / `_insert_dependency_edge` are static SQL with bound parameters; no string interpolation in either new module. |
| UTC ISO-8601 `Z` | PASS | Creation time comes from the existing `utcnow_iso()`; events are written by `history.record_event`, the same clock every other mutation uses. |
| One transaction, one event set | PASS | Rows, labels, edges, per-task `task.created` events and the single `plan.applied` event all execute inside one `write_transaction` (`BEGIN IMMEDIATE`, `isolation_level=None`). No second wrapper was introduced. |
| No half-implemented surface | PASS | The surface is `plan apply FILE\|- [--dry-run] [--agent]` and nothing else; `tack plan` alone is a usage error (exit 2) with a message naming the subcommand. No stray option, no unexposed helper. |
| Duplication avoided | PASS | The `task.created` payload was extracted into `service._record_created_event` and is now used by both `create_task` and the batch path, so the two cannot drift. Cycle detection was refactored into one walker, `dependencies.find_cycle_in_edges`, with `find_cycle` as a thin stored-edge caller — one algorithm, two callers, not a second implementation. |
| Commit hygiene (build-green intermediates, honest messages) | PASS | The intermediate commit `7674f58` was re-run independently in a detached worktree: 413 passed under `-W error`, ruff clean. Both messages state what landed; neither claims a gate it did not run. |
| Docs match code | PASS (one illustrative nit) | See the nit below; the behavioural claims in `README.md`, `SKILL.md`, `CHANGELOG.md` and `DESIGN.md` all match observed output. |
| No version bump / no dependency change | PASS | `pyproject.toml` untouched in the range. |

Nit, non-blocking: in `README.md` ("Plans"), the success document is introduced
as "On success the document is:" but shows `count: 2` with two entries, while
the example plan just above it declares three tasks. The shape is right; the
illustration silently drops the third task and could read as a bug report
against the example. Worth correcting when the `SKILL.md` example is added.

## Axis 2 — spec fidelity

| # | Contract point | Ruling | Observed |
|---|---|---|---|
| 1 | JSON file format: one top-level key `tasks`; unknown keys rejected; documented in README + SKILL with a complete example | **FAIL (documentation clause only)** | Format and rejection PASS: `{"tasks": [...]}` accepted; a second top-level key (`version`) → exit 4 `has unknown keys: version; allowed keys are: tasks`; an unknown element field (`descripion`) → exit 4 listing the allowed fields. README carries a complete three-task/two-edge example. `SKILL.md` describes every field and default in prose but carries **no example document**; the contract asks for one in both. |
| 2 | Create-only semantics stated honestly (re-applying creates a second set) | PASS | Second apply of the same file returned a disjoint id set `T-000004..T-000006`, task count 3 → 6, two `plan.applied` events. No `id` field exists, and README/SKILL/CHANGELOG say so explicitly. |
| 3 | File-scoped local keys; dangling/duplicate/missing-title/self-dependency all exit 4 | PASS | Dangling → exit 4 "not a key in this file; depends_on is file-scoped"; duplicate → exit 4 "duplicate key 'a'"; missing title only → exit 4 "'title' is required"; self-dependency → exit 4 "cannot depend on itself". A `depends_on` naming an existing board id (`T-000001`) is also exit 4, which is the same rule seen from the other side. |
| 4 | All-or-nothing: a bad element leaves task and event counts identical | PASS | On the live smoke board (6 tasks, 8 events): cycle → 6/6 tasks, 8/8 events, board SHA-256 identical before and after, byte size 94208 → 94208; dangling → 6 tasks, 8 events. A mid-batch failure after the first row is pinned by a unit test that monkeypatches the second insert and asserts tasks, labels, edges and events all rolled back. |
| 5 | Batch cycle detection (which machinery; why the no-cross-batch-edge claim is true) | PASS | `plans._check_cycles` builds the batch's own `key → [prerequisite key]` map and calls `dependencies.find_cycle_in_edges`, the same walker `find_cycle` uses on stored edges — no second algorithm. The no-cross-batch claim is true structurally: `depends_on` resolves only against keys declared in the file, so a plan cannot attach an edge to a pre-existing task (proved above by the exit-4 probe naming `T-000001`). |
| 6 | Events: one `task.created` per task + one board-level `plan.applied` with the documented payload; dry run emits none | PASS | After one 3-task batch: `{"plan.applied": 1, "task.created": 3}`; batch event has `task_id: null`, `actor: gate`, payload `{"file": "...", "tasks": 3, "ids": [T-000001..3]}`, newest-first after the three per-task events. Dry run: events 8 → 8, tasks 6 → 6. `plan.applied` is registered in `CANONICAL_EVENT_TYPES`. |
| 7 | Output contract: JSON only on stdout, `schema_version` 1, documented exit codes (0 / 4 / db codes), human renderer separate | PASS | stdout parsed as exactly one JSON document, `schema_version: 1`, stderr 0 bytes, `count`, `dry_run`, `source`, `tasks[{id,key,title}]`. Success 0; validation/cycle 4; missing board 3. Human renderer prints "Applied …: 3 tasks created." and one indented line per task (dry run: "would create … nothing written"). |
| 8 | No schema migration (`user_version` stays 2, no new tables/columns) | PASS | Board created at the tip: `user_version 2`, `application_id 1413563211`, tables `claims, dependencies, events, memo_revisions, metadata, sqlite_sequence, task_labels, tasks` — the 0.6.0 set. `plans.py` contains no DDL. |
| 9 | Docs: README, SKILL, CHANGELOG `[Unreleased]`, DESIGN §8 mapping row + §10 clarification | PASS | README gains a "Plans" section and the event-feed notes; SKILL gains the command line, the prose block and the JSON contract row; CHANGELOG has `[Unreleased] → Added` and `Fixed`; DESIGN §8 gains "Batch cycle detection" and "Atomic plan application" rows; DESIGN §10 states that "no bulk import" means no foreign data or history, and that atomic create-only application is in scope. The §10 wording is honest: it claims neither id adoption nor history replay, and the code matches it. |

## Gate — independent verification

Every row below was run by the gate in this session; the value is the observed
output, not a restatement of the author's report.

| # | Bar | Command | Observed | Ruling |
|---|---|---|---|---|
| 1 | Suite warn-clean at the tip | `uv run pytest -W error -q` (full output to a log, read in ranges) | `443 passed in 70.32s`, exit 0, no warnings | PASS |
| 2 | Lint | `uvx ruff check src tests` | `All checks passed!`, exit 0 | PASS |
| 3 | Types | `uv run mypy src` | `Success: no issues found in 16 source files`, exit 0 | PASS |
| 4 | Fresh board, 3-task/2-edge apply | `tack init --db <tmp>`, then `tack plan apply <tmp>/plan3.json --json` | exit 0; ids `T-000001..T-000003`; keys `design, build, null`; edges `001→002`, `002→003` (`source` is the prerequisite); events `{plan.applied: 1, task.created: 3}` | PASS |
| 5 | Re-apply is create-only | same command, second time | exit 0; ids `T-000004..T-000006`; 6 tasks; `{plan.applied: 2, task.created: 6}`; readiness across the two identical batches: 2 `eligible`, 4 `blocked` (1 / 2 per batch — each batch's root is eligible, its two dependents are blocked) | PASS |
| 6 | Cycle file → exit 4, board unchanged | `tack plan apply <tmp>/cycle.json --json` | exit 4, code `cycle`, message names `a -> b -> a`; board SHA-256 unchanged; tasks 6→6; events 8→8; bytes 94208→94208 | PASS |
| 7 | Dangling key → exit 4 | `tack plan apply <tmp>/dangling.json --json` | exit 4, code `validation`; tasks 6, events 8 unchanged | PASS |
| 8 | `--dry-run` writes nothing | `tack plan apply <tmp>/plan3.json --dry-run --json` | exit 0; `dry_run: true`; three `id: null`; events 8→8; tasks 6→6; stderr empty | PASS |
| 9 | `--db` untouched by a failure path | apply with `--db` pointing at a missing file | exit 3, `not_found`, and **no file created** | PASS |
| 10 | Cursor at/past the max event id | `tack history --since <max> --json`, `--since <max+50>` | exit 0 both; `items: []`, `order: "asc"`; `since` echoed | PASS |
| 11 | Empty board behaves | fresh board, `history --json` and `history --since 0 --json` | exit 0; `items: []` | PASS |
| 12 | 0.5 s interval and NDJSON-without-`--json` are stated | `tack guide`, `README.md` | guide: "streams NDJSON events (one JSON object per line) and polls every 0.5 s"; README: "polls every 0.5 s" and "always emits NDJSON, whether or not `--json` is given" | PASS |
| 13 | JSON is one document, `schema_version` 1 | `jq` on every stdout capture plus a stderr byte count | one object per invocation; `schema_version: 1`; stderr 0 bytes on success; error envelope also `schema_version: 1` on stdout | PASS |
| 14 | No schema migration | schema probe on the tip-created board | `user_version 2`; eight tables, none new; `application_id` unchanged | PASS |
| 15 | Intermediate commit is build-green | detached worktree at `7674f58`, suite re-run | `413 passed in 68.73s`, ruff clean | PASS |
| 16 | No version bump | `pyproject.toml` in the range | `version = "0.6.0"`, file untouched | PASS |

Computed, not eyeballed: the tip suite is 30 tests larger than the intermediate
commit's (`443 − 413 = 30`), all from the plan commit; the accepted batch
produces `2 × (3 + 1) + 1 + 1 = 10` events for the smoke board's final
`7` tasks (`3 + 3 + 1`), and 10 is what the board reported.

## Disagreements and named gaps

Nothing that was verified here is reported as passing on trust. The following
are the limits of this gate, stated plainly:

1. **`SKILL.md` has no complete plan example.** The brief asks for the format
   to be documented in `README.md` **and** `SKILL.md` with a small complete
   example; the `SKILL.md` block is prose only. Behaviour is correct and
   `README.md` has the example, but an agent that loads only `SKILL.md` never
   sees a concrete document. This is the one open contract clause.
2. **A real database error mid-batch was not forced.** Rollback on a
   mid-batch failure is proven by a unit test that raises from the second
   insert, and board identity after validation failures is proven byte-for-byte
   on disk. A genuine `SQLITE_FULL`/I/O failure during the transaction was not
   induced; the guarantee rests on the same `write_transaction` used by every
   other mutation.
3. **`--dry-run` ids are `null` by design**, so a dry run cannot be used as an
   id map for a later scripted step. This is documented; it is listed here so
   no one reads the preview as an allocation.
4. **Cycle errors carry `code: "cycle"`, not `code: "validation"`.** Exit 4 is
   consistent with the contract and with `depend add`; the docs describe both
   as validation errors, which is accurate at the exit-code level but not at
   the JSON `code` level. Not a defect; named for precision.
5. **README success example is internally inconsistent** (`count: 2` against a
   three-task example file) — see the Axis 1 nit.
6. **Housekeeping, outside this repository:** the installed copy of the
   `tackboard` skill no longer matches the repository `SKILL.md`
   (`sha256` differs after this round). That is expected after a skill edit and
   is the dispatcher's reinstall step, not a repo defect.
7. Coverage was not measured; this card's bars do not set a threshold for it.

## Required before the round closes

1. Add a small complete JSON example to the `SKILL.md` plan block (contract
   point 1). Documentation only.
2. While there, make the README success example's `count` match the example
   plan it follows (three tasks).

No behaviour change is requested; nothing in this report blocks the feature.
