# Security model

`tackboard` is a local coordination tool for agents working on one project. It
is not a multi-tenant service and it has no authentication layer. This document
states what that means in practice, what the code does enforce, and which
exposure is deliberately left to the operator.

The short version: **the board prevents accidents, not malice.** Anyone who can
run the `tack` command against the database can act as any agent and change any
task. Run it on a machine and a filesystem you already trust.

## Advisory agent identifiers

`--agent AGENT_ID` (or `TACKBOARD_AGENT`) is a label. It is not a credential,
not a login and not checked against a user directory. Every task or memo
mutation requires one, and the value is recorded in the event log and on the
claim, but the board cannot prove that the process using a label is the agent
that label names.

Consequences:

- There is no authorization. An agent that can run the CLI can claim, complete,
  re-route or cancel another agent's task. The single-holder claim is the only
  mutual-exclusion record, and it prevents two processes from taking the same
  task at the same time; it does not prevent a hostile process from taking a
  task by design.
- `--force --reason` is audited, not authorized. The enumerated force cases
  record the reason and the affected agent in history; they do not check the
  caller's right to use them. The audit trail makes an action visible after the
  fact, which is what it is for.
- Two agents sharing one label are one identity to the board. Their claims
  collide and their history is attributed to the same name. Use one label per
  agent process or session when the audit matters.
- The event log is the accountability surface. `tack history --json` and
  `tack history --follow --json` expose every mutation with its actor; read it
  when two agents disagree about who did what.
- A board file is plaintext. Titles, descriptions, notes and the memo are
  readable by anyone who can read the SQLite file. File permissions are the
  access control; there is no encryption at rest.

If more than a trusted local team can reach the board, the board is the wrong
place to put the secret. Keep secrets out of titles, descriptions and notes.

## The web view

`tack serve` starts a **read-only** HTTP view of one board. Its defaults and
enforced behaviour:

- **Loopback bind.** The default host is `127.0.0.1` and the default port is
  `8765`. The server is unauthenticated; there is no login, token or cookie
  check.
- **Read-only methods.** `GET` and `HEAD` are the only accepted methods.
  Everything else receives `405`, including `POST`, `PUT` and `DELETE`. There
  is no web mutation path: the web layer reads the same service/read models as
  the CLI and cannot write.
- **Host validation.** Every request's parsed `Host` header is validated —
  hostname and port — against an allowlist before routing. This is the
  DNS-rebinding guard. A loopback bind accepts the loopback names plus its own
  bind host; a request whose `Host` does not match is refused with `403` and a
  bounded, escaped explanation. A malformed or absent header is refused too.
- **Response hardening.** Responses carry
  `Content-Security-Policy: default-src 'self'` and
  `X-Content-Type-Options: nosniff`. The page ships no inline script or style,
  no external host, no web font and no build step. Connections are opened per
  request with `PRAGMA query_only=ON`, against the one database the CLI
  selected.
- **No remote bind by default.** A non-loopback `--host` is refused unless
  `--allow-remote` is passed, and `--allow-remote` also requires at least one
  explicit `--allowed-host`. That combination removes the loopback protection
  and does not add authentication: it is for a trusted network only. Do not
  expose the port to the internet. If remote access is needed, put an
  authenticating reverse proxy in front of it and bind the server to loopback.

The web view is a human operator's window. Agents should use the CLI's JSON
contract, not scrape the HTML or call the read-only API as a write path (there
is none).

## Untrusted text

Titles, descriptions, labels, note text, typed ref values, agent IDs, the board
memo and event payloads are treated as plain text. The board never executes
them, evaluates them or interprets them as a template.

- The server renders text through `html.escape` (quoted attribute escaping
  included) or `json.dumps`. It never concatenates raw text into an HTML string
  or a JavaScript literal.
- The in-house Markdown renderer used for task descriptions and notes builds DOM
  nodes with `createElement` and `textContent`; it does not use `innerHTML`.
  Its supported subset is paragraphs, headings, lists, fenced and inline code,
  bold, italic and links. A link destination must be an absolute `http(s)` URL;
  every other destination is rendered as plain text so the reader still sees
  where it pointed. The server keeps a `<pre>` fallback and the renderer replaces
  it only after a successful build.
- Typed note refs are validated on write: `commit` (hex object id), `url`
  (absolute http(s), length-capped), `path` (repository-relative, no absolute
  path or `..` segment) and `task` (an existing task on the same board). They
  are event-only and are never followed or fetched by the tool.
- The JSON contract is UTF-8 and carries no color codes. With `--json`, an
  error document goes to stdout and stderr stays empty unless `--verbose`;
  tracebacks appear only under `--verbose`.

A consumer must still treat the documents as untrusted input when it renders
them. The escaping above protects the web view; it does not make a task title
safe to paste into a shell, a SQL string or another template.

## Network filesystems

Do not put the board on a network or shared filesystem. SQLite's WAL locking is
unsafe there: the `-wal` and `-shm` coordination assumes local file semantics,
and a network mount can lose writes or corrupt the database under concurrent
agents.

`tack doctor` warns with `network_filesystem` when the database file sits on a
known network or shared mount: `nfs`, `nfs4`, `cifs`, `smbfs`, `smb3`,
`sshfs`/`fuse.sshfs`, `fuse.rclone`, `glusterfs`, `ceph`, `afs`, `9p`, `davfs`
and `lustre`. The warning carries the `fstype` and `mount_point`; detection that
cannot read the mount table stays silent rather than guessing. A warning never
changes the exit code.

The same rule applies to backups and snapshots: `tack backup PATH` writes a
consistent copy through SQLite's online backup API. Copying a live database
with `cp` can capture a torn WAL state and is not a backup. Keep the board and
its backups on a local disk; if the backup target is a network share, write the
snapshot locally first and copy the finished file.

## Reporting a problem

Security-relevant defects should be reported privately to the maintainers
rather than opened as a public issue with an exploit. The repository's
`agent-report` issue template is for tool defects found in normal use, not for
undisclosed vulnerabilities.
