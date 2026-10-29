# Review and gate — typed refs, reject, reviewer separation (T-000073)

Reviewer/gate: vera. Implementation card: T-000066. Gate card: T-000073.
Repository: `/home/keum/dev/athena/tackboard`, branch `main`.

## Verified tip and range

- Tip: `7d29d6f0452b8b729327ed2d20f649a96bc28edf` (`7d29d6f`)
- Range: `d88c392..7d29d6f` — six commits, all signed (`G`):
  - `97d7525` feat: typed note references, the reject shortcut and reviewer separation
  - `f7d82a4` feat(web): show the last rejection reason on the card and task page
  - `1062ce2` test: pin typed note references
  - `b6773e5` test: pin the reject contract
  - `2ee7628` test: pin reviewer separation
  - `7d29d6f` docs: document typed refs, reject and reviewer separation
- Working tree clean; `origin/main` equals the tip (`git ls-remote`).
- Commit identity `sangingeum <sangingeum@gmail.com>`, repo-local, signing on.

## Axis 1 — repo standards: PASS

- **One domain layer.** Ref validation, reject policy and reviewer separation
  live in `service.py`/`claims.py`/`models.py`; `cli.py` only parses flags and
  renders documents. The web change reads the task projection and renders it.
- **Parameterized SQL only.** The new queries in `_last_in_progress_actor` and
  `_rejection_stats` build only the `IN (?, …)` placeholder list dynamically;
  every value is bound. No string interpolation of user data.
- **Every mutation one transaction, one event.** `add_note` and `reject_task`
  open `write_transaction`; `apply_transition` joins the already-open
  transaction (`db.write_transaction` reuses it). Reject appends exactly one
  `task.rejected` event; a note appends exactly one `note` event.
- **No stored derived state.** `rejections`, `last_rejection_reason` and the
  review "author" are computed from the append-only `events` table on read
  (`_rejection_stats`, `_last_in_progress_actor`). Refs are event-only.
- **Additive within `schema_version` 1; no migration.** `schema.py` and `db.py`
  are untouched in the range; `init` still reports schema v2; task documents
  gain fields without removing or renaming any.
- **Commit hygiene.** Conventional `type: summary` messages, signed, and every
  intermediate commit builds green (see Gate rows).

## Axis 2 — spec fidelity: PASS on all points

### Typed references on notes

| Point | Verdict | Evidence |
|---|---|---|
| `note --ref KIND:VALUE` repeatable | PASS | `action="append"`; four refs round-trip in smoke |
| Closed vocabulary `commit\|url\|path\|task` | PASS | `NOTE_REF_KINDS`; unknown kind exits 4, message and `details.allowed` name the set |
| Commit id is 7–40 hex | PASS | `xyz` and a 41-character id both exit 4 |
| URL absolute http(s), no whitespace | PASS | `url:foo/bar` exits 4 |
| Path repo-relative: no leading `/`, no backslash, no `.`/`..`/empty segment | PASS | `/etc/passwd` and `../secrets` both exit 4 |
| `task` ref exists on the same board | PASS | `task:T-999999` exits 4 |
| At most 20 refs per note; duplicates collapse | PASS | 21 distinct refs exit 4; duplicate `path:x` stores once |
| URL ≤ 2048, path ≤ 1024 | PASS | 2049-byte URL and 1025-byte path exit 4; 2048/1024 accepted |
| Note text still 2 KiB; refs excluded from it | PASS | 2048-byte text + 20 refs accepted; 2049-byte text exits 4 |
| Event-only storage in `refs` array | PASS | payload `{text, refs:[{kind,value}]}`; no table/column |
| `show --json` and `history --json` return the same array | PASS | identical four-entry array in both; empty array for ref-less and legacy notes |
| Ref-less note keeps a documented, tested shape | PASS | `"refs": []`; tests cover plain and pre-existing rows |

### `tack reject`

| Point | Verdict | Evidence |
|---|---|---|
| Review → in_progress through the existing transition function | PASS | `reject_task` calls `apply_transition(mode="status")`; no new transition row or policy |
| Reason mandatory, stored in the event payload | PASS | empty/missing reason exits 4; payload carries `reason`, trimmed; ≤1024 characters |
| Non-review reject is a validation error (exit 4) | PASS | backlog and in_progress tasks both exit 4 with a clear message |
| One `task.rejected` event; **not** a forced transition | PASS | payload `{from, to, reason}` with no `forced` key; `--force` is not accepted on the verb |
| `rejections` and `last_rejection_reason` derived from history | PASS | `_rejection_stats`; count increments 1→2 across two reject cycles; zero/`null` when never rejected |
| No stored derived state | PASS | no column added; computed on every read |

### Reviewer separation

| Point | Verdict | Evidence |
|---|---|---|
| Valid only with `--stage review --claim` | PASS | both misuse cases exit 2 with named messages |
| Skips the last `in_progress` author | PASS | bob claims alice-authored work; alice with the flag gets `null` |
| Default behaviour unchanged | PASS | alice without the flag claims her own review item |
| Filter and claim stay atomic | PASS | filtering runs inside `next_task`'s single `BEGIN IMMEDIATE` select-and-claim |

### Web

| Point | Verdict | Evidence |
|---|---|---|
| Last rejection reason on the card and task page | PASS | served a scratch board; card HTML has `rejected 1×: tests do not cover the retry path`; task page has the same in its rejections fact |
| Read from the projection, no new endpoint, no contract change | PASS | two template helpers over `task["rejections"]`/`task["last_rejection_reason"]`; no route, no script, no CSP change |

### Docs

PASS — README gains "Handoff references and rejections" plus field limits and
the document-shape note; SKILL.md carries the commands, per-kind limits, document
shapes and derived fields; CHANGELOG lists all three under `[Unreleased]`;
DESIGN.md §4 states explicitly that reject is reason-bearing but **not** forced.

## Declared deviation — ruling: ACCEPTED

The three core features share commit `97d7525`. The commit message declares the
deviation and its reason: the previous leg left them as one uncommitted diff
across shared modules that cross-reference each other. Splitting that diff would
require reconstructing intermediate states that import and build.

Ruling: honest and safe. The message names the deviation; each intermediate
commit was independently built and tested here (`97d7525` 452 passed,
`f7d82a4` 452, `1062ce2` 473, `b6773e5` 484, `2ee7628` 491, tip 491), so the
"every commit builds green" guarantee holds. This is a process deviation from
the commit-per-artifact working agreement, not a contract or schema violation.
No functional or bisect risk remains.

## Gate rows — each run once, output captured

1. **Suite at tip** — `uv run pytest -W error -q > /tmp/refs-gate-pytest.log 2>&1`
   → exit 0, `491 passed in 86.11s` (39 new tests over the 452 baseline). PASS.
2. **Lint** — `uvx ruff check src tests` → `All checks passed!`, exit 0. PASS.
3. **Types** — `uv run mypy src` → `Success: no issues found in 16 source files`,
   exit 0. PASS.
4. **Independent smoke, fresh board** — PASS:
   - one ref of each kind round-trips through `show --json` and `history --json`;
   - unknown kind exit 4 naming `commit, url, path, task`; malformed, bad commit,
     relative URL, leading-slash path, `..` path, unknown task id all exit 4;
   - 21st ref exit 4; 20 refs + 2048-byte text accepted; 2049-byte text exit 4;
   - reject review task → `in_progress`, one `task.rejected` event with reason,
     `rejections: 1`, last reason set; reject without reason exit 4; reject a
     backlog task exit 4; second reject after leaving review exit 4;
   - `--different-from-author` selects per author; default unchanged; both
     misuse cases exit 2;
   - `doctor --json` reports `ok: true`.
5. **Web render** — PASS, HTML fetched from a served scratch board as above.
6. **Intermediate commits** — PASS; all five non-tip commits build and pass the
   suite under `-W error`.

## Named gaps

1. **Reject inherits the claim policy.** A review task claimed by another agent
   cannot be rejected by a third party: the transition returns exit 5
   (`claim_conflict`) unless the actor takes over or the claim is released first.
   No supervisor override was added. This is inherited policy, not a new one, but
   it is not covered by a test and the README does not spell it out. A reviewer
   who claims the review item (the normal `next --stage review --claim` path) is
   unaffected.
2. **No concurrency test for reviewer separation.** Atomicity is established by
   code reading — filtering and claiming share one `BEGIN IMMEDIATE` transaction
   — plus the unit tests, not by a racing-process test.
3. **Event type naming.** Notes still emit the existing `note` event type; the
   brief's "note.added" wording was read as descriptive. The payload shape is as
   specified and both `show` and `history` expose it identically.
4. **Mutation document shape.** `reject --json` returns the generic
   `{schema_version, changed, task}` document; the event itself is visible via
   `history --json`. No separate `event` key was added (none was required).
5. **Not run in this gate:** coverage run, wheel build, and the full web UI
   JavaScript behaviour. None are gate rows for this card.
