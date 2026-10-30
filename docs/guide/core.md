tack — a project-scoped task board for coordinating agents

Setup
  tack init --db PATH                  create the board (creates parent dirs)
  export TACKBOARD_DB=PATH             or pass --db PATH on every command
  export TACKBOARD_AGENT=my-agent      every task or memo mutation needs an actor
  tack guide                           print this summary
  tack guide --topics                  list the reference topics
  tack guide --topic NAME              print one topic (e.g. commands)

The work loop
  tack memo --json                     read the project memo before you start
  tack next --claim --json             take the top eligible task, atomically
  tack next --stage review --claim     take a task waiting for review
  tack show T-000001 --json            read one task in full
  tack note T-000001 "progress note"   append a handoff note
  tack complete T-000001 --json        finish it
  tack reopen T-000001 --json          put it back to ready

Reading the board
  tack board --json                    every task, for machines
  tack board --brief                   one line of counts
  tack board --count                   counts, one key=value per line
  tack board --eligible --limit 5      only work an agent may claim now
  tack list --assignee my-agent        filter by assignee
  tack list --label core --full        --full adds the description
  tack list --blocked                  only tasks with derived blockers

Dependencies
  tack create "Ship the parser" --depends-on T-000001
  tack depend add T-000002 --on T-000001
  tack deps T-000002                   direct prerequisites
  tack dependents T-000001             direct dependents
  tack graph --format dot              whole graph; --format json for machines

Plans
  tack plan apply plan.json --json     create a whole task DAG atomically
  tack plan apply plan.json --dry-run  validate and report; writes nothing

JSON
  --json works before or after the subcommand and prints exactly one JSON
  document on stdout, always carrying "schema_version": 1. --full adds
  descriptions to board, list and next. With --json, errors are printed to
  stdout as {"schema_version":1,"error":{...}} and stderr stays empty unless
  --verbose is given; without --json, errors go to stderr.
  The one exception: tack history --follow streams NDJSON events (one JSON
  object per line) and polls every 0.5 s.
  tack schema --json                   list the published JSON Schemas
  tack schema show --json              print one schema document

Exit codes
  0 success, including no-ops and an empty next
  1 internal error              4 validation, invalid transition, or cycle
  2 usage error, bad option     5 concurrency conflict: claim held by another
    combination, or no actor      agent, stale version, lock timeout
  3 task, project, or database  6 database, schema, or migration error
    not found / not configured
  Exit 5 is not one thing: under --json read error.retryable. locked is
  retryable; claim_conflict, stale_version and stale_rev are not.

Status and readiness
  Statuses: backlog, ready, in_progress, review, done, cancelled.
  Readiness is derived, never stored: a task is eligible when its status is
  ready, every prerequisite is done, it has no explicit blocked reason, and
  nobody holds a claim. --force --reason TEXT is allowed only for the audited
  cases — taking over a claim, an unfinished prerequisite, adding one to active
  work, recovering a lost claim, replacing a memo without `--if-rev` —
  exhaustive, not a general bypass.

Stale claims
  tack claims                          active claims, each with its age
  tack claims --stale                  only claims older than 2h by default
  tack recover T-000001 --reason TEXT  release a dead claim; audited
  The default threshold is the optional [tackboard] stale_claim_after key in
  .tackboard.toml (for example "45m"); --older-than overrides it per call.
  Every next document carries stale_claims; when it is above zero, run
  tack claims --stale and decide before starting new work.
