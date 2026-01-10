"""
Simple MCP Client for testing MCP Server.

This client can be used to test the MCP server functionality by connecting
to a running MCP server and calling its tools.
"""

import asyncio
from typing import Any, Dict, List

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


class MCPClient:
    """Simple MCP client for testing purposes."""

    def __init__(self, server_path: str, env: Dict[str, str] | None = None):
        """Initialize the MCP client.

        Args:
            server_path: Path to the MCP server script (not used, kept for compatibility)
            env: Environment variables to pass to the server
        """
        self.server_path = server_path
        self.env = env or {}
        # Create transport with environment variables
        # Run as module from the project root
        import pathlib

        project_root = (
            pathlib.Path(server_path).parent.parent.parent
        )  # Go up to project root
        self.transport = StdioTransport(
            command="uv",
            args=["run", "todo-list-mcp"],
            env=self.env,
            cwd=str(project_root),
        )
        self.client = Client(self.transport)

    async def __aenter__(self):
        """Enter context manager and connect to the server."""
        await self.client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager and disconnect."""
        await self.client.__aexit__(exc_type, exc_val, exc_tb)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool with the given arguments.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as a dictionary

        Returns:
            Tool execution result
        """
        result = await self.client.call_tool(tool_name, arguments)
        return result

    async def list_tools(self) -> List[Dict]:
        """List all available tools from the server.

        Returns:
            List of tool descriptions
        """
        tools = await self.client.list_tools()
        # Convert Tool objects to dictionaries
        return [tool.model_dump() for tool in tools]


# Synchronous wrapper for easier testing
class SyncMCPClient:
    """Synchronous wrapper around the async MCP client."""

    def __init__(self, server_path: str, env: Dict[str, str] | None = None):
        """Initialize the synchronous MCP client."""
        self.server_path = server_path
        self.env = env or {}
        self._loop = None
        self._async_client = None

    def __enter__(self):
        """Enter context manager."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Create async client
        self._async_client = MCPClient(self.server_path, self.env)

        # Enter the async context
        self._loop.run_until_complete(self._async_client.__aenter__())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        if self._async_client:
            self._loop.run_until_complete(
                self._async_client.__aexit__(exc_type, exc_val, exc_tb)
            )
            # Properly stop the transport to prevent cleanup warnings
            if hasattr(self._async_client.transport, "_stop_event"):
                self._async_client.transport._stop_event.set()

        if self._loop:
            self._loop.close()

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool synchronously."""
        if not self._loop or not self._async_client:
            raise RuntimeError("Client not connected")

        async def _call():
            return await self._async_client.call_tool(tool_name, arguments)

        return self._loop.run_until_complete(_call())

    def list_tools(self) -> List[Dict]:
        """List tools synchronously."""
        if not self._loop or not self._async_client:
            raise RuntimeError("Client not connected")

        async def _list():
            return await self._async_client.list_tools()

        return self._loop.run_until_complete(_list())
