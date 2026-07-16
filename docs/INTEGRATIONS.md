# Host Integrations

This document describes the development-preview integration surface. The
single source of machine-readable lifecycle recipes is:

```bash
python3 mind.py integrations --json
```

The protocol server uses stable MCP revision `2025-11-25`, newline-delimited
JSON-RPC over standard input/output, and the current project directory as its
memory root.

## Codex

In a trusted project, add this project-scoped configuration to
`.codex/config.toml`:

```toml
[mcp_servers.mind]
command = "python3"
args = ["mind.py", "mcp"]
```

Then inspect the connected server with `/mcp`. The command and configuration
shape follow the official Codex MCP reference:
<https://developers.openai.com/codex/mcp>.

`AGENTS.md` remains the fallback contract for hosts or sessions that do not
enable the server.

## Claude Code

From the project root:

```bash
claude mcp add --transport stdio --scope project mind -- python3 mind.py mcp
claude mcp get mind
```

Claude Code writes project-scoped servers to `.mcp.json` and requests user
approval before using a checked-in server. The syntax follows:
<https://code.claude.com/docs/en/mcp>.

`CLAUDE.md` remains the fallback contract. Hook integrations can use
`context --json` at session start and `remember --batch` before compaction.

## Gemini CLI

Add this to the project's `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "mind": {
      "command": "python3",
      "args": ["mind.py", "mcp"],
      "cwd": "."
    }
  }
}
```

Inspect the server with `/mcp`. The configuration shape follows:
<https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md>.

`GEMINI.md` remains the fallback contract.

## Windows

Replace `python3` plus `mind.py` with the stock launcher arguments:

```text
py -3 mind.py mcp
```

The Windows continuous-integration cells execute the exported CRLF field path
verbatim.

## Trust Boundary

Project-scoped protocol configuration is executable configuration. Review and
trust a repository before enabling it. `mind` itself uses no network in the
default kernel, but a separately configured semantic process may use the
network and receives query and memory text.
