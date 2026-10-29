# Stale-claim visibility — QA gate report

Gate for the stale-claim visibility round. Reviewed tip: `<SHA>` (filled at
completion). Bars are ruled PASS / FAIL / BLOCKED with the command run and the
observed result. A FAIL blocks the version bump.

Status: SKELETON — bars being filled in. Do not treat as a verdict yet.

## Bars of record

| # | Bar | Ruling | Evidence |
|---|-----|--------|----------|
| 1 | Suite warn-clean (`pytest -q -W error`) | pending | |
| 2 | Lint and types (ruff, mypy) | pending | |
| 3 | Derived fields end to end | pending | |
| 4 | `claims --stale` contract | pending | |
| 5 | Config threshold (cwd-relative) | pending | |
| 6 | `doctor` warning | pending | |
| 7 | `recover` | pending | |
| 8 | No migration | pending | |
| 9 | Docs match behaviour | pending | |

## Confirmation of the review's fixes

1. One clock for `claims` documents — pending.
2. `recover` refusal wording and exit code — pending.
3. README cwd-relative threshold paragraph — pending.

## Open findings from the review

| # | Finding | Ruling | Reasoning |
|---|---------|--------|-----------|
| 4 | Three copies of the age arithmetic | pending | |
| 5 | Threshold computed in unrelated commands | pending | |
| 6 | Same-agent refusal message unpinned | pending | |

## Overall verdict

Pending.
