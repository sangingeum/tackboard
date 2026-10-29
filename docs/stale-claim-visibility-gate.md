# Stale-claim visibility — QA gate report

Gate on the stale-claim visibility round. Ran against `18f550b` on `main`
(`f42bde4` feature, `ad37aef` snapshot freezing, `8fc00ca` docs, `c050f29`
review skeleton, `18f550b` review verdict and fixes). The working tree was
clean at the start of the gate and the tip had not moved.

Method: every behavioural bar ran against throwaway boards created with
`tack init` under a temporary directory. The live board was never opened for
write; its schema was read through a read-only SQLite URI and `doctor` ran on a
consistent snapshot taken with `tack backup`. Commands were driven from a script
file so the run is reproducible and its full transcript retained for the round
record. The installed `tack` 0.4.2 binary was used only as the "before" side of
the output comparison in bar 4; every other command ran as `uv run tack`.

## Verdict

**PASS on all nine bars.** The round is fit for the version bump. The review's
two code fixes and its documentation fix do what the review document says they
do. No new transition, force case or migration was added.

| # | Bar | Ruling |
|---|-----|--------|
| 1 | Suite is warn-clean | PASS |
| 2 | Lint and types | PASS |
| 3 | Derived fields, end to end | PASS |
| 4 | `claims --stale` contract | PASS |
| 5 | Config threshold | PASS |
| 6 | `doctor` warning | PASS |
| 7 | `recover` | PASS |
| 8 | No migration | PASS |
| 9 | Docs match behaviour | PASS |

## Bar evidence

### 1. Suite is warn-clean — PASS

Command: `uv run pytest -q -W error` (full output redirected to a file and read
from that file).

Observed: `403 passed in 66.44s (0:01:06)`, exit code 0. Because the run used
`-W error`, a warning would have failed the suite; the output contains no
warning text. This reproduces the pre-gate post-review count independently.

### 2. Lint and types — PASS

Commands: `uvx ruff check src tests`; `uv run mypy src`.

Observed: ruff printed `All checks passed!`, exit 0. mypy printed
`Success: no issues found in 15 source files`, exit 0.

### 3. Derived fields, end to end — PASS

Scratch board (`uv run tack init --db <scratch>/board.sqlite3`), three tasks
created in `ready`, two claimed by `alpha`, then the claim row for `T-000001`
backdated three hours in SQLite.

Observed through the CLI:

- `show T-000001 --json` → `claim_age_seconds: 10800` (JSON integer, not a
  string or boolean) and `claim_stale: true`.
- `show T-000003 --json` on the unclaimed control task →
  `claim_age_seconds: null`, `claim_stale: false`.
- `list --json` carried both claimed tasks in one document: stale age 10800,
  fresh age 0, age delta 10800 against a claim-timestamp delta of 10800, skew
  0s. The two ages were measured against one clock; the document does not
  straddle two.
- The arithmetic was re-checked with the calculation engine:
  `skew = abs(10800 - 10800)` → 0; `10800 >= 7200` → 1 (true);
  `10800 >= 300` → 1; `10800 >= 18000` → 0.

### 4. `claims --stale` contract — PASS

- `claims --stale --json` → items `["T-000001"]` only; the fresh claim is
  filtered out at the default 2h threshold.
- `claims --older-than 0s --json` → items `["T-000001", "T-000002"]`; the
  explicit duration overrides the threshold and shows the fresh claim.
- `claims --json` without `--stale` → both claims, top-level keys
  `["items", "schema_version"]`, per-item keys
  `["age_seconds", "agent_id", "claimed_at", "task", "task_id"]`.
- "Unchanged from 0.4.2" was measured, not asserted from the changelog: the
  installed 0.4.2 binary produced byte-identical text output to the round tip
  (`# active claims` header and both rows, same order, same columns) and the
  same JSON document keys. The only widening is inside each nested `task`
  document, which the round documents deliberately as the projection contract.
- `claims --older-than nonsense --json` → exit code 4, error document
  `{"code": "validation", "message": "Invalid duration 'nonsense'; use a
  number with a unit suffix (s, m, h, d), for example 2h.", "exit_code": 4}`.
  No silent fallback to the default. Exit 4 is the documented validation code.

### 5. Config threshold — PASS

Same board read from three directories, each with its own `.tackboard.toml`
(the board was selected explicitly with `--db`; only the threshold key varied):

- `stale_claim_after = "5m"` → `claims --stale` returned `["T-000001"]`
  (the 3h claim is stale at 5m).
- `stale_claim_after = "5h"` → `claims --stale` returned `[]`; the same 3h
  claim is not stale at 5h.
- `stale_claim_after = "nonsense"` → exit 4, `code: validation`, the duration
  parser's message naming the bad value.

**The design weak spot, stated plainly:** the threshold follows the process's
current directory, not the board's directory. One board therefore reports two
different staleness answers from two directories, and the difference is silent
because both answers are valid. The README describes exactly this behaviour
("the threshold is read from the nearest `.tackboard.toml` above the **current
directory**, not from the board's directory: the same board read from two
directories can therefore use two different thresholds") and gives the
workaround. Docs match reality. This is a design characteristic to revisit, not
a documentation mismatch.

### 6. `doctor` warning — PASS

- Before backdating, with two fresh claims: `doctor --json` → exit code 0,
  `ok: true`, `warnings: []`. A fresh claim produces no stale warning.
- After backdating `T-000001`: `doctor --json` → exit code 0, `ok: true`, one
  warning: `code: "stale_claim"`, message `T-000001 is claimed by alpha; the
  claim is 10801s old (stale after 7200s).`, with `task_id`, `agent_id` and
  `age_seconds` fields alongside. The age is one second over the backdated
  three hours because a second elapsed between the update and the read; the
  message and the structured field agree.
- Exit code was captured explicitly: 0, not 6. `doctor` returns 6 only for
  errors, and a stale claim is a warning.

### 7. `recover` — PASS

- As a different agent: `recover T-000001 --agent beta --reason "holder is
  gone"` → exit 0; the claim disappeared from `claims --json`; `history
  T-000001 --json` recorded a `task.released` event with payload
  `{"forced": true, "from": "in_progress", "reason": "holder is gone",
  "released_agent": "alpha", "to": "ready"}`. The reason is in the event.
- Without `--reason`: exit code 2, `error: the following arguments are
  required: --reason`.
- Recovering your own claim: exit code 4, message `T-000002 is already claimed
  by you; release it with plain 'tack release', or 'tack recover' a claim held
  by another agent.` The phrase "`--force` is not allowed here" does not
  appear, and the claim stays in place.
- No new transition or force case: `models.py` (the `ALLOWED_TRANSITIONS` map)
  is absent from the round's diff; the table still has the six statuses and the
  spec edges. `service.py`'s round diff adds one branch only — the case-specific
  refusal when a caller forces the release of its own claim. The force cases
  remain the four enumerated in `_FORCE_NOT_APPLICABLE` (takeover, unfinished
  prerequisites, adding an unfinished prerequisite to active work, recovery of
  an unclaimed `in_progress` task), matching the README's list. `recover` is
  still a thin wrapper over `release --force --reason`.

### 8. No migration — PASS

- Scratch board: `PRAGMA user_version` → 2; `init --json` reported
  `schema_version: 1`.
- Live pre-round board, read-only: `PRAGMA user_version` → 2,
  `application_id` → 1413563211, `PRAGMA quick_check` → `ok`. A `tack backup`
  snapshot of it then ran `doctor --json` → exit 0, `ok: true`, no errors, no
  warnings. The live file's size and mtime were identical before and after the
  gate, and no `-wal`/`-shm` files appeared, so nothing wrote to it.
- No DDL or migration ran: the round's diff on `schema.py` contains no
  `CREATE`, `MIGRAT`, `user_version`, `ALTER` or `DROP` lines, and `models.py`
  is untouched. The schema additions are read-only warning logic only.

### 9. Docs match behaviour — PASS

- `README.md` describes the two derived fields (`claim_age_seconds`, null when
  unclaimed; `claim_stale`), `claims --stale`, the `stale_claim` doctor warning,
  `recover` as a thin audited wrapper, the own-claim refusal with plain
  `release`, and the cwd-relative threshold with its workaround. Each statement
  matches bars 3–7 as observed.
- `SKILL.md` states the same contract and documents exit 4 for an unparseable
  configured duration; observed exit 4.
- `CHANGELOG.md` [Unreleased] describes the derived fields, the one-clock rule,
  `claims --stale`, the config key, `recover` and the doctor warning. Its
  sentence "Without `--stale` the document is unchanged" is accurate for the
  claims document's own keys and for the text output (both measured identical to
  0.4.2); the nested task projection gains the two fields, which the preceding
  bullet states as the intended projection change. No behavioural mismatch, but
  the sentence is narrower than it reads.

## Confirmation of the review's fixes

1. **One clock in `claims.py` — confirmed.** `list_claims` captures one `now`
   and passes it to both `_age_seconds` and `_project_rows`; the observed
   `list` document had zero skew between the age delta and the timestamp delta.
   `show` on the same board agreed (10800s, integer, stale true).
2. **`recover` refusal wording — confirmed.** Exit code 4, case-specific message
   naming `tack release`, no mention of a flag the user never typed, policy and
   claim state unchanged.
3. **README cwd-relative paragraph — confirmed.** The paragraph's claim was
   reproduced exactly: same board, two directories, two thresholds (5m → stale,
   5h → not stale).

## Rulings on the review's open findings

| # | Finding | Ruling | Reasoning |
|---|---------|--------|-----------|
| 4 | Three copies of the claim-age arithmetic | FOLLOW-UP | All three readers agreed in this gate (skew 0s, doctor age 10801s vs claims age 10800s one second apart), so the duplication is maintenance debt rather than present divergence; consolidate on the next touch. Recorded in `docs/stale-claim-visibility-review.md` (finding 4) and here. |
| 5 | Threshold computed in commands that never mention staleness | FOLLOW-UP | A malformed value breaks `show`/`list`/`doctor` too (observed: `show` exit 4 under the bad config), but the failure is loud, names the bad value, loses no data, and the round's own contract requires a validation error; narrowing the blast radius is a design improvement, not a release blocker. Recorded in the review document (finding 5) and here. |
| 6 | Same-agent refusal message not pinned by a test | FOLLOW-UP | The behaviour is correct today (exit 4, message names `tack release`) but a refactor could drop the wording; add the assertion the next time `tests/test_claims.py` is touched. Recorded in the review document (finding 6) and here. |

## Agreement with the review's stylistic calls

The gate agrees with the review's two-axis verdict: accept with named changes.
The names are natural language, the command verb fits the existing claim/release
family, and the one house-style debt is the triplicated age arithmetic, which
this gate also rules a follow-up. The web-view stale cue is out of scope for
this gate and was not judged.

## Follow-ups to carry into the next round

1. Consolidate the three age computations (`claims.py`, `service.py`,
   `schema.py`) behind one helper.
2. Stop resolving the stale threshold on read paths that never expose
   staleness, so a malformed `stale_claim_after` cannot break `show`/`list`.
3. Pin the same-agent `recover` refusal message in `tests/test_claims.py`.
4. Consider resolving the threshold relative to the board's directory; today it
   is cwd-relative and documented as such.

## Limits of this gate

- `doctor` ran against a `tack backup` snapshot of the live board, not the live
  file directly, because the live board is read-only to this gate; the live
  file's schema was read through a read-only URI. Both reported version 2.
- The installed 0.4.2 binary was used only for the "before" comparison in bar 4;
  it does not contain `--stale`, `recover` or the derived fields.
- The full command transcript is retained outside the repository for the round
  record; the commands above are the reproducible subset.
