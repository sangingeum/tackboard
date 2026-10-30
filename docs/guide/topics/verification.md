Prove the board works end to end — every step's exit code should be `0`:

```bash
tack init --db /tmp/board.sqlite3
export TACKBOARD_DB=/tmp/board.sqlite3 TACKBOARD_AGENT=check
tack create "Prerequisite" --status ready --json          # -> T-000001
tack create "Dependent" --status ready --depends-on T-000001 --json
tack claim T-000001 --json                                # held by check
tack complete T-000001 --json                             # -> changed:true
tack show T-000002 --json                                 # dependency_ready:true
tack next --claim --json                                  # -> claims T-000002
tack board --brief                                        # counts in one line
tack doctor --json                                        # ok:true, no warnings
```

If `claim` on the dependent succeeds only after the prerequisite is `done`, and
`doctor` reports a clean board, the readiness, dependency and claim machinery is
all working.
