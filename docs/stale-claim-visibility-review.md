# Stale-claim visibility — two-axis review

Board card T-000060. Reviewing the round landed on `main` at `8fc00ca`
(`f42bde4` feature, `ad37aef` snapshot freezing, `8fc00ca` docs). Scope of this
review is the spot-check list on the card: `service.derived_flags`, the
`--stale` flag and threshold resolution in `claims.py`/`cli.py`, the
`[tackboard] stale_claim_after` read in `config.py`, the `doctor` stale warning
in `schema.py`, the `recover` wrapper, and the tests that pin those contracts.
Web templates/static and the roadmap document are out of scope by the card's own
instruction. The verification bars at the tip (403 tests under `-W error`, ruff,
mypy, end-to-end smoke) are of record from the butler and were not re-run here;
the two small fixes below were checked with the four affected test modules only.

## Verdict

- **Correctness/contract axis: accept with named changes.** The round meets its
  design intent — derived only, no storage, `schema_version` stays 1, one
  command resolves a stale claim, no new force case, no silent fallback — but it
  shipped one document that straddled two clocks (`tack claims --json`) and one
  error message addressed to a flag the user never typed. Both are fixed in this
  review commit. The remaining named change is consolidation of the age
  arithmetic, which I did not make (see findings).
- **House-style/terminology axis: accept with named changes.** Names are natural
  language (`claim_age_seconds`, `claim_stale`, `stale_claim_after`,
  `stale_claim`, `recover`), no coordinate labels, and the command verb fits the
  repo's `claim`/`release` family. The one house-style debt is that the same age
  arithmetic now exists in three modules. The cwd-relative threshold was not
  documented plainly; that is fixed here in the README.

## Spot-check rulings

### 1. `service.derived_flags` — `claim_age_seconds` / `claim_stale`

Read `service.py:363-431` (`_claim_age_seconds`, `_configured_stale_seconds`,
`derived_flags`) and `service.py:434-508` (`_project_row`, `_project_rows`).

- **One clock per document: yes for the projection.** `_project_rows` takes one
  `moment` (`service.py:491`) and hands it to every row's `derived_flags`; no
  row recomputes "now". `get_task`, `list_tasks`, `project_tasks_by_id` and the
  board document all flow through it. The `now` argument is optional so callers
  can pin a clock; the default is one `utcnow_iso()` per projection call.
- **Null vs 0 for an unclaimed task: correct.** `_claim_age_seconds` returns
  `None` when there is no claim, so `claim_age_seconds` is `null` and
  `claim_stale` is `false` — not an age of zero. Pinned by
  `tests/test_claims.py:772-776`.
- **Computed in one place: partly.** The flag itself is computed only in
  `derived_flags`, but the *age arithmetic* is not: `service._claim_age_seconds`
  (`service.py:363-369`), `claims._age_seconds` (`claims.py:72-76`) and an inline
  copy in `schema._doctor_claim_states` (`schema.py:727-730`) all parse the same
  timestamp format. That is three implementations of one rule. Finding 4.
- **Projection coverage: yes.** The two fields are added in the same dict as the
  other derived flags (`service.py:470-475`), so they appear in `show`, `list`,
  `next`, the board document and each `claims` item's nested `task`. The web
  *graph* document (`views.py:83-96`) whitelists four derived flags and does not
  carry the new two; that cue belongs to board card T-000054 and is deliberately
  out of this round, so I note it as a boundary, not a finding.
- **Fixed: `claims --json` straddled two clocks.** `claims.list_claims` captured
  `now` for the item ages (`claims.py:184`) and then built the nested task
  documents without passing that clock (`claims.py:198-210`), so
  `_project_rows` took a *second* `utcnow_iso()`. In one document
  `items[].age_seconds` and `items[].task.claim_age_seconds` could differ by a
  second — exactly the "one now per read" rule the round set out to keep. The
  call now passes `now=now`; the document ages from a single clock. This was not
  pinned by any test, which is why it survived.

### 2. `claims.py` + `cli.py` — `--stale` and threshold resolution

Read `claims.py:46-69,136-226`, `cli.py:618-645,1075-1108,1234-1240`.

- **`--older-than` is still an override.** `list_claims` gives it precedence
  over `stale=True` (`claims.py:178-183`); the CLI passes both through unchanged.
  Exercised in both directions by `tests/test_claims.py:810-819`.
- **A bad duration is a validation error with a stable exit code.** Both the
  flag and the configured value go through `parse_duration`
  (`claims.py:50-59`), which raises `ValidationError` — exit 4. Nothing falls
  back silently. Pinned at the service layer
  (`tests/test_claims.py:843-855`) and the CLI layer
  (`tests/test_cli.py:1636-1651`, asserts `code == 4` and the JSON error code
  `validation`).
- **No-`--stale` output:** the text rendering is untouched
  (`cli.py:1086-1094`) and the claims document keys are unchanged
  (`schema_version`, `items`, and per item `task_id`, `agent_id`, `claimed_at`,
  `age_seconds`, `task`). Literal byte-identity with the pre-round binary is
  impossible because the round deliberately adds `claim_age_seconds` and
  `claim_stale` to every nested task document; that is the stated projection
  contract, not an accidental widening. Document keys are identical, so I judge
  the intent met.
- **`--stale` changes no ordering or filtering beyond the threshold**
  (`ORDER BY c.claimed_at, c.task_id`, skip when `age < threshold`), so the
  boundary is `age >= threshold`, matching `derived_flags`'s
  `claim_age_seconds >= threshold` and the doctor check's `age < threshold:
  continue`. One comparison direction across all three readers.

### 3. `config.py` — `[tackboard] stale_claim_after`

Read `config.py:40-42,97-120,25-27`.

- The key lives in the same optional `[tackboard]` table as `db`, so there is no
  second configuration surface. Absent file or key means `2h`; a present
  non-string, empty or blank value raises `ValidationError` naming the file and
  the key. The parse itself stays in `claims.parse_duration`, so `config.py` only
  reads text. That part is honest.
- **The weak point is resolution, not parsing.** `stale_claim_seconds()` reads
  the nearest `.tackboard.toml` walking up from **`Path.cwd()`**
  (`claims.py:62-69`, `config.py:40-42`), independently of how the database was
  resolved. The same board read from two directories can therefore use two
  thresholds — and the failure is silent, because the default `2h` is a valid
  answer rather than an error. That is the most likely place for an operator to
  be surprised. The module's own docstrings say "nearest", not "relative to the
  current directory"; the README said the same.
- **Fixed: documentation.** The README now states plainly, next to the key's
  description, that the threshold follows the current directory rather than the
  board's directory, that the same board can see two thresholds, and what to do
  about it (put the key at the board's root, or pass `--older-than`). The code
  is unchanged; I did not move resolution to the board's directory because that
  would change how `.tackboard.toml` itself is found for `db`, which is a wider
  contract than this round.
- **Honest failure mode, broad blast radius.** Because `_project_rows` always
  resolves the threshold (`service.py:492`) and `derived_flags` always computes
  the age, a malformed `stale_claim_after` fails *every* read — `show`, `list`,
  `doctor` — not only the stale paths. The failure is loud and names the file
  (good); the coupling is unnecessary (finding 5).

### 4. `schema.py` `doctor` — the `stale_claim` warning

Read `schema.py:423-505,656-745`.

- **Warning, not error.** The finding is appended to `warnings`
  (`schema.py:733-743`); `ok` remains `not errors` (`schema.py:502`), so a stale
  claim leaves `ok: true` and exit 0. Pinned by `tests/test_schema.py:354-360`
  and the CLI test at `tests/test_cli.py:1661-1675`.
- **Message carries task id, agent and age** — `"{task_id} is claimed by
  {agent_id}; the claim is {age}s old (stale after {threshold}s)"` — and the
  structured fields `task_id`, `agent_id`, `age_seconds` ride along. Pinned.
- **Module stays configuration-free.** `doctor` takes `stale_after_seconds` as a
  parameter and defaults to `config.DEFAULT_STALE_CLAIM_SECONDS`; the CLI
  resolves the configured value and passes it in (`cli.py:1234-1240`). Pinned by
  the relaxed-threshold assertion at `tests/test_schema.py:362-366`.
- The age here is computed inline against one `now` captured at the top of
  `_doctor_claim_states` (`schema.py:660`), so the doctor document does not
  straddle clocks. That inline copy is finding 4, not a clock bug.

### 5. `recover` in `cli.py` — the thin wrapper

Read `claims.py:136-161`, `service.py:641,667-674,855-895`,
`cli.py:631-645,1098-1108`.

- **Genuinely thin.** `claims.recover` calls `release(..., force=True,
  reason=reason)`, which calls `apply_transition(mode="release", ...)`. It adds
  no transition, no event type of its own (the event is `task.released`), and no
  force case. The two cases it leans on are already enumerated — another agent's
  claim (`_plan_release`, `service.py:875-882`) and an `in_progress` task with a
  lost claim (`service.py:864-873`). `apply_transition` still decides.
- **The reason is recorded like `release --force`.** `_check_force_reason`
  validates and trims it, and `apply_transition` writes `forced: true` plus
  `reason` into the event payload (`service.py:696-698`). Pinned at
  `tests/test_claims.py:869-872,906-908` and `tests/test_cli.py:1705-1710`.
- **Refusals are documented.** README states a claim the caller already holds is
  refused and a healthy unclaimed task is a no-op; SKILL.md repeats it. Pinned
  by `tests/test_claims.py:884-893`.
- **Ruling on the butler's observation — recovering your own claim fails with
  "`--force is not allowed here: ...`".** The *refusal is correct*: your own
  claim is not one of the enumerated force cases, releasing it needs no force,
  and `recover` is for a claim held by someone else — so failing is right and
  the claim correctly stays put. The *wording was a defect*: the user typed
  `recover`, not `--force`, and the message offered a policy catalogue instead of
  the next command. I fixed the wording, not the policy: `_plan_release` now
  raises a case-specific `ValidationError` — "`T-…` is already claimed by you;
  release it with plain 'tack release', or 'tack recover' a claim held by
  another agent." The exit code stays 4 and the transition still does not
  happen. Note the message is not pinned by a test (the existing test only
  asserts a `ValidationError`), so a future refactor could quietly lose it —
  finding 9.

### 6. Tests added in `tests/test_claims.py`, `test_cli.py`, `test_schema.py`

Read `test_claims.py:750-908`, `test_cli.py:1595-1710`,
`test_schema.py:325-366`.

Contracts pinned, not just implementation: the `>=` threshold boundary and the
`--older-than` override in both directions; the config key actually being read
(3h stale under 2h, not under 10d); an invalid configured value as a validation
error with exit 4 at both service and CLI layers; the warning present for an old
claim and absent for a fresh one, with `ok: true`; the relaxed-threshold
parameter; recover with and without a reason, the audited `forced`/`reason`
payload, the lost-claim recovery, and the no-op. The one-clock test
(`test_claims.py:779-794`) monkeypatches `utcnow_iso` to prove all rows share one
moment — a contract test, and the reason the projection's clock discipline is
pinned.

Gaps: the same-agent `recover` refusal is asserted only as "a ValidationError",
so the message I fixed is not held; the "threshold boundary" test backdates by
7200s against a later "now", so it exercises just-past rather than exactly-at;
and no test compares a `claims` item's age with its nested task's age — which is
why the two-clock bug in finding 1 went unnoticed. These are low severity and do
not block the round.

## Findings

| # | Severity | Location | Finding | Disposition |
|---|----------|----------|---------|-------------|
| 1 | Medium | `claims.py:198-210` | `tack claims --json` built its nested task documents with a second `utcnow_iso()`, so one document straddled two clocks; item age and task age could differ. | **Fixed** — pass the document's `now` into `_project_rows`. |
| 2 | Low | `service.py:855-895` | Recovering your own claim refused correctly but told the user "--force is not allowed here", a flag they never typed. | **Fixed** — case-specific message naming plain `tack release`. Refusal and exit 4 unchanged. |
| 3 | Medium | `README.md:109-116` | The cwd-relative threshold was not documented plainly; the same board read from two directories can silently use two thresholds. | **Fixed in docs** — README now states it and gives the workaround. Code unchanged by design decision. |
| 4 | Medium | `claims.py:72`, `service.py:363`, `schema.py:727` | Claim-age arithmetic exists in three copies. | Open — named change; consolidation touches three modules and the doctor's inline copy, too wide to land safely in this leg. |
| 5 | Low | `service.py:492,814,831` | Every projection, and even a plain `claim` through `_plan_claim`, resolves the config threshold and computes an age it does not use; a malformed key therefore fails commands that never mention staleness. | Open — named change; honest but over-coupled. |
| 6 | Low | `tests/test_claims.py:886` | The same-agent `recover` refusal's message is unpinned; only the exception type is asserted. | Open — named change for the next touch of that test. |

## What I fixed myself

Two code fixes and one documentation fix, in the review commit on this branch:

1. `src/tackboard/claims.py` — pass the document's single `now` into
   `_project_rows` for the nested task documents in `list_claims`.
2. `src/tackboard/service.py` — a case-specific refusal in `_plan_release` when
   `--force` targets the caller's own claim, replacing the generic
   "--force is not allowed here" for that branch. No policy or exit-code change.
3. `README.md` — a paragraph stating that `stale_claim_after` is read from the
   nearest `.tackboard.toml` above the current directory, with the workaround.

After the fixes: the four affected modules pass under `-W error`
(`220 passed`), `ruff check src tests` is clean and `mypy src` is clean. The full
suite was not re-run; the tip's `403 passed` remains the bar of record.

## Scope I did not audit

Web templates, static assets and the web stale cue (board card T-000054); the
roadmap document; `apply_transition`'s force policy outside the release path;
migration/schema storage (none was touched); and the end-to-end smoke, whose
results are the butler's of record.
