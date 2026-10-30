A note event's payload is `{text, refs}`; `refs` is always an array (empty
`[]` when the note carried none, including notes written before refs existed —
the stored row is never rewritten). Each entry is `{kind, value}` with `kind`
one of `commit`, `url`, `path`, `task`. The same array appears in `show`'s
`notes[]` and in the `note` events of `history`, so a consumer reads one shape.
Refs are event-only: no table, no column, no migration, and they never count
toward the 2 KiB note text cap.

`tack note T-000001 "text" --ref commit:1a2b3c4 --ref path:src/parser.py` is how
an agent hands the next one the evidence instead of a description of it. A ref
is a pointer, not a claim: `task` must name an existing task on this board,
`commit` must be a hexadecimal git object id, `url` an absolute `http`/`https`
URL, and `path` a repository-relative path. Field limits and the per-kind
grammar are in `tack guide --topic documents`.
