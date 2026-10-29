# Stale-claim visibility — two-axis review

Board card T-000060. Reviewing the round landed on `main` at `8fc00ca`
(`f42bde4` feature, `ad37aef` snapshot freezing, `8fc00ca` docs). Scope of this
review: the spots named on the card — `service.derived_flags`, the `--stale`
flag and threshold resolution in `claims.py`/`cli.py`, the `[tackboard]
stale_claim_after` read in `config.py`, the `doctor` stale warning in
`schema.py`, the `recover` wrapper, and the tests that pin those contracts.
Web templates and the roadmap document are out of scope by the card's own
instruction. The verification bars (403 tests under `-W error`, ruff, mypy,
end-to-end smoke) are of record from the butler; they were not re-run.

## Verdict

- Correctness/contract axis: **SKELETON — pending**
- House-style/terminology axis: **SKELETON — pending**

## Findings

SKELETON — pending. Each finding will carry file:line and severity.

## What I fixed myself

SKELETON — pending.

## Scope I did not audit

SKELETON — pending.
