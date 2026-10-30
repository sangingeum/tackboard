# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-30

### Added

- First public release. `tackboard-mcp` is distributed separately from the core
  and depends on `tackboard>=0.7.2,<0.8`, which supplies the `tack` CLI.
- Seven MCP tools — `next_claim`, `show`, `note`, `complete`, `release`,
  `create` and `board_brief` — that shell out to `tack --json` and return the
  CLI document unchanged, so a client never sees a second implementation of
  claims, transitions or validation.
- All four MCP annotation hints declared explicitly on every tool.
