# Event feed, agent summary and network warning — review + gate

Combined two-axis review and QA gate for the three changes landed over the 0.5.0
release (`d8405b4`): a resumable event feed in `history`, a derived per-agent
activity summary, and a `doctor` warning for a board on a network filesystem.
One report, both axes, one verdict.

- Baseline: `main`, code at `0d9d16e` (`c1accc1`, `df6c7eb`, `0d9d16e`).
- Tip at gate time: `a0af51f`, which adds only the skeleton of this report
  (`git diff --stat 0d9d16e..a0af51f` = one docs file). The tree was clean and
  `origin/main` matched the tip.
- Round contract: additive only — the plain `tack history` document and its
  ordering unchanged, no schema migration, no version bump.

Method: every behavioural bar ran against throwaway boards created with
`tack init` under a temporary directory; the live board was never opened for
write. The installed 0.5.0 binary is the "before" side of the backward
compatibility comparison; every new command ran as `uv run tack`. Probe scripts
were kept outside the repository; commands below use `<tmp>` for their
throwaway directory.

## Verdict

**PASS on all ten gate bars.** The round is additive as promised: the plain
`history` document is byte-identical to 0.5.0, the cursor is exclusive and
ascending, `--follow` is a bounded-poll NDJSON feed that exits 0 on SIGINT,
`agents` stores nothing, and the network warning is conservative (silent when
it cannot read the mount table, no false positive on a local disk). Two-axis
review: Axis 1 PASS, Axis 2 PASS with one named test gap (see follow-ups).

## Gate bars

| # | Bar | Ruling |
|---|-----|--------|
| 1 | Suite warn-clean at the tip | PASS |
| 2 | Ruff and mypy clean | PASS |
| 3 | Backward compatibility of plain `history` | PASS |
| 4 | Cursor contract (`--since`) | PASS |
| 5 | `--follow` is honest | PASS |
| 6 | `agents --json` is derived | PASS |
| 7 | `doctor` network warning | PASS |
| 8 | No schema migration | PASS |
| 9 | Docs match behaviour | PASS |
| 10 | No version bump | PASS |

### 1. Suite warn-clean

`uv run pytest -q -W error`, full output redirected to a log file and read
back: **411 passed in 67.55s**, exit 0. No warnings, no skips.

### 2. Lint and types

`uvx ruff check src tests` → `All checks passed!` exit 0.
`uv run mypy src` → `Success: no issues found in 15 source files` exit 0.

### 3. Backward compatibility of plain `history`

Board built with the installed 0.5.0 binary only (13 events: create, status,
claim, complete, release, note), then the same board read by both binaries:

| Command pair | Result |
|---|---|
| `tack history --json` vs `uv run tack history --json` | byte-identical (`diff` exit 0) |
| `tack history` vs `uv run tack history` (human form) | byte-identical |
| `tack history T-000003 --limit 2 --json` vs the same under `uv run` | byte-identical |

Top-level keys are still `["items", "schema_version", "task_id"]`, `task_id`
is `null` for the board-wide call, and ordering is still newest first
(`ids = [13,12,…,1]`). No `order` or `since` key appears in the plain form.

### 4. Cursor contract

All against the same board (max event id 13):

| Case | Observed |
|---|---|
| `--since 0` | exit 0, `order:"asc"`, `since:0`, 13 items, ids `[1…13]` |
| `--since 13` (equal to last id) | exit 0, `items: []` |
| `--since 1013` (beyond the end) | exit 0, `items: []`, `order:"asc"` |
| `--since 12` (cursor just before the last) | exactly `[13]` — exclusive confirmed |
| `--since abc` | exit 4, `{"error":{"code":"validation","message":"Invalid event id 'abc' for --since: use a non-negative integer."…}}`, stderr empty |
| `--since -1` | exit 4, validation error naming the negative cursor |
| `--follow --limit 2` | exit 2, `--follow cannot be combined with --limit.` |

Cursor semantics (exclusive `id > cursor`, ascending, at-least-once, cursor =
last id seen) are documented in `README.md`, `SKILL.md` and `CHANGELOG.md`.

### 5. `--follow` is honest

Feed started in its own session/process group with `start_new_session`, events
appended by separate `uv run tack note` processes:

- first appended event appeared as one line in **597 ms**, second in **490 ms**;
- idle CPU of the whole process group over a 3 s window: **0.00 %** (2 pids) —
  the loop sleeps on `FOLLOW_POLL_SECONDS = 0.5`, no busy spin;
- SIGINT to the process group returned **exit 0**, no surviving members;
- stdout carried **2 NDJSON lines**, each a flat object with
  `schema_version`, `id`, `timestamp`, `actor`, `event_type`, `task_id`,
  `payload`; stderr empty;
- `--follow --since 0` replayed ids `[1,2,3,4,5]` within 2 s and then streamed
  id 6; all ids ascending. No replay of older events without `--since`
  (first poll started at the board's current maximum id).
- `--follow` opens the board read-only; appends from other processes were not
  blocked while it was running.

### 6. `agents --json` is derived

- Fresh board: `{"schema_version": 1, "agents": []}`; human form prints
  `(no agent activity)`.
- On the 13-event board the produced counts match a recomputation from the raw
  `events` and `claims` rows exactly: `alice` completed 1, `bob` released 1,
  `carol` currently holds `T-000003`, `qa` (creator) zero counts, all
  `last_event_at` equal to each actor's highest event id.
- No stored state: a schema dump (tables, indexes, columns) of a board created
  by 0.5.0 and of a board created by the tip is identical (94 lines, `diff`
  exit 0); no table or column was added.
- Document shape is stable: top-level `["agents", "schema_version"]`,
  `schema_version` 1, agents sorted by `agent_id`.

### 7. `doctor` network warning

- Local disk board: exit 0, `ok:true`, `warnings: []` (real `/proc/self/mounts`,
  `/` is `ext4`); no false positive.
- Synthetic mount table with a network root (`nfs4` on `/`): one
  `network_filesystem` warning carrying `"fstype": "nfs4"` and
  `"mount_point": "/"`, and `ok: true` — a warning never changes the exit code.
- Unreadable mount table (`_read_mount_table` → `None`): silent, `ok: true`.
- Longest-match check: with an `nfs4` mount at `/` and `ext4` at
  `/mnt/share`, a board under `/mnt/share` resolves to `ext4` and stays silent;
  with `nfs` at `/mnt/share` it warns. `/` and `ext4` are not in the network
  fstype set, so the root match cannot misclassify a local board.
- Escaped mount points (`\040` for a space) resolve correctly.

### 8. No schema migration

`PRAGMA user_version` is **2** on the 0.5.0-created board and on the
tip-created board; `application_id` is identical (`1413563211`); the schema
dump is identical. The tip opens the 0.5.0 board without `migrate`
(`board` and `doctor` both exit 0). No migration ran in this round.

### 9. Docs match behaviour

`README.md` documents the NDJSON exception, the exclusive cursor, ascending
order, at-least-once delivery and the SIGINT exit 0. `SKILL.md` adds the
`--since` and `--follow` rows to the contract table and states the NDJSON
exception as the only one. `CHANGELOG.md` `[Unreleased]` describes the same.
The behaviour observed above matches all three. Version deliberately not
bumped — not counted as a miss.

### 10. No version bump

`pyproject.toml` still `version = "0.5.0"`; both `tack --version` and
`uv run tack --version` print `tack (tackboard) 0.5.0`.

## Two-axis review

### Axis 1 — correctness and design

PASS.

- **Derived-only respected.** `agents_document` computes the whole summary in
  one read transaction from `claims` plus `events`; nothing is stored and the
  schema is untouched.
- **One clock per document.** No generated clock is introduced; the only
  timestamps are the event log's own, and `last_event_at` is looked up from the
  actor's highest event id rather than from a second, unrelated time source.
- **Exclusive cursor, justified ordering.** `id > ?` with `ORDER BY id ASC`;
  the `events.id` column is `INTEGER PRIMARY KEY AUTOINCREMENT` and the table
  is append-only, so id ordering is the sound resume order. The plain read
  keeps `ORDER BY id DESC` and its 0.5.0 shape.
- **Error and exit conventions kept.** Invalid cursor → validation error exit 4
  (JSON error on stdout under `--json`, empty stderr); `--follow --limit` →
  usage error exit 2; unknown task → not-found exit 3.
- **`--follow` cannot corrupt or block the board.** It opens the database
  read-only, issues short read transactions, and only sleeps between polls;
  writers were able to append throughout the probe.
- **Conservative doctor detection.** Narrow fstype allowlist; longest mount
  point wins; silence when the mount table is unreadable or the path is not
  mounted; warning only, exit code untouched.
- **No hidden dependency.** The only new import is `time` (stdlib);
  `dependencies = []` is unchanged.

### Axis 2 — contract, docs and tests

PASS with one test gap.

- New documents appear in the `SKILL.md` contract table: the `--since` feed
  form, the `--follow` NDJSON form, and `agents`; the plain `history` row is
  annotated "newest first".
- Tests pin the main boundaries: the exclusive cursor and the equal-to-last-id
  empty result, invalid `abc`/`-1` exit 4, the plain document's lack of an
  `order` key, `--follow --limit` rejection, a real subprocess SIGINT test
  against the console script, `agents` counts, and the empty-board summary.
- The SIGINT path is a real subprocess test, not a script-only claim.
- `doctor` has three tests: network warning payload, silence on local disk,
  silence when the mount table is unreadable.
- **Gap:** no automated test pins `--since` *beyond the maximum id* or
  `--since` on an empty board. Both were verified live here and behave as
  documented, but the suite would not catch a regression in those two cases.
- Docs describe the NDJSON exception and the cursor semantics; the exact poll
  interval (0.5 s) is not stated, only "a bounded interval".

## Fixes in this commit

One unambiguous documentation defect, introduced by this round, fixed here:
in `SKILL.md` the memo contract row read `` `text:"",` `` (stray comma inside
the code span) and now reads `` `text:""` ``. Documentation only; no code,
test or contract change.

## Follow-ups

1. **Pin the remaining cursor boundaries in tests** — `history --since` with a
   cursor beyond the maximum event id, and on an empty board. Both pass live;
   the suite currently pins neither.
2. **State the poll interval** — `README.md` and `SKILL.md` say "a bounded
   interval"; naming the value (0.5 s) would make the claim checkable.
3. **Optional (documentation)** — note that `--follow` emits NDJSON even
   without `--json` (today only `SKILL.md` says it "implies `--json`"); this is
   behaviour observed, not a defect.
