"""
Tanishi Web Search — Eyes on the internet.

Uses DuckDuckGo Lite (no API key needed) and httpx for web fetching.
Tanishi can search the web, read pages, and stay informed.
"""

import re
import json
import httpx
from urllib.parse import quote_plus, urljoin
from typing import Optional

from tanishi.tools.registry import ToolDefinition


# ============================================================
# Search Engine (DuckDuckGo Lite — no API key required)
# ============================================================

async def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using DuckDuckGo.
    Returns formatted search results.
    """
    try:
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.post(url, data={"q": query}, headers=headers)
            html = resp.text

        # Parse results from DuckDuckGo Lite HTML
        results = _parse_ddg_lite(html, max_results)

        if not results:
            # Fallback: try DuckDuckGo API
            results = await _search_ddg_api(query, max_results)

        if not results:
            return f"No results found for '{query}'. The internet is being shy today."

        output_lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            output_lines.append(f"{i}. {r['title']}")
            output_lines.append(f"   URL: {r['url']}")
            if r.get("snippet"):
                output_lines.append(f"   {r['snippet']}")
            output_lines.append("")

        return "\n".join(output_lines)

    except Exception as e:
        return f"Search failed: {str(e)}. Even Google goes down sometimes."


def _parse_ddg_lite(html: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo Lite HTML results."""
    results = []

    # Find result links — DDG Lite uses simple table structure
    link_pattern = re.compile(
        r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>\s*(.+?)\s*</a>',
        re.DOTALL
    )
    snippet_pattern = re.compile(
        r'<td[^>]*class="result-snippet"[^>]*>\s*(.+?)\s*</td>',
        re.DOTALL
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (url, title) in enumerate(links[:max_results]):
        title = re.sub(r'<[^>]+>', '', title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()

        if url.startswith("//duckduckgo.com/l/"):
            # Extract actual URL from DDG redirect
            actual_url = re.search(r'uddg=([^&]+)', url)
            if actual_url:
                from urllib.parse import unquote
                url = unquote(actual_url.group(1))

        if title and url and not url.startswith("//duckduckgo"):
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


async def _search_ddg_api(query: str, max_results: int) -> list[dict]:
    """Fallback: DuckDuckGo Instant Answer API."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
            data = resp.json()

        results = []

        # Abstract
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": data["AbstractText"][:300],
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results - len(results)]:
            if isinstance(topic, dict) and topic.get("FirstURL"):
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "url": topic["FirstURL"],
                    "snippet": topic.get("Text", "")[:200],
                })

        return results
    except Exception:
        return []


# ============================================================
# Web Page Fetcher — Read any URL
# ============================================================

async def fetch_webpage(url: str, max_length: int = 5000) -> str:
    """
    Fetch and extract text content from a webpage.
    Returns cleaned text content.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            html = resp.text

        # Basic HTML to text conversion
        text = _html_to_text(html)

        if len(text) > max_length:
            text = text[:max_length] + "\n\n[... content truncated ...]"

        return f"Content from {url}:\n\n{text}"

    except Exception as e:
        return f"Failed to fetch {url}: {str(e)}"


def _html_to_text(html: str) -> str:
    """Basic HTML to text conversion."""
    # Remove scripts and styles
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL)

    # Convert common elements
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub(r'<p[^>]*>', '\n\n', html)
    html = re.sub(r'<h[1-6][^>]*>', '\n\n## ', html)
    html = re.sub(r'</h[1-6]>', '\n', html)
    html = re.sub(r'<li[^>]*>', '\n• ', html)

    # Remove remaining tags
    html = re.sub(r'<[^>]+>', '', html)

    # Clean up whitespace
    html = re.sub(r'\n\s*\n\s*\n', '\n\n', html)
    html = re.sub(r'  +', ' ', html)

    return html.strip()


# ============================================================
# Tool Definitions for Registry
# ============================================================

def get_web_tools() -> list[ToolDefinition]:
    """Return web-related tool definitions."""
    return [
        ToolDefinition(
            name="web_search",
            description="Search the internet using DuckDuckGo. Use this when you need current information, news, facts, or anything not in your training data. Returns titles, URLs, and snippets.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query. Be specific for better results.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (1-10).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            handler=web_search,
            category="search",
            risk_level="low",
        ),
        ToolDefinition(
            name="fetch_webpage",
            description="Fetch and read the text content of a webpage URL. Use this after web_search to read full articles, documentation, or any web page. Returns cleaned text content.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch (must start with http:// or https://).",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum characters to return.",
                        "default": 5000,
                    },
                },
                "required": ["url"],
            },
            handler=fetch_webpage,
            category="search",
            risk_level="low",
        ),
    ]
