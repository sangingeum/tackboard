# tackboard-mcp — an optional MCP adapter

A small [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes one `tack` project board to an MCP client (an IDE assistant, a chat
client, another agent runtime).

**This package is optional and outside the core.** The `tackboard` package —
the CLI, the database, the JSON contracts — stays dependency-free and does not
import anything here. If you only want the board, install `tackboard`; if your
agent runtime speaks MCP and you would rather it called tools than assembled
shell commands, install this beside it.

## What it is, precisely

The adapter contains **no domain logic**. Every tool shells out to the `tack`
CLI with `--json` and returns that document unchanged, so a client can never
see a claim, a transition or a validation rule that differs from the board. A
non-zero exit is raised as an error whose payload is the CLI's own error
document (`{"schema_version":1,"error":{...}}`).

The two exceptions are `board_info` and `board_attach`: no single CLI call
answers "which board is this process serving?" or "switch to this board", so
those two return adapter-owned documents (both carry a `document` key). They
still delegate every fact to the CLI — `board_info` reads `tack board --brief
--json`, and `board_attach` validates with `tack doctor` before it swaps.

What it adds is discovery and a typed surface: fifteen named tools with
described arguments, instead of a CLI string to compose.

## Install

```bash
uv tool install tackboard-mcp      # or: pip install tackboard-mcp
```

This installs the adapter and pulls `tackboard` with it, so the `tack` CLI
comes along in the same environment. The MCP framework and the core are this
package's dependencies, and they are declared here — never in the core.

`TACK_CMD` remains available for unusual environments where the adapter and the
CLI live in separate environments; set it to the command that invokes `tack`
(for example `TACK_CMD="uv run tack"`).

## Configure

The server reads four environment variables:

| Variable | Meaning |
|---|---|
| `TACKBOARD_DB` | the board file to operate on (same variable the CLI reads) |
| `TACKBOARD_AGENT` | the actor every mutation is attributed to |
| `TACK_CMD` | overrides the CLI command prefix; defaults to the installed core module, or `tack` when the core is not importable |
| `TACKBOARD_MCP_ALLOW_ADMIN` | enables `board_create` and `board_attach`; only `1`, `true` or `yes` enable, everything else disables |

The same gate is available as the start-up flag `--allow-board-admin`. It is
read once at start-up and never changes; without it the two administrative
tools refuse with a `board_admin_disabled` document naming the flag.

```bash
TACKBOARD_DB=~/.local/state/board.sqlite3 TACKBOARD_AGENT=assistant tackboard-mcp
```

The process speaks MCP over stdio. Register it with your client the way that
client documents stdio servers:

```json
{
  "mcpServers": {
    "tackboard": {
      "command": "tackboard-mcp",
      "env": {
        "TACKBOARD_DB": "/path/to/board.sqlite3",
        "TACKBOARD_AGENT": "assistant"
      }
    }
  }
}
```

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `memo_read` | `rev?` | the memo document, current or one stored revision |
| `next_claim` | `stage?` (`work`/`review`) | the `next --claim` document; `task` is `null` when nothing is eligible |
| `claim` | `task_id`, `force?`, `reason?` | the mutation document; `force` requires a non-empty reason |
| `status` | `task_id`, `status`, `force?`, `reason?`, `if_version?` | the mutation document |
| `reject` | `task_id`, `reason`, `if_version?` | the mutation document; the claim returns to the author |
| `list` | `status?`, `assignee?`, `label?`, `priority?`, `blocked?`, `eligible?`, `limit?`, `full?` | the `list` document; descriptions only with `full` |
| `show` | `task_id` | the `show` document: the task and its notes |
| `note` | `task_id`, `text`, `refs?` (`KIND:VALUE`) | the `note` document with the appended event |
| `complete` | `task_id` | the mutation document; completing unblocks dependents |
| `release` | `task_id`, `reason?` | the mutation document; a reason is the audited force path |
| `create` | `title`, `description?`, `priority?`, `assignee?`, `labels?`, `depends_on?`, `status?` | the created task document |
| `board_brief` | — | the counts-only board document |
| `board_info` | — | the adapter's `board_info` document: the active board, its path, whether it exists, and warnings |
| `board_create` | `path` (absolute) | the `tack init` document; creates the board but does not attach it |
| `board_attach` | `path` (absolute, existing) | the adapter's `board_attach` document; every later call uses the new board |

`board_create` and `board_attach` are administrative and disabled unless the
server was started with `--allow-board-admin` or `TACKBOARD_MCP_ALLOW_ADMIN=1`.
Both accept absolute paths only; a relative path is refused with a
`board_path_invalid` document. `board_create` delegates to `tack init --db
PATH --json`, so missing parent directories are created, and it does **not**
change the active board. `board_attach` validates the target with `tack doctor`
and reads its project label before swapping, so a failed validation leaves the
old board active. `tack doctor` opens the target read-write and may create
`-wal`/`-shm` files beside it.

There is no `board_delete`: deletion stays a CLI/Core concern. `board_info`
reports a missing or unconfigured board as a warning (`attached: false`) rather
than an error; a board that exists but cannot be read (corrupt, foreign,
permissions) is raised with the CLI's document. In discovery mode the board is
resolved from the server's working directory, and `board_info` warns about that
dependency — set `TACKBOARD_DB` to pin it.

Every tool declares all four MCP annotation hints as explicit booleans. The
five read tools (`memo_read`, `list`, `show`, `board_brief`, `board_info`) are
read-only and repeatable; every mutating tool writes only the one local board
and touches no external system (`openWorldHint` false); nothing on this surface
deletes data (`destructiveHint` false); and `complete`, `claim`, `board_attach`
are idempotent while `next_claim`, `status`, `reject`, `note`, `release`,
`create` and `board_create` are not.

Each tool returns the CLI's JSON document unchanged. Exit codes are not
translated into a second vocabulary: a failed call raises with the CLI's
`error.code`, `exit_code` and `details` intact. When the CLI reports
`not_found` (no board), the error text carries the CLI's actionable message and
the full document, plus a one-line hint naming `board_create` and
`board_attach` — never a generic "Error executing tool".

## Tests

```bash
uv run pytest packages/tackboard-mcp/tests
```

The tool layer is tested without the framework: argument mapping against a fake
transport, plus a scratch board driven through the real CLI. The binding tests
(`test_server.py`) run only when `mcp` is installed — including a real client
handshake over stdio — and skip otherwise, so the core suite never needs the
framework.

## Limits

- One board per process. The database is fixed at start-up from `TACKBOARD_DB`
  or discovery; `board_attach` can change it, but the change is process-global
  and applies to every later call, not per client and not per call. Multi-client
  deployments should run one server per board.
- The web view and the schema commands are not exposed; they are not tool-shaped.
- `--force` is reachable only through `release`, `claim` and `status`, each
  requiring a non-empty `reason`; the remaining force cases stay operator
  decisions. `reopen`, `depend_add`, `memo_append` and `claims` are deliberately
  not exposed — fall back to the CLI for those.
