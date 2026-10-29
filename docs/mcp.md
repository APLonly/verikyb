# Using verikyb with Claude Desktop (MCP)

verikyb can run as an MCP server, so Claude Desktop can use its tools.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- A local clone of this repository
- Claude Desktop installed

## Configure Claude Desktop

Open the Claude Desktop config file:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the `verikyb` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "verikyb": {
      "command": "uv",
      "args": ["--directory", "<PATH_TO_VERIKYB>", "run", "verikyb"]
    }
  }
}
```

Replace `<PATH_TO_VERIKYB>` with the absolute path to your local clone of this repository. For example:

- **Windows:** `C:\\Users\\you\\code\\verikyb`
- **macOS:** `/Users/you/code/verikyb`

In JSON, every Windows backslash must be written twice (`\\`).

If the file already has other servers under `mcpServers`, add `verikyb` alongside them instead of replacing the whole file.

## Apply the change

Fully quit and restart Claude Desktop. verikyb should then appear in its list of available tools.