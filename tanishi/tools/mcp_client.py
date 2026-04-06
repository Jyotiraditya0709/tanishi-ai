"""
Tanishi MCP Client — Model Context Protocol Integration.

Connects Tanishi to any MCP server, discovers tools, and registers them
in Tanishi's tool registry. This gives Tanishi access to 2300+ integrations.

Supports:
- stdio transport (local MCP servers via subprocess)
- SSE transport (remote MCP servers via HTTP)
- Auto-discovery of tools from connected servers
- Config file for persistent server connections

Usage:
    /mcp list          — show connected servers
    /mcp connect <url> — connect to an SSE MCP server
    /mcp servers       — show available pre-configured servers
"""

import os
import json
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

from tanishi.tools.registry import ToolDefinition


# ============================================================
# Pre-configured popular MCP servers
# ============================================================

POPULAR_SERVERS = {
    "filesystem": {
        "name": "Filesystem",
        "description": "Read/write files, search, directory listing",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "transport": "stdio",
    },
    "github": {
        "name": "GitHub",
        "description": "Repos, issues, PRs, code search",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "transport": "stdio",
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
    },
    "google-drive": {
        "name": "Google Drive",
        "description": "Search, read, organize Google Drive files",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-gdrive"],
        "transport": "stdio",
    },
    "slack": {
        "name": "Slack",
        "description": "Read/send messages, channels, users",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "transport": "stdio",
        "env": {"SLACK_BOT_TOKEN": "", "SLACK_TEAM_ID": ""},
    },
    "notion": {
        "name": "Notion",
        "description": "Pages, databases, search Notion workspace",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-notion"],
        "transport": "stdio",
        "env": {"NOTION_API_KEY": ""},
    },
    "brave-search": {
        "name": "Brave Search",
        "description": "Web search via Brave API",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "transport": "stdio",
        "env": {"BRAVE_API_KEY": ""},
    },
    "sqlite": {
        "name": "SQLite",
        "description": "Query and manage SQLite databases",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sqlite"],
        "transport": "stdio",
    },
    "memory": {
        "name": "Memory",
        "description": "Knowledge graph-based persistent memory",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
    },
}


# ============================================================
# MCP Connection Manager
# ============================================================

@dataclass
class MCPServer:
    """Represents a connected MCP server."""
    name: str
    transport: str  # "stdio" or "sse"
    status: str = "disconnected"  # connected, disconnected, error
    tools: list[dict] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    url: Optional[str] = None
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    _reader: Any = None
    _writer: Any = None


class MCPClientManager:
    """
    Manages connections to MCP servers and bridges their tools
    into Tanishi's tool registry.
    """

    def __init__(self, tool_registry=None):
        self.servers: dict[str, MCPServer] = {}
        self.tool_registry = tool_registry
        self.config_path = Path.home() / ".tanishi" / "mcp_servers.json"
        self._load_config()

    def _load_config(self):
        """Load saved MCP server configurations."""
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text())
                for name, cfg in data.items():
                    self.servers[name] = MCPServer(
                        name=name,
                        transport=cfg.get("transport", "stdio"),
                        command=cfg.get("command"),
                        args=cfg.get("args", []),
                        env=cfg.get("env", {}),
                        url=cfg.get("url"),
                    )
            except Exception:
                pass

    def _save_config(self):
        """Save MCP server configurations."""
        data = {}
        for name, server in self.servers.items():
            data[name] = {
                "transport": server.transport,
                "command": server.command,
                "args": server.args,
                "env": server.env,
                "url": server.url,
            }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(data, indent=2))

    async def connect_stdio(self, name: str, command: str, args: list[str],
                             env: dict[str, str] = None) -> MCPServer:
        """Connect to a local MCP server via stdio transport."""
        server = MCPServer(
            name=name,
            transport="stdio",
            command=command,
            args=args,
            env=env or {},
        )

        try:
            # Merge environment
            proc_env = os.environ.copy()
            if env:
                proc_env.update({k: v for k, v in env.items() if v})

            # Ensure Node.js is in PATH (Windows often has it in Program Files)
            nodejs_paths = [
                r"C:\Program Files\nodejs",
                r"C:\Program Files (x86)\nodejs",
                os.path.expanduser(r"~\AppData\Roaming\npm"),
            ]
            current_path = proc_env.get("PATH", "")
            for np in nodejs_paths:
                if os.path.isdir(np) and np not in current_path:
                    current_path = f"{np};{current_path}"
            proc_env["PATH"] = current_path

            # Resolve command — on Windows, use full path for npx
            resolved_command = command
            if command == "npx" and os.name == "nt":
                for np in nodejs_paths:
                    npx_cmd = os.path.join(np, "npx.cmd")
                    if os.path.exists(npx_cmd):
                        resolved_command = npx_cmd
                        break

            # Start the MCP server process
            server.process = await asyncio.create_subprocess_exec(
                resolved_command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )

            server._reader = server.process.stdout
            server._writer = server.process.stdin

            # Wait for server to start up (npx downloads packages first time)
            await asyncio.sleep(2.0)

            # Check if process is still alive
            if server.process.returncode is not None:
                stderr = await server.process.stderr.read()
                err_msg = stderr.decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"Server exited immediately: {err_msg}")

            # Initialize MCP connection
            init_result = await self._mcp_initialize(server)

            # Discover tools
            await self._discover_tools(server)

            server.status = "connected"
            self.servers[name] = server
            self._save_config()

            # Register tools in Tanishi
            if self.tool_registry:
                self._register_mcp_tools(server)

            return server

        except FileNotFoundError:
            server.status = "error"
            raise RuntimeError(
                f"Command '{command}' not found. "
                f"Make sure Node.js and npx are installed: https://nodejs.org/"
            )
        except Exception as e:
            server.status = "error"
            raise RuntimeError(f"Failed to connect to {name}: {str(e)}")

    async def connect_sse(self, name: str, url: str) -> MCPServer:
        """Connect to a remote MCP server via SSE transport."""
        server = MCPServer(
            name=name,
            transport="sse",
            url=url,
        )

        try:
            import httpx

            # Test connection
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    raise RuntimeError(f"Server returned {resp.status_code}")

            server.status = "connected"
            self.servers[name] = server
            self._save_config()

            # For SSE, we'll discover tools via the messages endpoint
            await self._discover_tools_sse(server)

            if self.tool_registry:
                self._register_mcp_tools(server)

            return server

        except Exception as e:
            server.status = "error"
            raise RuntimeError(f"Failed to connect to {name} at {url}: {str(e)}")

    async def _mcp_initialize(self, server: MCPServer):
        """Send MCP initialize request via JSON-RPC."""
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": {
                    "name": "Tanishi",
                    "version": "0.5.0",
                },
            },
        }

        response = await self._send_jsonrpc(server, init_msg)

        # Send initialized notification
        initialized_msg = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        await self._send_notification(server, initialized_msg)

        return response

    async def _discover_tools(self, server: MCPServer):
        """Discover available tools from the MCP server."""
        msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        response = await self._send_jsonrpc(server, msg)

        if response and "result" in response:
            tools = response["result"].get("tools", [])
            server.tools = tools
            return tools

        return []

    async def _discover_tools_sse(self, server: MCPServer):
        """Discover tools from SSE server via HTTP."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                # Send tools/list via POST to the messages endpoint
                msg_url = server.url.replace("/sse", "/messages") if "/sse" in server.url else server.url
                resp = await client.post(
                    msg_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                        "params": {},
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    server.tools = data.get("result", {}).get("tools", [])
        except Exception:
            server.tools = []

    async def _send_jsonrpc(self, server: MCPServer, message: dict) -> Optional[dict]:
        """Send a JSON-RPC message via stdio and read response."""
        if not server._writer or not server._reader:
            return None

        try:
            # MCP stdio transport: newline-delimited JSON
            payload = json.dumps(message) + "\n"
            server._writer.write(payload.encode("utf-8"))
            await server._writer.drain()

            # Read response — may need to skip notifications/logs
            response = await asyncio.wait_for(
                self._read_jsonrpc_response(server._reader, message.get("id")),
                timeout=30.0,
            )
            return response

        except asyncio.TimeoutError:
            return {"error": "Timeout waiting for MCP server response"}
        except Exception as e:
            return {"error": str(e)}

    async def _send_notification(self, server: MCPServer, message: dict):
        """Send a notification (no response expected)."""
        if not server._writer:
            return

        try:
            payload = json.dumps(message) + "\n"
            server._writer.write(payload.encode("utf-8"))
            await server._writer.drain()
            # Give server a moment to process
            await asyncio.sleep(0.3)
        except Exception:
            pass

    async def _read_jsonrpc_response(self, reader, expected_id=None) -> Optional[dict]:
        """Read JSON-RPC response from stdio. Handles both NDJSON and Content-Length framing."""
        max_attempts = 20  # Skip up to 20 notifications to find our response

        for _ in range(max_attempts):
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=15.0)
                if not line:
                    continue

                line_str = line.decode("utf-8").strip()

                # Skip empty lines
                if not line_str:
                    continue

                # If we get Content-Length header, read that format
                if line_str.startswith("Content-Length:"):
                    content_length = int(line_str.split(":")[1].strip())
                    # Read blank line separator
                    await reader.readline()
                    # Read body
                    body = await reader.readexactly(content_length)
                    data = json.loads(body.decode("utf-8"))
                else:
                    # Try parsing as JSON directly (NDJSON format)
                    try:
                        data = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                # Check if this is our response (has matching id) or a notification
                if isinstance(data, dict):
                    # If it has an "id" matching ours, it's our response
                    if expected_id is not None and data.get("id") == expected_id:
                        return data
                    # If it has "result" or "error" and no method, it's a response
                    if "result" in data or "error" in data:
                        if "method" not in data:
                            return data
                    # Otherwise it's a notification — skip and keep reading

            except asyncio.TimeoutError:
                return None
            except Exception:
                continue

        return None

    def _register_mcp_tools(self, server: MCPServer):
        """Register MCP server tools in Tanishi's tool registry."""
        for tool in server.tools:
            tool_name = f"mcp_{server.name}_{tool['name']}"
            description = tool.get("description", f"MCP tool from {server.name}")
            input_schema = tool.get("inputSchema", {"type": "object", "properties": {}})

            # Create a closure for the tool handler
            async def mcp_handler(server_ref=server, tool_ref=tool, **kwargs):
                return await self._call_mcp_tool(server_ref, tool_ref["name"], kwargs)

            tool_def = ToolDefinition(
                name=tool_name,
                description=f"[MCP:{server.name}] {description}",
                input_schema=input_schema,
                handler=mcp_handler,
                category="mcp",
                risk_level="medium",
            )

            self.tool_registry.register(tool_def)

    async def _call_mcp_tool(self, server: MCPServer, tool_name: str,
                              arguments: dict) -> str:
        """Execute a tool on an MCP server."""
        if server.transport == "stdio":
            msg = {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
            response = await self._send_jsonrpc(server, msg)

            if response and "result" in response:
                content = response["result"].get("content", [])
                # Extract text from content blocks
                texts = []
                for block in content:
                    if block.get("type") == "text":
                        texts.append(block["text"])
                    elif block.get("type") == "image":
                        texts.append("[Image returned]")
                    else:
                        texts.append(str(block))
                return "\n".join(texts) if texts else "Tool returned no content."

            elif response and "error" in response:
                return f"MCP Error: {response['error']}"

            return "No response from MCP server."

        elif server.transport == "sse":
            try:
                import httpx
                msg_url = server.url.replace("/sse", "/messages") if "/sse" in server.url else server.url
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        msg_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 100,
                            "method": "tools/call",
                            "params": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        },
                    )
                    data = resp.json()
                    content = data.get("result", {}).get("content", [])
                    texts = [b["text"] for b in content if b.get("type") == "text"]
                    return "\n".join(texts) if texts else "Tool returned no content."
            except Exception as e:
                return f"MCP SSE Error: {str(e)}"

        return "Unknown transport."

    async def disconnect(self, name: str):
        """Disconnect from an MCP server."""
        server = self.servers.get(name)
        if not server:
            return

        if server.process:
            try:
                server.process.terminate()
                await asyncio.sleep(0.5)
                if server.process.returncode is None:
                    server.process.kill()
            except Exception:
                pass

        # Unregister tools
        if self.tool_registry:
            prefix = f"mcp_{name}_"
            to_remove = [t for t in self.tool_registry.tools if t.startswith(prefix)]
            for t in to_remove:
                del self.tool_registry.tools[t]

        server.status = "disconnected"
        server.tools = []

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        for name in list(self.servers.keys()):
            await self.disconnect(name)

    def list_servers(self) -> str:
        """List all connected/configured MCP servers."""
        if not self.servers:
            return "No MCP servers configured. Use /mcp connect <name> to add one."

        lines = ["MCP Servers:\n"]
        for name, server in self.servers.items():
            status_icon = {"connected": "[OK]", "disconnected": "[ ]", "error": "[!!]"}.get(server.status, "[?]")
            tool_count = len(server.tools)
            lines.append(f"  {status_icon} {name} ({server.transport}) — {tool_count} tools")

        return "\n".join(lines)

    def list_available(self) -> str:
        """List pre-configured popular MCP servers."""
        lines = ["Available MCP Servers (pre-configured):\n"]
        for key, info in POPULAR_SERVERS.items():
            connected = key in self.servers and self.servers[key].status == "connected"
            status = "[OK]" if connected else "[ ]"
            env_needed = list(info.get("env", {}).keys())
            env_note = f" (needs: {', '.join(env_needed)})" if env_needed else ""
            lines.append(f"  {status} {key}: {info['description']}{env_note}")

        lines.append(f"\nConnect with: /mcp connect <name>")
        return "\n".join(lines)

    async def connect_popular(self, name: str) -> str:
        """Connect to a pre-configured popular MCP server."""
        if name not in POPULAR_SERVERS:
            return f"Unknown server '{name}'. Use /mcp servers to see available options."

        info = POPULAR_SERVERS[name]

        # Check required env vars
        env = {}
        for key, default in info.get("env", {}).items():
            value = os.getenv(key, default)
            if not value:
                return (
                    f"Server '{name}' needs {key} to be set.\n"
                    f"Add it to your .env file: {key}=your_value_here"
                )
            env[key] = value

        server = await self.connect_stdio(
            name=name,
            command=info["command"],
            args=info["args"],
            env=env,
        )

        return (
            f"Connected to {info['name']}!\n"
            f"Discovered {len(server.tools)} tools:\n"
            + "\n".join(f"  - {t['name']}: {t.get('description', '')[:60]}" for t in server.tools)
        )


# ============================================================
# Tool Definitions for Tanishi
# ============================================================

async def mcp_connect(server_name: str, url: str = "") -> str:
    """Connect to an MCP server by name or URL."""
    from tanishi.tools.mcp_client import _get_manager

    manager = _get_manager()

    if url:
        # SSE connection
        server = await manager.connect_sse(server_name, url)
        return f"Connected to {server_name} via SSE. {len(server.tools)} tools available."
    elif server_name in POPULAR_SERVERS:
        return await manager.connect_popular(server_name)
    else:
        return f"Unknown server '{server_name}'. Provide a URL or use one of: {', '.join(POPULAR_SERVERS.keys())}"


async def mcp_list() -> str:
    """List connected MCP servers and available pre-configured ones."""
    from tanishi.tools.mcp_client import _get_manager
    manager = _get_manager()
    connected = manager.list_servers()
    available = manager.list_available()
    return f"{connected}\n\n{available}"


async def mcp_disconnect(server_name: str) -> str:
    """Disconnect from an MCP server."""
    from tanishi.tools.mcp_client import _get_manager
    manager = _get_manager()
    await manager.disconnect(server_name)
    return f"Disconnected from {server_name}."


# Singleton manager
_manager_instance = None

def _get_manager():
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = MCPClientManager()
    return _manager_instance


def init_mcp_manager(tool_registry) -> MCPClientManager:
    """Initialize the MCP manager with Tanishi's tool registry."""
    global _manager_instance
    _manager_instance = MCPClientManager(tool_registry=tool_registry)
    return _manager_instance


def get_mcp_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="mcp_connect",
            description="Connect to an MCP (Model Context Protocol) server to gain new tool capabilities. Use for integrating with external services like GitHub, Slack, Notion, Google Drive, databases, and 2000+ more. Pass a server name (e.g. 'github', 'slack', 'notion') for pre-configured servers, or provide a URL for custom SSE servers.",
            input_schema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Name of the MCP server to connect to (e.g. 'github', 'slack', 'filesystem', 'notion', 'brave-search', 'sqlite', 'memory')",
                    },
                    "url": {
                        "type": "string",
                        "description": "Optional URL for SSE-based MCP servers (e.g. 'http://localhost:3000/sse')",
                        "default": "",
                    },
                },
                "required": ["server_name"],
            },
            handler=mcp_connect,
            category="mcp",
            risk_level="medium",
        ),
        ToolDefinition(
            name="mcp_list",
            description="List all connected MCP servers and available pre-configured servers. Shows server status, transport type, and number of tools available.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=mcp_list,
            category="mcp",
            risk_level="low",
        ),
        ToolDefinition(
            name="mcp_disconnect",
            description="Disconnect from an MCP server and remove its tools.",
            input_schema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Name of the MCP server to disconnect from",
                    },
                },
                "required": ["server_name"],
            },
            handler=mcp_disconnect,
            category="mcp",
            risk_level="low",
        ),
    ]
