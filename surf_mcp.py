#!/usr/bin/env python3
"""
surf MCP server — exposes surf as tools for Claude Code and other MCP clients.

Setup (add to ~/.claude/settings.json under mcpServers):
  "surf": {
    "command": "/path/to/surf/.venv/bin/python3",
    "args": ["/path/to/surf/surf_mcp.py"]
  }
"""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from mcp.server.models import InitializationOptions
    from mcp.server import NotificationOptions, Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    sys.exit(
        "mcp package not installed. Run: pip install mcp\n"
        "Or from the surf directory: .venv/bin/pip install mcp"
    )

SURF_DIR = Path(__file__).parent
SURF_PY = SURF_DIR / "surf.py"
PYTHON = sys.executable

server = Server("surf")


def _run_surf(query: str) -> dict:
    """Run surf with --json and return parsed result dict."""
    try:
        result = subprocess.run(
            [PYTHON, str(SURF_PY), query, "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(SURF_DIR),
        )
        stdout = result.stdout.strip()
        if stdout:
            return json.loads(stdout)
        return {"error": result.stderr.strip() or "No output from surf"}
    except subprocess.TimeoutExpired:
        return {"error": "Query timed out after 60 seconds"}
    except json.JSONDecodeError as e:
        return {"error": f"Could not parse surf output: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _strip_ansi(text: str) -> str:
    return re.sub(r'\033\[[0-9;]*m', '', text)


def _format_result(data: dict) -> str:
    """Format surf JSON result as clean text for MCP clients."""
    if "error" in data:
        return f"Error: {data['error']}"

    parts = []

    if tldr := data.get("tldr"):
        parts.append(f"**Summary:** {_strip_ansi(tldr)}")

    if body := data.get("answer") or data.get("response"):
        clean = _strip_ansi(str(body)).strip()
        if clean and clean != data.get("tldr", ""):
            parts.append(clean)

    if sources := data.get("sources"):
        lines = ["**Sources:**"]
        for i, s in enumerate(sources[:6], 1):
            lines.append(f"{i}. {s}" if isinstance(s, str) else
                         f"{i}. {s.get('title', '')} — {s.get('url', '')}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else "No results found."


def _tool(name: str, description: str, query_param: str = "query",
          query_desc: str = "The search query") -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {query_param: {"type": "string", "description": query_desc}},
            "required": [query_param],
        },
    )


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        _tool(
            "search",
            "Search the web and get an AI-synthesized answer with cited sources. "
            "Best for general questions, current events, how-to, research, comparisons. "
            "surf routes weather/stock/academic/factual queries to specialized APIs automatically.",
            query_desc="Natural language search query",
        ),
        _tool(
            "weather",
            "Get a live weather forecast for any location. "
            "Returns current conditions, hourly and daily forecast from Open-Meteo. "
            "Use for temperature, rain, wind, UV index queries.",
            query_desc="Weather query, e.g. 'weather in Austin tomorrow' or 'will it rain in NYC this weekend'",
        ),
        _tool(
            "stock",
            "Get real-time stock or crypto price data from Yahoo Finance. "
            "Returns current price, day/52-week range, market cap, volume, and trend sparkline.",
            query_desc="Stock query, e.g. 'AAPL stock price' or 'Apple stock today' or 'bitcoin price'",
        ),
        _tool(
            "academic",
            "Search peer-reviewed academic literature. "
            "Searches PubMed (biomedical/clinical) and arXiv (physics/math/CS) in parallel. "
            "Use for clinical studies, meta-analyses, systematic reviews, scientific papers.",
            query_desc="Research topic, e.g. 'statins and cognitive decline' or 'CRISPR off-target effects'",
        ),
        _tool(
            "factual",
            "Look up a named entity on Wikipedia. "
            "Use for 'what is X', 'who is Y', 'where is Z' — people, places, events, concepts. "
            "Handles disambiguation pages with an inline choice.",
            query_desc="Entity query, e.g. 'what is the Coriolis effect' or 'who is Ada Lovelace'",
        ),
        _tool(
            "read_url",
            "Fetch and read any web page or article. "
            "Returns AI-formatted full article content with a TL;DR header. "
            "Use when you have a specific URL to read and extract information from.",
            query_param="url",
            query_desc="The full URL to fetch and read",
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    query = arguments.get("query") or arguments.get("url", "")
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _run_surf, query)
    return [types.TextContent(type="text", text=_format_result(data))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="surf",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
