`doctor --json` returns
`{"schema_version":1,"ok":<bool>,"errors":[...],"warnings":[...]}`:

- `errors` lists problems that make the board unusable — a missing database, a
  wrong application id, an unsupported schema version, broken foreign keys, a
  dangling dependency, a cycle, corrupt metadata. A non-empty `errors` exits
  **6**.
- `warnings` lists inconsistencies that are real but not fatal — an
  `in_progress` task whose prerequisite is unfinished, an `in_progress` task
  with no claim. Warnings still exit **0**.
- `ok` is `not errors`. **`ok:true` does NOT mean the board is clean**: it only
  means no errors were found, and it is emitted alongside a non-empty
  `warnings` array. The health signal is the `warnings` array (and `errors`) —
  not `ok`, and not the exit code. Read `warnings` on every run.
- Every entry of `errors` and `warnings` carries `code` (the machine-readable
  kind) and `message` (the human-readable text); beyond those two an entry
  carries only the keys its own kind needs. The claim and started-dependent
  checks name the task in `task_id` (`claim_on_inactive_task`,
  `in_progress_without_claim`), and `stale_claim` adds `agent_id` and
  `age_seconds` for the claim that has aged past the configured threshold.
  `inconsistent_blocked_state` adds
  `prerequisite_id` for the unfinished prerequisite. A `schema_version` error
  adds `found` / `supported`; a `dependency_cycle` adds `path`; a foreign-key
  violation adds `table` / `rowid` — including `dangling_dependency`, whose
  entry concerns a task row but names it by `table` / `rowid`, not `task_id`.
- `doctor` also emits a `network_filesystem` warning when the database sits on a
  network/shared mount (NFS, CIFS/SMB, SSHFS, rclone, GlusterFS, Ceph, AFS, 9p,
  davfs, Lustre) where SQLite's WAL locking is unsafe. It carries `fstype` and
  `mount_point`; detection that cannot read the mount table stays silent. Move
  the board to a local disk; a warning never changes the exit code.
- `doctor` warns about event rows the CLI would never write — a finding means
  corruption or an out-of-band write, and every one of these is a warning:
  - `note_refs_exceeded` / `note_ref_invalid` / `note_ref_missing_task` /
    `note_payload_invalid` — a stored note's `refs` break the documented kind
    limits or the 20-ref cap, or name a task that is not on the board. Findings
    carry `task_id`, `event_id`, and `kind` / `value` / `limit` where they apply.
  - `memo_event_missing_revision` — a `memo.*` event names a `rev` that is not
    in `memo_revisions`; `memo_event_rev_mismatch` — the newest memo event does
    not name the current revision (`expected` / `found`); `memo_event_invalid` —
    an event carries no revision.
  - `plan_event_missing_task` / `plan_event_invalid` — a `plan.applied` event's
    `ids` name tasks that do not exist, or the event carries no id list.
- The `stale_claim` warning is the derived `claim_stale` flag — the same one the
  task projection and `claims --stale` read — against the configured
  `stale_claim_after` threshold.
