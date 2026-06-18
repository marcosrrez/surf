#!/usr/bin/env python3
"""surf MCP server — data-only. Returns structured search/API data for Claude Code to synthesize."""

import asyncio
import contextlib
import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from mcp.server.models import InitializationOptions
    from mcp.server import NotificationOptions, Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    sys.exit("mcp package not installed: pip install mcp")

# Import surf data functions
sys.path.insert(0, str(Path(__file__).parent))
try:
    from surf import (
        ddg_search,
        _handle_weather, _handle_financial, _handle_factual,
        _search_pubmed, _search_arxiv,
        _ACADEMIC_PREAMBLES, _ACADEMIC_STOP,
        fetch_page, extract_text,
    )
except ImportError as e:
    sys.exit(f"Could not import surf: {e}")

server = Server("surf")

_ANSI = re.compile(r'\033\[[0-9;]*m')


def _strip(text: str) -> str:
    return _ANSI.sub('', text).strip()


def _call(fn, *args, **kwargs):
    """Call a surf function, suppressing all stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*args, **kwargs)


def _json_content(result: dict | list) -> list[types.TextContent]:
    return [types.TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2),
    )]


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
            "Search the web via DuckDuckGo and return a list of results with titles, "
            "URLs, domains, and snippets. Best for general questions, current events, "
            "how-to, research, comparisons. Returns raw structured data for Claude to synthesize.",
            query_desc="Natural language search query",
        ),
        _tool(
            "weather",
            "Get a live weather forecast for any location from Open-Meteo. "
            "Returns current conditions, hourly and daily forecast data. "
            "Use for temperature, rain, wind, UV index queries.",
            query_desc="Weather query, e.g. 'weather in Austin tomorrow' or 'will it rain in NYC this weekend'",
        ),
        _tool(
            "stock",
            "Get real-time stock or crypto price data from Yahoo Finance. "
            "Returns current price, day/52-week range, market cap, volume, and trend data.",
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
            "Use for 'what is X', 'who is Y', 'where is Z' — people, places, events, concepts.",
            query_desc="Entity query, e.g. 'what is the Coriolis effect' or 'who is Ada Lovelace'",
        ),
        _tool(
            "read_url",
            "Fetch and read any web page or article. "
            "Returns the page title and first 3000 characters of extracted text. "
            "Use when you have a specific URL to read and extract information from.",
            query_param="url",
            query_desc="The full URL to fetch and read",
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    loop = asyncio.get_event_loop()

    if name == "search":
        query = arguments.get("query", "")
        try:
            results = await loop.run_in_executor(
                None, lambda: _call(ddg_search, query, num_results=10)
            )
            return _json_content(results or [])
        except Exception as e:
            return _json_content({"error": str(e)})

    elif name == "weather":
        query = arguments.get("query", "")
        try:
            result = await loop.run_in_executor(
                None, lambda: _call(_handle_weather, query)
            )
            if result is None:
                return _json_content({"error": "no results found"})
            response_str, sources, _streaming = result
            return _json_content({
                "summary": _strip(response_str),
                "sources": sources,
            })
        except Exception as e:
            return _json_content({"error": str(e)})

    elif name == "stock":
        query = arguments.get("query", "")
        try:
            result = await loop.run_in_executor(
                None, lambda: _call(_handle_financial, query)
            )
            if result is None:
                return _json_content({"error": "no ticker found"})
            response_str, sources, _streaming = result
            return _json_content({
                "summary": _strip(response_str),
                "sources": sources,
            })
        except Exception as e:
            return _json_content({"error": str(e)})

    elif name == "academic":
        query = arguments.get("query", "")
        try:
            def _do_academic():
                # Strip request-phrasing preambles
                search_terms = query
                ql = query.lower()
                for prefix in _ACADEMIC_PREAMBLES:
                    if ql.startswith(prefix):
                        search_terms = query[len(prefix):]
                        break

                pubmed_papers: list[dict] = []
                arxiv_papers: list[dict] = []

                with ThreadPoolExecutor(max_workers=2) as executor:
                    pubmed_future = executor.submit(_search_pubmed, search_terms)
                    arxiv_future = executor.submit(_search_arxiv, search_terms)
                    future_map = {pubmed_future: "pubmed", arxiv_future: "arxiv"}
                    for future in as_completed(future_map, timeout=15.0):
                        try:
                            res = future.result()
                            if future_map[future] == "pubmed":
                                pubmed_papers = res
                            else:
                                arxiv_papers = res
                        except Exception:
                            pass

                # Filter arXiv: key search terms must appear in title or abstract
                if arxiv_papers and search_terms:
                    key_terms = {w.lower().rstrip("s") for w in search_terms.split()
                                 if len(w) > 3 and w.lower() not in _ACADEMIC_STOP}
                    if key_terms:
                        arxiv_papers = [
                            p for p in arxiv_papers
                            if any(t in (p.get("title", "") + " " + p.get("abstract", "")).lower()
                                   for t in key_terms)
                        ]

                papers = pubmed_papers + arxiv_papers

                # Deduplicate by title prefix
                seen_titles: set = set()
                unique_papers = []
                for p in papers:
                    key = p["title"].lower()[:50]
                    if key not in seen_titles:
                        seen_titles.add(key)
                        unique_papers.append(p)
                papers = unique_papers[:5]

                return [
                    {
                        "title": p.get("title", ""),
                        "authors": p.get("authors", ""),
                        "year": p.get("year", ""),
                        "abstract": p.get("abstract", ""),
                        "url": p.get("link", ""),
                        "source": p.get("source", ""),
                    }
                    for p in papers
                ]

            papers = await loop.run_in_executor(None, _do_academic)
            if not papers:
                return _json_content({"error": "no results found"})
            return _json_content({"papers": papers})
        except Exception as e:
            return _json_content({"error": str(e)})

    elif name == "factual":
        query = arguments.get("query", "")
        try:
            result = await loop.run_in_executor(
                None, lambda: _call(_handle_factual, query)
            )
            if result is None:
                return _json_content({"error": "not found"})
            response_str, sources, _streaming = result
            url = sources[0].get("url", "") if sources else ""
            return _json_content({
                "summary": _strip(response_str),
                "url": url,
            })
        except Exception as e:
            return _json_content({"error": str(e)})

    elif name == "read_url":
        url = arguments.get("url", "")
        try:
            def _do_read():
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    html = fetch_page(url)
                    extracted = extract_text(html, return_title=True)
                return extracted

            extracted = await loop.run_in_executor(None, _do_read)

            if isinstance(extracted, tuple):
                title, text = extracted
            else:
                title, text = "", extracted or ""

            return _json_content({
                "title": title,
                "text_excerpt": text[:6000],
            })
        except Exception as e:
            return _json_content({"error": str(e)})

    else:
        return _json_content({"error": f"unknown tool: {name}"})


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="surf",
                server_version="2.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
