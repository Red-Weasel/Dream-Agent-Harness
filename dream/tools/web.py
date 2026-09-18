"""Web tools: search (SearXNG) and browse (Camoufox)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..web import searxng
from .context import ctx, err, ok

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to search for."},
        "category": {
            "type": "string",
            "description": "SearXNG category: general, news, science, it, images, videos. Default general.",
        },
        "limit": {"type": "integer", "description": "Max results (default 8)."},
        "time_range": {
            "type": "string",
            "description": "Optional recency filter: day, week, month, year.",
        },
    },
    "required": ["query"],
}


@tool(
    "web_search",
    "Search the web via the local SearXNG metasearch engine. Returns ranked results "
    "with titles, URLs, and snippets. Use this to find sources, then `browse` the best "
    "ones for full content.",
    _SEARCH_SCHEMA,
)
async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    try:
        res = await searxng.search(
            args["query"],
            categories=args.get("category", "general"),
            limit=int(args.get("limit", 8)),
            time_range=args.get("time_range"),
        )
    except searxng.SearxngUnavailable as e:
        return err(str(e))
    except Exception as e:  # network/JSON errors
        return err(f"Search failed: {type(e).__name__}: {e}")

    lines = [f"Search: {res['query']}"]
    for a in res.get("answers", []):
        lines.append(f"  answer: {a}")
    if not res["results"]:
        lines.append("(no results)")
    for i, r in enumerate(res["results"], 1):
        lines.append(f"\n{i}. {r['title']}  [{r['engine']}]")
        lines.append(f"   {r['url']}")
        if r["content"]:
            lines.append(f"   {r['content']}")
    if res.get("suggestions"):
        lines.append("\nsuggestions: " + ", ".join(res["suggestions"]))
    return ok("\n".join(lines))


_BROWSE_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "URL to open (http/https)."},
        "screenshot": {
            "type": "boolean",
            "description": "Also capture a full-page screenshot (default false).",
        },
        "wait_selector": {
            "type": "string",
            "description": "Optional CSS selector to wait for before reading (for JS-heavy pages).",
        },
        "max_chars": {
            "type": "integer",
            "description": "Truncate extracted text to this many chars (default 12000).",
        },
    },
    "required": ["url"],
}


@tool(
    "browse",
    "Open a URL in the persistent Camoufox stealth browser and return the page's clean "
    "readable text (and optionally a screenshot). Handles JavaScript-heavy and "
    "bot-protected pages that a plain fetch can't.",
    _BROWSE_SCHEMA,
)
async def browse(args: dict[str, Any]) -> dict[str, Any]:
    browser = ctx().browser
    res = await browser.fetch(
        args["url"],
        screenshot=bool(args.get("screenshot", False)),
        wait_selector=args.get("wait_selector"),
    )
    if "error" in res:
        return err(f"Could not load {res['url']}: {res['error']}")

    max_chars = int(args.get("max_chars", 12000))
    text = res["text"] or "(no readable text extracted)"
    truncated = len(text) > max_chars
    body = text[:max_chars]
    header = [
        f"# {res['title']}",
        f"URL: {res['final_url']}  (status {res['status']})",
    ]
    if res.get("screenshot"):
        hint = "  (call `see` with this path to look at it)" if ctx().multimodal else ""
        header.append(f"screenshot: {res['screenshot']}{hint}")
    header.append("")
    footer = (
        f"\n\n[...truncated {len(text) - max_chars} chars; call browse again with a "
        f"larger max_chars for more.]"
        if truncated
        else ""
    )
    return ok("\n".join(header) + body + footer)
