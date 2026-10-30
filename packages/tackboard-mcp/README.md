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

What it adds is discovery and a typed surface: seven named tools with described
arguments, instead of a CLI string to compose.

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

The server reads three environment variables:

| Variable | Meaning |
|---|---|
| `TACKBOARD_DB` | the board file to operate on (same variable the CLI reads) |
| `TACKBOARD_AGENT` | the actor every mutation is attributed to |
| `TACK_CMD` | overrides the CLI command prefix; defaults to the installed core module, or `tack` when the core is not importable |

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
| `next_claim` | `stage?` (`work`/`review`) | the `next --claim` document; `task` is `null` when nothing is eligible |
| `show` | `task_id` | the `show` document: the task and its notes |
| `note` | `task_id`, `text`, `refs?` (`KIND:VALUE`) | the `note` document with the appended event |
| `complete` | `task_id` | the mutation document; completing unblocks dependents |
| `release` | `task_id`, `reason?` | the mutation document; a reason is the audited force path |
| `create` | `title`, `description?`, `priority?`, `assignee?`, `labels?`, `depends_on?`, `status?` | the created task document |
| `board_brief` | — | the counts-only board document |

Every tool declares all four MCP annotation hints as explicit booleans. The two
read tools (`board_brief`, `show`) are read-only and repeatable; every mutating
tool writes only the one local board and touches no external system
(`openWorldHint` false); nothing on this surface deletes data
(`destructiveHint` false); and `complete` is idempotent while `next_claim`,
`note`, `release` and `create` are not.

Each tool returns the CLI's JSON document unchanged. Exit codes are not
translated into a second vocabulary: a failed call raises with the CLI's
`error.code`, `exit_code` and `details` intact.

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

- One board per process: the database is fixed at start-up.
- The web view and the schema commands are not exposed; they are not tool-shaped.
- `--force` is reachable only through `release`'s `reason`, deliberately: the
  other force cases are operator decisions, not assistant ones.
