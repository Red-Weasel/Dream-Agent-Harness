"""Web tools: search (SearXNG) and browse (Camoufox)."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from claude_agent_sdk import tool

from ..core import turn_origin
from ..web import searxng
from ..web.browser import Destination, bare_url, destination, local_host
from .context import ctx, err, in_thread, ok, untrusted_web_content

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


_SCHEME = re.compile(r"https?://", re.I)
_PAIRS = {'"': '"', "'": "'", "`": "`", "<": ">", "(": ")"}  # decoration; brackets never are (an IPv6 host's, a link's)
_NOT_IN_ONE_URL = set("[]()<>\"'`,@")


def _unwrap(token: str) -> str:
    """`token` less at most one layer of wrapping -- a matching pair of quotes, backticks, angle brackets or
    parentheses around it -- and the sentence punctuation after it (`(http://x).` is http://x; `[::1]` is
    itself, its brackets being the address's)."""
    token = token.rstrip(".,;:!?")
    if len(token) > 1 and token[0] in _PAIRS and token[-1] == _PAIRS[token[0]]:
        token = token[1:-1].rstrip(".,;:!?")
    return token


def _one_url(token: str) -> bool:
    """`token` is one URL in its entirety and nothing else: no second `://`; no whitespace, control,
    zero-width or bidi character; none of the characters that would make it a link, a list, a quotation
    or userinfo (`[ ] ( ) < > " ' `` ` `` , @`) -- except the two brackets of an IPv6 address standing as
    the URL's host, in the authority and nowhere else; what sits between them, a zone id included, is held
    to the same rule."""
    if token.count("://") > 1 or any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in token):
        return False
    try:
        netloc = urlsplit(token if _SCHEME.match(token) else "http://" + token).netloc
    except ValueError:
        return False
    body = token
    if netloc.startswith("[") and "]" in netloc:
        literal = netloc[:netloc.index("]") + 1]
        try:
            if ipaddress.ip_address(literal[1:-1]).version == 6:
                # the authority comes first in the token: this is it. Only its two brackets go; the
                # address and any zone id stay in `body` for the check below.
                body = token.replace(literal, literal[1:-1], 1)
        except ValueError:
            pass
    return not _NOT_IN_ONE_URL & set(body)


def _urls(words: str) -> list[str]:
    """What the owner's words offer as URLs: each whitespace-separated token that, less one layer of
    wrapping (_unwrap), is one URL in its entirety (_one_url) -- a plain URL, or a bare destination.
    Nothing else: not a Markdown link's target, not a URL inside another's path, query or fragment, not
    URLs run together (a comma between them makes the token no URL at all). What is not one whole URL
    grants nothing; the owner types the address on its own. A URL that is its own token grants even
    inside whitespace-padded Markdown (`[go]( http://localhost:3001/ )`): the owner typed it on its own."""
    return [url for url in (_unwrap(token) for token in words.split()) if url and _one_url(url)]


def _typed_by_owner(turns: list[dict[str, Any]]) -> frozenset[Destination]:
    """The owner's grants: the destinations (scheme, host, port) of the URLs in their latest message that
    name this machine or its network directly (web.browser.local_host: `localhost`, `*.localhost`, a
    local address), each one whole token typed on its own (_urls) parsed whole (web.browser.destination)
    -- a bare one read as the model's bare URL is (web.browser.bare_url) -- never matched as a substring:
    `http://127.0.0.1:8080@attacker.example/` names attacker.example and, reading two ways, authorizes
    nothing; `https://localhost.attacker.example/` names that host, not localhost. A typed name grants
    nothing, nor does a typed public address: a public destination needs no grant, and a name that
    resolves locally never opens by being typed. A user-role turn Dream wrote (the progress guard's, the
    loop's) is skipped; a Council or guided-task
    wrapper counts by the owner's words inside it. Nothing a page or a tool result says can stand in for
    the owner: only they can point the browser at this machine or its network."""
    for turn in turns:  # newest first
        words = turn["content"]
        if turn_origin.is_generated(turn["tool_name"], words):
            words = turn_origin.owner_words(turn["tool_name"], words)
            if words is None:
                continue
        found = set()
        for url in _urls(words):
            if not _SCHEME.match(url):  # bare: read as the model's bare URL is (http for a local destination)
                url = bare_url(url)
            dest = destination(url)
            if dest and local_host(dest[1]):
                found.add(dest)
        return frozenset(found)
    return frozenset()


@tool(
    "browse",
    "Open a URL in the persistent Camoufox stealth browser and return the page's clean "
    "readable text (and optionally a screenshot). Handles JavaScript-heavy and "
    "bot-protected pages that a plain fetch can't. Reaches only the public internet: a "
    "local or private address opens only when the user typed that URL in their message. "
    "On Linux the browser runs inside a bubblewrap sandbox with its own network namespace: "
    "no network traffic reaches it or leaves it except through Dream's connection boundary "
    "(without bubblewrap it does not run); elsewhere that boundary is the proxy alone.",
    _BROWSE_SCHEMA,
)
async def browse(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    turns = await in_thread(c.store.recent_user_turns, c.session_id)
    res = await c.browser.fetch(
        args["url"],
        screenshot=bool(args.get("screenshot", False)),
        wait_selector=args.get("wait_selector"),
        allowed=_typed_by_owner(turns),
    )
    if "error" in res:
        return err(f"Could not load {res['url']}: {res['error']}")

    max_chars = int(args.get("max_chars", 12000))
    text = res["text"] or "(no readable text extracted)"
    truncated = len(text) > max_chars
    page = "\n".join([
        f"# {res['title']}",
        f"URL: {res['final_url']}  (status {res['status']})",
        "",
        text[:max_chars],
    ])
    # The page is data; Dream's own lines to the model stay outside the block.
    lines = [untrusted_web_content(page)]
    if res.get("screenshot"):
        hint = "  (call `see` with this path to look at it)" if c.multimodal else ""
        lines.append(f"screenshot: {res['screenshot']}{hint}")
    if res.get("sealed_off"):
        lines.append("local page opened sealed off from the internet; not loaded: " + ", ".join(res["sealed_off"]))
    if truncated:
        lines.append(
            f"[...truncated {len(text) - max_chars} chars; call browse again with a "
            f"larger max_chars for more.]"
        )
    return ok("\n".join(lines))
