# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `tack plan apply FILE|- [--dry-run]` creates a whole dependency DAG in one
  command. The plan is JSON with one top-level key, `tasks`; each element may
  carry a local, file-scoped `key`, `title`, `description`, `status` (default
  `ready`), `priority` (default `normal`), `assignee`, `labels` and
  `depends_on` (keys from the same file). Unknown fields, duplicate keys,
  dangling references, self-dependencies, invalid values and cycles among the
  batch's own edges are validation errors (exit 4) and create nothing. An
  accepted batch commits every task, label and edge in one `BEGIN IMMEDIATE`
  transaction — create-only, so applying the same file twice creates a second
  set of tasks and never updates or adopts an existing one. `--dry-run`
  validates and reports without writing anything. The JSON document is
  `{schema_version, dry_run, source, count, tasks:[{id, key, title}]}`; a dry
  run reports `id: null` because ids are assigned inside the write
  transaction. No schema migration.
- A plan application appends one canonical `task.created` event per task plus
  one board-level `plan.applied` event whose payload carries `file`, `tasks`
  and `ids`.

### Changed

- **JSON contract (additive):** a `task.updated` event for an assignment change
  now carries the new value as `assignee` beside the existing
  `previous_assignee` (`null` means unassigned). The `fields` list still names
  `assignee`, `schema_version` stays `1`, and no field is renamed or removed.
  The web timeline renders the pair as `previous → new`; `tack history` (human
  and JSON) shows both because the payload carries both.
- Web view: stale active claims are marked without relying on colour alone.
  The board card appends `⚠ stale`, the graph holder badge reads `· stale` with
  a dashed outline, the no-JavaScript outline appends `— stale`, and the task
  page shows a `⚠ stale claim` chip. The web layer reads the server's derived
  `claim_stale` flag, so the threshold is the one `tack claims --stale` uses.

### Fixed

- `tack history --since EVENT_ID` now documents and tests the resume cursor's
  end of feed: a cursor at or past the newest event, and an empty board, both
  return an empty oldest-first feed (exit 0) rather than an error. The event
  feed's poll interval (`0.5 s`) is stated in `README.md` and `tack guide`.
- The docs no longer claim a 45° hatch fill on blocked graph nodes: the drawn
  cue is the 3 px border plus `· blocked` on the node's status line, with the
  `⊘ blocked` chip in the outline. `README.md` and the web design system now
  describe what the pixels support.

## [0.6.0] - 2026-10-29

### Added

- `tack history --since EVENT_ID` resumes the append-only event feed: events
  with `id` greater than the cursor, **oldest first**, so a consumer appends in
  order. The document adds `order: "asc"` and echoes the cursor in `since`;
  the plain `history` document is unchanged (newest first). Delivery is
  at-least-once; de-duplicate on event `id`.
- `tack history --follow [--since EVENT_ID]` streams new events as NDJSON, one
  flat JSON object per line — the only command allowed to emit more than one
  JSON document. Without `--since` it starts at the board's current maximum
  event id; it polls on a bounded interval and exits **0** on SIGINT. It cannot
  be combined with `--limit`.
- `tack agents [--json]` summarises per-agent activity from active claims and
  the event log: claimed tasks, last event time, and counts of completed,
  released and forced actions. Nothing is stored and `schema_version` stays
  `1`; a board with no activity returns `"agents": []`.
- `tack doctor` warns with `network_filesystem` when the database sits on a
  network or shared mount where SQLite's WAL locking is unsafe. The warning
  carries `fstype` and `mount_point`, never changes the exit code, and stays
  silent when the mount table cannot be read.

## [0.5.0] - 2026-10-29

### Added

- Stale-claim visibility. The task projection now carries two derived,
  read-only fields: `claim_age_seconds` (the active claim's age in seconds,
  `null` when the task is unclaimed) and `claim_stale` (whether that age
  reached the stale threshold). Nothing is stored and the JSON document
  `schema_version` stays `1`; the age is measured against one clock per read,
  so a single document never straddles two.
- `tack claims --stale` lists only claims older than the configured threshold.
  The default is `2h` and may be overridden by the optional
  `[tackboard] stale_claim_after = "2h"` key in `.tackboard.toml`; an
  unparseable value is a validation error. `--older-than` still works and
  overrides the threshold for one call. Without `--stale` the document is
  unchanged.
- `tack recover TASK_ID --reason TEXT` resolves a dead claim in one audited
  command. It is a thin wrapper over the existing `release --force --reason`
  transition, mapping only to the enumerated force cases (a claim held by
  another agent, or an `in_progress` task that lost its claim); it adds no
  transition, no force case and no schema change.
- `doctor` emits a `stale_claim` warning per stale active claim, naming the
  task, the claiming agent and the claim's age. It remains a warning: `ok`,
  `errors` and the exit code are unchanged (exit 0).

### Fixed

- The `claims --json` document now measures every claim's age against one clock
  per read, so a single document can no longer straddle two clocks.
- Releasing your own claim through `recover` now refuses with a message naming
  plain `tack release`, instead of mentioning `--force` which the user never
  typed. The refusal's policy and exit code 4 are unchanged.
- `README.md` states that the stale threshold, like the database path, is read
  from the nearest `.tackboard.toml` above the current directory, so the same
  board read from two directories can use two thresholds.

## [0.4.2] - 2026-10-25

### Fixed

- Web graph: a `reviewable` node now carries its marker as a second parallel
  line 3 px inside the card's top edge, in the reviewable colour, instead of an
  unnamed blue bar near the bottom edge. The marker is named in the legend
  ("reviewable — double top edge"), in each node's accessible name and title,
  and in the server-rendered dependency outline.
- Web graph: every node is now one uniform rounded rectangular card, so the ID,
  title, status label and holder badge always stay inside the node outline. A
  corner glyph plus colour carry the stored status; only `backlog` (dashed)
  and `cancelled` (dotted) also change the border style. The legend and README
  describe that glyph language. A claimed node's holder
  badge sits on its own row below the status label, so the two never overlap.
  The `+N` elided-link marker is anchored at a fixed top-right corner and no
  longer collides with titles. Edges render beneath the opaque cards and are
  no longer visible across node text.

### Changed

- Web view (Round 2, UI state and layout): the graph toolbar toggles now have a
  visible on-state that does not rely on colour alone — a pressed button gains
  a 2 px border and a bolder label, so "Highlight claimed", "Focus claimed" and
  "Hide done & cancelled" read as on or off. The minimap's "Collapse minimap"
  control sits in its own row below the drawing instead of on top of it, so it
  no longer covers minimap content. Blocked sentences on board cards keep a
  task ID whole: the ID is wrapped in a `nowrap` span, so a narrow card wraps
  before the ID rather than at its hyphen. The Prerequisites and Dependents
  sections on `/task/<id>` are separated by the same 16 px rhythm the other
  panels use. Removed six unreferenced `.shape-*` legend rules left over from
  the pre-uniform-card design, and documented the glyph map so `web.py`,
  `graph.js` and `board.js` stay in sync. README corrected: only `backlog` and
  `cancelled` nodes change border style.

## [0.4.1] - 2026-10-25

### Added

- `tack next --assignee AGENT_ID` selects only work routed to that agent, and
  `next --assignee ""` selects only unassigned work. The default (unassigned
  or the acting agent's route) and `--all-assignees` are unchanged; the two
  selector forms cannot be combined. The selector applies to plain `next` and
  `next --claim`; a claim still records the acting agent and leaves the
  assignment as routed.

## [0.4.0] - 2026-10-24

### Added

- Graph and task documents now carry optional `claimed_by` and `claimed_at`
  fields on every node and task, so `tack graph --json` and `/api/graph` agree
  on claim data for the same database. The change is additive: `schema_version`
  stays `1` and no field is removed or renamed. Both fields are nullable and a
  consumer must treat them as possibly absent.
- The web view implements the claimed overlay and hide-closed design: a double
  ring and holder badge on claimed nodes (relative age in the badge title, full
  ISO timestamp in the tooltip and `<title>`), "Highlight claimed" and "Focus
  claimed" toggles, a "Hide done & cancelled" toggle with URL-over-storage
  persistence (`?hide_closed=1` / `?hide_closed=0`), hidden-node and elided-link
  counts, and a `+N` marker on nodes whose links were hidden. Derived
  `blocked`/`eligible`/`reviewable`/`dependency_ready` flags remain computed
  from the full graph.
- Keyboard shortcuts and a help dialog: `g b`/`g g`, `/`, `f`, `j`/`k`, Enter,
  `t`, `c`, `h`, `+`/`-`/`0`, arrow keys, `?` and Esc, with the bindings ignored
  in text fields and suspended while a dialog is open. The canvas is a single
  tab stop with `role="group"`, `aria-roledescription`, `aria-activedescendant`
  and per-node `role="link"` labels; a skip link precedes the top bar.

### Changed

- The read-only web view (`tack serve`) is rebuilt as a modern dashboard. The
  CLI contract, the JSON `schema_version: 1` and the database are unchanged;
  every change is additive or visual. The board polls the JSON API with
  `If-None-Match`/`ETag` (an unchanged board answers `304` with an empty body)
  and diffs the DOM instead of reloading the page. Filters, scroll position,
  selection and open disclosures survive updates, cards animate between columns
  (FLIP), changed items flash briefly, and count changes animate. Polling pauses
  while the tab is hidden and has a visible pause/resume control and a
  "last updated Ns ago" indicator.
- Design tokens and a theme: CSS custom properties for colour, spacing, radius,
  shadow, type scale and motion durations; one visual language for the six
  statuses and the priorities across board, graph and task pages. The theme
  follows `prefers-color-scheme` with a manual `auto → light → dark` toggle
  stored in `localStorage` and applied as `data-theme` on `<html>`, set before
  first paint by an external synchronous script so there is no flash of the
  wrong theme. `prefers-reduced-motion` disables or shortens animations.
- `/board` is a dashboard: clickable status counts, chip filters, claim badges
  and a bounded closed-card list. `/task/<id>` gains a facts panel, blocker
  summary, timeline and an in-house Markdown renderer built from
  `createElement`/`textContent` with `http(s)`-only links and
  `rel="noopener noreferrer"` — no third-party library and no build step.
  `/graph` is rebuilt on hand-written SVG with a longest-path layout, pointer
  navigation (drag-to-pan with a movement threshold, zoom-at-cursor, wheel and
  trackpad handling, click-to-select, click-empty-to-clear, double-click zoom,
  edge hover and a draggable minimap), path highlighting and a minimap. The
  server-rendered dependency outline still works without JavaScript.
- The web view is responsive: scroll-snap columns at wide widths, tabs below
  640px, touch pan/pinch/tap support and 44×44px minimum targets. The
  accessibility pass targets WCAG 2.2 AA in both themes: state is never conveyed
  by colour alone, focus rings meet contrast requirements, and count changes are
  announced through one debounced live region.

### Removed

- The full-page auto-refresh script (`static/refresh.js`); polling and diffing
  replace it.

### Fixed

- The graph keeps its viewport still when arrowing between active nodes instead
  of recentring the camera on every keyboard move. Graph pointer targets and the
  minimap mount survive re-renders, and the minimap label counts nodes that were
  not drawn. The keyboard active descendant and mobile board overflow are
  correct at narrow widths.

## [0.3.1] - 2026-10-17

### Fixed

- A stale `--if-version` now carries `details: {"expected", "current"}` on
  every `stale_version` error, matching `stale_rev`; the code, exit status 5
  and `retryable: false` are unchanged for a version mismatch. This includes
  the row-count conflict inside a transition, which reads the stored version
  back after the failed update; that one site also reports a row that has
  vanished in the meantime as `not_found`, exit 3, because a row that no longer
  exists is not a version conflict.
- `--description-file` reports invalid UTF-8 as exit 4 `validation` naming the
  path (or `stdin` for the `-` form), instead of an uncaught decode error and
  exit 1. It now reads through the same helper as `memo --file` (file and `-`),
  so both forms share one contract: valid UTF-8 verbatim, an unreadable path as
  exit 3, invalid UTF-8 as exit 4.

## [0.3.0] - 2026-10-17

### Added

- A board-level memo: one standing document per project board, read first and
  edited as a whole, for a project's operating premise rather than the
  per-session narrative. `tack memo` reads the head revision as JSON,
  `--rev N` reads one revision, `--history` lists them newest first (with
  `--limit`), `--diff [A [B]]` renders a line-based unified diff with three
  lines of context from the standard library, `--append TEXT` adds a line,
  `--restore N` re-applies an earlier revision as a new revision, `--clear`
  writes an empty revision, `--file PATH` / `-` replace from a file or stdin,
  and `--edit` edits in `$VISUAL`/`$EDITOR` (refusing with exit 2 when there is
  no terminal, so an unattended agent cannot block on an editor prompt).
  Revisions are immutable and append-only, the cap is 64 KiB measured in bytes,
  `--dry-run` reports a write's real outcome without performing it, and a
  guarded write against a stale revision exits 5 with code `stale_rev` and
  `retryable: false`. `--reason` is required for a replace, restore or clear
  and optional for an append.
- Schema migration 1 -> 2, adding the `memo_revisions` table. Nothing
  auto-migrates: a board created by the previous release is refused with exit 6
  until `tack migrate` runs, which writes its usual pre-migrate backup.
  `tack backup` needs no change and preserves every revision.
- `doctor` validates the memo when the schema version claims it: a memo
  revision table that is missing or unusable (`memo_table_invalid`), an
  oversize revision, a blank author, and a revision run that is not a gapless
  `1..N` are errors.
- The web view shows the memo as a panel at the top of `/board` (escaped
  inside a `<pre>`, collapsed by default when it is large, never truncated)
  and publishes it at `/api/memo`, which is exactly the `tack memo --json`
  document. There is no web write path.

### Changed

- The Host-header refusal now explains itself. A refused request still gets a
  single `403`, but the body names the received `Host` value (or says plainly
  that the header was absent or unparseable), lists the accepted host names,
  states that a `host:port` value must carry the port the server is bound to,
  and points at `--allowed-host` (a non-loopback bind also needs
  `--allow-remote`). The value is echoed `repr()`-escaped and length-capped so
  a hostile or oversized header cannot inflate the response, and `HEAD`
  refusals stay body-less as before.

## [0.2.0] - 2026-10-17

### Changed

- A claim never writes the assignment. The explicit `claim` command and the
  implicit claim taken by `status … in_progress` now agree: any agent may claim
  a task, the claim names the agent that takes it, and the assignment
  (`assignee`) is left alone — it is written only by an explicit routing
  command (`create --assignee` / `update --assignee`). A claim on a task routed
  to another agent therefore succeeds and records that routing in the claim
  event instead of being refused, the assignment-transfer `--force` case is
  retired, and `update --assignee` records the previous assignee in its event.
  The retired assignment-transfer force case also introduces one new refusal:
  `claim --force --reason` on a task assigned to another agent used to transfer
  the assignment and now exits 4 as force-not-applicable, because no force case
  applies. A plain `claim` on the same task succeeds and leaves the assignment
  in place.
- `task.status_changed` now records `routed_to` (the card's current assignee)
  when a `status … in_progress` move takes a claim on a card routed to another
  agent, exactly as the claim event does, and omits the key when the actor is
  the assignee or the card is unrouted.

### Fixed

- The reported version is single-sourced. `pyproject.toml` is now the only
  place the release number is written by hand: `tackboard.__version__` is read
  from the installed distribution metadata, with a clearly-marked fallback for
  a source tree whose metadata is absent, so `tack --version` can no longer
  drift from the packaged version. At 0.1.1 the number was bumped in
  `pyproject.toml` and `SKILL.md` but missed in `src/tackboard/__init__.py`, so
  the release reported itself as 0.1.0.

## [0.1.1] - 2026-10-16

### Added

- An `agent-report` GitHub issue template, so a tool defect found while using
  `tack` has one place to be filed.

### Fixed

- Length-limit validation errors now carry machine-readable `details` —
  `field`, `max`, `unit` and `actual` — instead of an empty object, so an agent
  can recover programmatically rather than parsing the message prose. Filed as
  issue #2, where a 200-character title limit was discoverable only by hitting
  exit 4 mid-round; the same details cover `description`, `label`, `labels`,
  `blocked_reason`, `note` and `--reason`.
- Packaging metadata was bumped to 0.1.1 in `pyproject.toml` and `SKILL.md`.
  `src/tackboard/__init__.py` was missed and still reported 0.1.0; the 0.2.0
  entry above corrects that.

## [0.1.0] - 2026-10-16

First release. `tackboard` is a project-scoped task board for coordinating
several agents on one codebase: a stable `tack` CLI over a single SQLite
database per project, a versioned JSON contract, readiness derived from a
dependency DAG, single-holder claims with an atomic "claim the next ready
task" selector, and a read-only board and dependency-graph web view for the
operator. The sections below describe this first-release surface in full and
the packaging work that makes the distributions installable.

### Added

- Repository scaffold: package layout, packaging metadata, MIT license, CI.
- Domain core: configuration and project resolution, connection/transaction
  handling, schema with migrations, backup and diagnostics, task models, the
  service layer and its single transition function, dependency DAG handling,
  claims with the atomic `next --claim` selector, and append-only history.
- The `tack` command-line interface: every command except the browser server,
  global options accepted before or after the subcommand, versioned JSON
  documents on stdout, human renderings of the same data, and the documented
  exit-code contract.
- `history.list_notes`, so `show` can display a task's latest notes without
  SQL outside the service layer.
- Operator documentation in `README.md` (installation, project selection, CLI
  examples, status/dependency/claim semantics, the agent quickstart snippet,
  backup and troubleshooting) and contributor documentation in `DESIGN.md`.
- Test coverage for the CLI contract: filters, counts, JSON documents and the
  error envelope, actor resolution, the exit-code matrix, an end-to-end
  lifecycle, and the packaging (console script and `python -m`) paths.
- The read-only web view and the `tack serve` command: a Kanban board with
  filters, counts and a pausable auto-refresh; a dependency graph drawn by
  hand-written SVG with a server-rendered indented outline for browsers without
  JavaScript; and a task detail page. `GET` and `HEAD` only, Host-header
  validation, `Content-Security-Policy` and `nosniff` on every response, and
  `PRAGMA query_only` connections to the selected database only.
- `tackboard.views`, the interface-neutral read models (`board_document`,
  `graph_document`, `task_document`) that the CLI and the web layer both use, so
  `/api/board`, `/api/graph` and `/api/tasks/<id>` are the CLI's own documents.
- Test coverage for the web area: two-database isolation, JSON parity with the
  CLI, filters and counts, method rejection, Host validation, escaping, an empty
  database, a 1,000-task graph, the no-JavaScript fallback, and the single
  `serve --json` startup record.
- `SKILL.md`: the agent-facing usage guide — a flat command reference, the
  versioned JSON and exit-code contracts, the claim/work/complete loop, and the
  best practices for claiming, depending on, and completing work from a CLI.
- Field limits documented in `SKILL.md` and `README.md`: every write-time limit
  in one table (`title` 200 characters, `description` 64 KiB, a label 50
  characters and 20 labels per task, `blocked_reason` and note text 2 KiB,
  `--reason` 1024 characters, agent-id shape), so a caller can size a value
  before writing it instead of discovering the limit on exit 4.
- Distributions on PyPI as `tackboard` (`uv tool install tackboard`,
  `uvx tackboard --help`, or `pip install tackboard`). The wheel and the sdist
  both carry the web templates and static assets and the MIT license; the sdist
  also carries the README and the project metadata.

### Fixed

- `depend remove` no longer advertises `--force`/`--reason`. Removing a
  dependency is not one of the enumerated `--force` cases (spec 6.2), so the
  flag is now rejected as a usage error (exit 2), exactly as `update` already
  rejects it. `--agent` and `--if-version` are unchanged.
- `init` on an existing database now refuses an older schema version with the
  same error `open_database` uses (`Run 'tack migrate'`, exit 6) instead of
  silently creating current tables, re-seeding metadata and stamping a new
  schema version. `init` is strictly idempotent-or-refuse again, and `migrate`
  remains the only command that changes a schema version (spec 5.4).
- A forced start into `in_progress` now covers a *cancelled* prerequisite as
  well as an unfinished one. `tack claim --force --reason` and
  `tack status … in_progress --force --reason` consult one shared force
  predicate, so the claim/status policy (spec 8.2 row 1, spec 6.2 case 2) is
  enforced identically from every entry point.
- Starting a task for which the actor already holds a claim — for example
  beginning work on a `review` task the actor claimed — no longer rewrites the
  claim's `claimed_at`. "If already held by actor, keep the claim" now keeps
  the claim as it stands (spec 8.2).
- Declared Python support is consistent: the package requires Python 3.12 and
  the `Programming Language :: Python` classifier no longer advertises 3.11.
