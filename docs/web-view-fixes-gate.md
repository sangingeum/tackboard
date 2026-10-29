# Web view fix rounds 1-3 — verification gate

Verified tip: `7a3d7ef0d591cf6df53a876853956625ecf77b5e` (`test: pin the stale claim canvas wiring and dashed badge`), `main`, tree clean.

Commit range covered:

- Round 1: `b6ab065` (uniform rectangular cards) and `29fb920` (shape-helper cleanup).
- Round 2: `b15f830` (toggle state, minimap control row, unbroken IDs, section rhythm, glyph-map cleanup).
- Round 3: `774fb53` (reviewable marker naming) and `f113d13`, `7a3d7ef` (stale claims, assignee history values, tests). The `88b8d98..HEAD` range also contains unrelated feature commits; the Round 3 commits were reviewed directly.

All commands below were run from the repository root in this session. CDP measurements used headless Chrome 155 against `tack serve --port 8765` on a throwaway board under the system temp directory.

## Fix items

| Item | Result | Evidence | Accept criterion |
|---|---|---|---|
| 1. Holder badge does not cover status | PASS | Claimed `T-000002` badge box `x=30 y=56 w=142 h=13`; status text box `x=10 y=39.97 w=79.72 h=13.04`. Intersection false; badge/title intersection also false. Re-measured after `Fit all`; still false for every graph node. | Full status label and full badge visible at 100% and after Fit all. |
| 2. Text contained in node | PASS | 8 graph nodes at `Fit all`; each card `176x72`; id/title/status/glyph boxes all remained inside the card (`outside=0` for every node). | No text crosses a node outline for any status. |
| 3. Edges do not paint through text | PASS | Viewport child order: 4 `g.edge` groups before 7 visible `g.node` groups (`lastEdge=3`, `firstNode=4`). Card fills are opaque (`rgb(232,241,248)`, `rgb(251,241,226)`, `rgb(242,236,250)`, `rgb(255,255,255)`). | No edge visible across node text. |
| 4. `+N` elision marker anchor | PASS | A done prerequisite (`T-000005`) was hidden with `h`; its visible dependent `T-000006` rendered `+1` at `x=148 y=6 w=22 h=13` (fixed top-right). Overlap with glyph/id/title: false/false/false. | Fixed anchor, never overlaps text, stays attached. |
| 5. Toggle pressed state | PASS | `Highlight claimed` at `aria-pressed=true`: 2 px border, font-weight 700; at `false`: 1 px, 500. `Focus claimed` changed 1 px/500 → 2 px/700 when pressed. `Hide done & cancelled` changed the same way. `aria-pressed` wiring preserved. | Visible on-state not by colour alone; off-state distinct. |
| 6. Minimap button does not cover drawing | PASS | Expanded frame `200x140` at `(536, 684.5)`; drawing `198x94` at `y=685.5-779.5`; toggle `198x44` at `y=779.5-823.5`. Overlap false, button inside frame true. `aria-expanded`/`aria-controls` intact. | Control fits without covering minimap content. |
| 7. Task IDs do not break mid-ID | PASS | Blocked sentence renders `<span class="nowrap">T-00000N</span>`; computed `white-space: nowrap`. With the card forced to 120 px, the ID text node still returned one client rect (`x=41 y=568.5 w=56.4`) and the blocked sentence stayed on one line. | Card wraps before the ID, never inside it. |
| 8. Prerequisites/Dependents rhythm | PASS | Task `T-000007` has both sections. Prerequisites link group bottom `848.6`; Dependents top `864.6`; gap `16.0` px, matching the panel rhythm. | Same spacing as other sections. |
| 9. Reviewable marker named consistently | PASS | Legend entry `reviewable — double top edge`; node aria-label `T-000003, Gate gamma, review, reviewable, priority normal, assignee <holder>`; node title contains `(review, reviewable)`; outline contains `◐ reviewable`. Marker is a line at `y=3` across `w=148`, stroke `rgb(95,63,138)` at `2 px`. | One concept, one name in legend, aria-label and outline. |
| 10. Blocked hatch claim matches pixels | PASS | Production blocked card `T-000004` has `stroke-width: 3px`; README says the drawn cue is a `3 px border` plus `· blocked`; design-system text corrected; CHANGELOG records that the hatch claim was removed. No production hatch CSS remains. | Hatch drawn or docs corrected. |
| 11. Assignee history carries both values | PASS | `tack update T-000001 --assignee <agent>` produced event payload `{"assignee":"<agent>","fields":["assignee"],"previous_assignee":null}` in both CLI history and the task API. Task page timeline renders `assignee: unassigned → <agent>`. CHANGELOG names the additive contract change. `schema_version` remains `1`; `f113d13^..7a3d7ef` changes only this payload plus web rendering/tests, not another event shape. | Previous and new values, `previous -> new` timeline, named contract change, schema 1. |
| 12. Stale claims marked distinctly | PASS | Backdated active claim to 3 h: `tack claims --stale` lists `T-000002`; board card reads `@<holder> 3h ⚠ stale`; graph badge reads `@<holder> · 3h · stale` with `stroke-dasharray: 3px, 2px` and `stroke-width: 2px`; task page shows `⚠ stale claim` chip with dashed border; outline reads `[claimed by <holder> ... — stale]`. Web reads the server-derived flag; threshold comes from `claims.stale_claim_seconds()` via `service._configured_stale_seconds()`, with no second hard-coded value in `web.py`. | Distinct on board card, graph badge and task page; one threshold. |

## Do-not-regress constraints

| Constraint | Result | Evidence |
|---|---|---|
| 1. Read-only web view | PASS | `POST /` → `405`; `PUT /` → `405`; `DELETE /` → `405`; `HEAD /` → `200 OK`. |
| 2. CSP unchanged | PASS | Response header `Content-Security-Policy: default-src 'self'`. Board DOM: 0 inline `<script>`, 0 `<style>`, 0 `style=` attributes. External references were only local `/static/theme.js`, `/static/app.css`, `/static/keys.js`, `/static/board.js` (and graph equivalents). No external host, vendored library or build step. |
| 3. JS-disabled outline intact | PASS | Server-rendered `/graph` contains `<details class="graph-fallback">` with 8 `dep-line` items for 8 tasks and 5 dependency statements (`T-000004` has two, `T-000006`/`T-000007`/`T-000008` one each). Each line links to its `/task/<id>` page. |
| 4. Keyboard, skip link, single-tab-stop canvas | PASS | First `Tab` focused `a.skip-link` with `href="#main-content"`. `#graph-svg` has `tabindex="0"`, `role="group"`, and an aria-label describing the graph. Clicking `T-000003` set `aria-activedescendant="node-T-000003"`; `ArrowLeft` from `T-000004` moved the active node to `T-000001`; `?` opened the help dialog; `c` and `h` toggled their controls. |
| 5. `/api/*` documents identical to CLI | **FAIL** | `/api/board` is byte-identical to `tack board --json`. `/api/graph` adds `blocked`, `dependency_ready`, `eligible` and `reviewable` to every node versus `tack graph --json`. `/api/tasks/T-000002` adds `claim`, `prerequisites`, `dependents` and `history` versus `tack show --json`. The history event payload itself matches CLI history, but the envelopes differ (`history` versus `items`) and there is no `/api/history` endpoint. The same divergence exists at base `88b8d98`, so it was not introduced by Rounds 1-3, but the bar's absolute identity requirement is not met. |
| 6. Tests added for behaviour changes | PASS | `uv run pytest -W error -q` → `447 passed in 71.19s`; `uvx ruff check src tests` → `All checks passed!`; `uv run mypy src` → `Success: no issues found in 16 source files`. Relevant cases include `tests/test_web.py::test_graph_legend_uses_the_drawn_status_glyphs`, `test_graph_legend_names_the_reviewable_marker`, `test_dependency_outline_marks_reviewable_nodes`, `test_double_edge_uses_the_reviewable_token`, `test_graph_js_wires_the_stale_claim_badge`, `test_stale_claim_badge_uses_a_dashed_mark`, `test_assignment_history_names_both_sides`, `test_stale_claim_is_marked_on_every_web_surface`, `test_status_glyph_maps_agree`, `test_blocked_card_wraps_before_the_task_id`, `test_graph_page_exposes_the_canvas_toolbar_selection_and_minimap`, `test_hide_closed_filters_the_outline_and_keeps_derived_flags`; `tests/test_service.py` asserts both assignee values including clearing. |
| 7. Status distinguishable beyond colour | PASS | Legend lists six statuses with glyph and label (`…`, `▷`, `▶`, `◐`, `✓`, `✕`); graph nodes carry the same glyph plus a text status line (`ready`, `in progress`, `backlog · blocked`, etc.); backlog uses `stroke-dasharray: 6 4`, cancelled `2 3`; README now states only those two differ by border style. |

## Round 3-specific checks

- Reviewable marker: named consistently in legend, node aria-label/title and outline (see item 9). PASS.
- Blocked hatch: production docs corrected and the visible cue is the 3 px border (see item 10). PASS.
- Assignee history: both values in the payload, task API and web timeline; changelog names the contract change; schema stays `1`; no other event shape moved in the Round 3 commits. PASS.
- Stale claims: distinct word/mark on board card, graph badge, task page and outline; one threshold source. PASS.

## Gaps

1. **API identity (constraint 5) is not met in the current tree.** The graph and task HTTP documents are deliberately richer than the CLI documents, and that predates these rounds. It is reported as a constraint failure rather than a fix-round regression.
2. **No `/api/history` endpoint.** History was compared through the task document's embedded `history` list; event payloads match CLI history, envelopes do not.
3. **Static design prototype still shows a hatch.** `docs/web-redesign/prototype.html`, `prototype.css` and `prototype.js` retain a hatch pattern as a mock artifact. Production README and design-system text are corrected; the mock is not the production claim, but it can still confuse a reader.
4. **Measurement class.** All checks are DOM, computed-style and bounding-box measurements. No pixel-level contrast audit or real screen-reader session was run.
5. **Threshold override path not exercised.** The default `2h` threshold was exercised by backdating to `3h`; a board-local `[tackboard] stale_claim_after` override was not.
6. **Keyboard pass is representative.** Skip link, tab stop, arrow movement, `?`, `c` and `h` were exercised; not every shortcut listed in the help dialog was driven.
7. **Assignee transition coverage.** Live CDP covered `null → <agent>`; clearing (`<agent> → null`) is covered by unit tests, not by a live page measurement.
