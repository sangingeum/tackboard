# Event feed, agent summary and network warning — review + gate

Combined two-axis review and QA gate of the three commits landed over the 0.5.0
release (`d8405b4`): a resumable event feed in `history`, a derived per-agent
activity summary, and a `doctor` warning for a board on a network filesystem.
One report, both axes, one verdict.

- Baseline: `main` at `0d9d16e`, clean, pushed to `origin`.
- Scope: `c1accc1`, `df6c7eb`, `0d9d16e` (9 files, +597/−15).
- Round contract: additive only — the plain `tack history` document and its
  ordering must be byte-identical in shape to 0.5.0; no schema migration; no
  version bump.

## Verdict

**PENDING — filled at the tip.**

## Gate bars

| # | Bar | Verdict | Command and observed result |
|---|---|---|---|
| 1 | Suite warn-clean at the tip | PENDING | |
| 2 | Ruff and mypy clean | PENDING | |
| 3 | Backward compatibility of plain `history` | PENDING | |
| 4 | Cursor contract (`--since`) | PENDING | |
| 5 | `--follow` is honest | PENDING | |
| 6 | `agents --json` is derived | PENDING | |
| 7 | `doctor` network warning | PENDING | |
| 8 | No schema migration | PENDING | |
| 9 | Docs match behaviour | PENDING | |
| 10 | No version bump | PENDING | |

## Two-axis review

### Axis 1 — correctness and design

PENDING.

### Axis 2 — contract, docs and tests

PENDING.

## Follow-ups

PENDING.
