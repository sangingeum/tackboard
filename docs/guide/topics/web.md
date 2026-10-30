`tack serve` starts a read-only browser view of one board: Kanban columns per
stored status with the memo at the top, a dependency graph, and per-task detail
pages. It binds to loopback by default, answers only `GET` and `HEAD` (405
otherwise), sends `Content-Security-Policy: default-src 'self'`, and ships no
inline script or style and no external host — safe to leave open for a human to
watch.

- `/board` — status columns, live counts and filters; a claimed card names the
  holder and the claim age, and a stale claim appends `⚠ stale`.
- `/graph` — the full DAG. Status is carried by a corner glyph and words, never
  colour alone; `blocked` and `reviewable` are overlays on the stored status.
  "Hide done & cancelled" counts elided edges as `+N` markers and "Focus
  claimed" dims the rest without hiding it; `?hide_closed=1` / `0` overrides
  the `localStorage` preference.
- `/task/<id>` — metadata, derived readiness, prerequisites and dependents with
  live statuses, the claim holder and its age, blockers, the description
  rendered as Markdown, and a newest-first timeline of notes and history.
- `/api/*` — the JSON documents the web view renders. Each is a **superset** of
  its CLI counterpart: every CLI key is present with the same value, and the
  derived readiness flags a browser needs are added on top. The rule, the
  per-route keys and the schema obligation are in `docs/api-contract.md`.

Nothing in the web view mutates: every change goes through the CLI so it lands
as an audited event. `--allow-remote` and `--allowed-host` exist for a
deliberate exception; a loopback board never needs them.
