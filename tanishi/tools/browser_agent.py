"""
Tanishi Browser Agent — She controls your browser.

Uses Playwright to automate a real Chrome browser:
- Open pages, click buttons, fill forms
- Search and compare products
- Extract information from any website
- Take page screenshots for visual analysis
- Automate repetitive web tasks

This is what makes Tanishi a real agent — not just talking, DOING.
"""

import os
import re
import json
import base64
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

from tanishi.tools.registry import ToolDefinition


# ============================================================
# Browser Manager — Singleton browser instance
# ============================================================

class BrowserManager:
    """
    Manages a persistent browser session.
    Reuses the same browser across tool calls for speed.
    """

    _instance = None
    _browser = None
    _context = None
    _page = None

    @classmethod
    async def get_page(cls):
        """Get or create the browser page."""
        if cls._page and not cls._page.is_closed():
            return cls._page

        try:
            from playwright.async_api import async_playwright

            if not cls._instance:
                cls._instance = await async_playwright().start()

            if not cls._browser or not cls._browser.is_connected():
                cls._browser = await cls._instance.chromium.launch(
                    headless=False,  # Visible browser — user can see what's happening
                    args=[
                        "--window-size=1280,900",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )

            if not cls._context:
                cls._context = await cls._browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )

            cls._page = await cls._context.new_page()
            return cls._page

        except Exception as e:
            raise RuntimeError(f"Browser launch failed: {e}. Run: playwright install chromium")

    @classmethod
    async def close(cls):
        """Close the browser."""
        try:
            if cls._page and not cls._page.is_closed():
                await cls._page.close()
            if cls._context:
                await cls._context.close()
            if cls._browser:
                await cls._browser.close()
            if cls._instance:
                await cls._instance.stop()
        except Exception:
            pass
        cls._page = None
        cls._context = None
        cls._browser = None
        cls._instance = None


# ============================================================
# Browser Actions
# ============================================================

async def browse_url(url: str) -> str:
    """
    Open a URL in the browser and return the page content.
    The browser is VISIBLE — user can watch Tanishi work.
    """
    try:
        page = await BrowserManager.get_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1000)  # Let JS render

        title = await page.title()
        url_final = page.url

        # Extract main text content
        content = await page.evaluate("""
            () => {
                // Remove scripts, styles, nav, footer
                const remove = document.querySelectorAll('script, style, nav, footer, header, iframe, .ad, .ads, .sidebar');
                remove.forEach(el => el.remove());

                // Get main content
                const main = document.querySelector('main, article, .content, #content, .main')
                    || document.body;
                return main.innerText.substring(0, 5000);
            }
        """)

        return f"Page: {title}\nURL: {url_final}\n\nContent:\n{content[:4000]}"

    except Exception as e:
        return f"Failed to open {url}: {str(e)}"


async def browser_search(query: str, engine: str = "google") -> str:
    """
    Search the web using a real browser. Returns visible search results.
    """
    try:
        page = await BrowserManager.get_page()

        if engine == "google":
            await page.goto(f"https://www.google.com/search?q={query}", wait_until="domcontentloaded", timeout=15000)
        elif engine == "amazon":
            await page.goto(f"https://www.amazon.in/s?k={query}", wait_until="domcontentloaded", timeout=15000)
        elif engine == "youtube":
            await page.goto(f"https://www.youtube.com/results?search_query={query}", wait_until="domcontentloaded", timeout=15000)
        else:
            await page.goto(f"https://www.google.com/search?q={query}", wait_until="domcontentloaded", timeout=15000)

        await page.wait_for_timeout(2000)

        # Extract search results
        if engine == "amazon":
            results = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('[data-component-type="s-search-result"]');
                    const results = [];
                    items.forEach((item, i) => {
                        if (i >= 8) return;
                        const title = item.querySelector('h2 a span')?.innerText || '';
                        const price = item.querySelector('.a-price .a-offscreen')?.innerText || 'Price N/A';
                        const rating = item.querySelector('.a-icon-alt')?.innerText || '';
                        const link = item.querySelector('h2 a')?.href || '';
                        if (title) results.push({title, price, rating, link});
                    });
                    return results;
                }
            """)

            if not results:
                return f"No Amazon results for '{query}'. Page might need manual interaction."

            output = [f"Amazon search results for: {query}\n"]
            for i, r in enumerate(results, 1):
                output.append(f"{i}. {r['title'][:80]}")
                output.append(f"   Price: {r['price']} | Rating: {r['rating']}")
                output.append(f"   Link: {r['link'][:100]}")
                output.append("")
            return "\n".join(output)

        elif engine == "youtube":
            results = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('ytd-video-renderer');
                    const results = [];
                    items.forEach((item, i) => {
                        if (i >= 8) return;
                        const title = item.querySelector('#video-title')?.innerText || '';
                        const channel = item.querySelector('#channel-name')?.innerText || '';
                        const views = item.querySelector('#metadata-line span')?.innerText || '';
                        const link = item.querySelector('#video-title')?.href || '';
                        if (title) results.push({title, channel, views, link});
                    });
                    return results;
                }
            """)

            output = [f"YouTube results for: {query}\n"]
            for i, r in enumerate(results, 1):
                output.append(f"{i}. {r['title'][:80]}")
                output.append(f"   Channel: {r['channel']} | {r['views']}")
                output.append("")
            return "\n".join(output)

        else:
            # Google results
            results = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('.g');
                    const results = [];
                    items.forEach((item, i) => {
                        if (i >= 8) return;
                        const title = item.querySelector('h3')?.innerText || '';
                        const snippet = item.querySelector('.VwiC3b')?.innerText || '';
                        const link = item.querySelector('a')?.href || '';
                        if (title) results.push({title, snippet, link});
                    });
                    return results;
                }
            """)

            output = [f"Google search results for: {query}\n"]
            for i, r in enumerate(results, 1):
                output.append(f"{i}. {r['title']}")
                output.append(f"   {r['snippet'][:150]}")
                output.append(f"   {r['link'][:100]}")
                output.append("")
            return "\n".join(output)

    except Exception as e:
        return f"Browser search failed: {str(e)}"


async def click_element(selector: str, text: str = "") -> str:
    """Click an element on the current page by CSS selector or text content."""
    try:
        page = await BrowserManager.get_page()

        if text:
            # Click by visible text
            element = page.get_by_text(text, exact=False).first
            await element.click(timeout=5000)
        else:
            await page.click(selector, timeout=5000)

        await page.wait_for_timeout(1500)

        title = await page.title()
        return f"Clicked. Now on: {title} ({page.url})"

    except Exception as e:
        return f"Click failed: {str(e)}"


async def fill_form(selector: str, value: str) -> str:
    """Fill a form field on the current page."""
    try:
        page = await BrowserManager.get_page()
        await page.fill(selector, value, timeout=5000)
        return f"Filled '{selector}' with '{value[:50]}'"
    except Exception as e:
        return f"Fill failed: {str(e)}"


async def get_page_info() -> str:
    """Get info about the current page — title, URL, visible text."""
    try:
        page = await BrowserManager.get_page()
        title = await page.title()
        url = page.url

        # Get visible text
        text = await page.evaluate("""
            () => {
                const main = document.querySelector('main, article, .content, #content')
                    || document.body;
                return main.innerText.substring(0, 3000);
            }
        """)

        # Get all links
        links = await page.evaluate("""
            () => {
                const links = [];
                document.querySelectorAll('a[href]').forEach((a, i) => {
                    if (i >= 15) return;
                    const text = a.innerText.trim().substring(0, 60);
                    if (text && text.length > 2) links.push({text, href: a.href});
                });
                return links;
            }
        """)

        # Get all buttons
        buttons = await page.evaluate("""
            () => {
                const btns = [];
                document.querySelectorAll('button, input[type="submit"], [role="button"]').forEach((b, i) => {
                    if (i >= 10) return;
                    const text = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
                    if (text) btns.push(text.substring(0, 40));
                });
                return btns;
            }
        """)

        # Get form fields
        fields = await page.evaluate("""
            () => {
                const fields = [];
                document.querySelectorAll('input, textarea, select').forEach((f, i) => {
                    if (i >= 10) return;
                    const name = f.name || f.id || f.placeholder || f.type;
                    fields.push({name, type: f.type, selector: f.id ? '#'+f.id : f.name ? `[name="${f.name}"]` : ''});
                });
                return fields;
            }
        """)

        output = [f"Page: {title}", f"URL: {url}\n"]

        if buttons:
            output.append(f"Buttons: {', '.join(buttons[:10])}")
        if fields:
            output.append(f"Form fields: {', '.join(f['name'] for f in fields[:10])}")
        if links:
            output.append(f"\nTop links:")
            for l in links[:10]:
                output.append(f"  [{l['text']}] → {l['href'][:80]}")

        output.append(f"\nContent preview:\n{text[:2000]}")

        return "\n".join(output)

    except Exception as e:
        return f"Page info error: {str(e)}"


async def page_screenshot() -> str:
    """Take a screenshot of the current browser page."""
    try:
        page = await BrowserManager.get_page()
        screenshots_dir = Path.home() / ".tanishi" / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        filename = f"browser_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = screenshots_dir / filename
        await page.screenshot(path=str(path), full_page=False)

        return f"Browser screenshot saved to {path}"

    except Exception as e:
        return f"Screenshot failed: {str(e)}"


async def browser_back() -> str:
    """Go back to the previous page."""
    try:
        page = await BrowserManager.get_page()
        await page.go_back(wait_until="domcontentloaded", timeout=10000)
        title = await page.title()
        return f"Went back to: {title} ({page.url})"
    except Exception as e:
        return f"Back failed: {str(e)}"


async def scroll_page(direction: str = "down") -> str:
    """Scroll the page up or down."""
    try:
        page = await BrowserManager.get_page()
        if direction == "down":
            await page.evaluate("window.scrollBy(0, 800)")
        else:
            await page.evaluate("window.scrollBy(0, -800)")
        await page.wait_for_timeout(500)
        return f"Scrolled {direction}"
    except Exception as e:
        return f"Scroll failed: {str(e)}"


async def close_browser() -> str:
    """Close the browser."""
    await BrowserManager.close()
    return "Browser closed."


# ============================================================
# Tool Definitions
# ============================================================

def get_browser_tools() -> list[ToolDefinition]:
    """Return browser automation tool definitions."""
    return [
        ToolDefinition(
            name="browse_url",
            description="Open a URL in a real Chrome browser (visible to user) and return the page content. Use this to visit any website, read articles, check products, or navigate to a specific page.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to visit (e.g., 'https://amazon.in')"},
                },
                "required": ["url"],
            },
            handler=browse_url,
            category="browser",
            risk_level="low",
        ),
        ToolDefinition(
            name="browser_search",
            description="Search using a real browser. Supports Google (default), Amazon (product search with prices), and YouTube (video search). Use for product comparisons, shopping, research. Returns structured results with titles, prices, links.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "engine": {
                        "type": "string",
                        "enum": ["google", "amazon", "youtube"],
                        "description": "Search engine: 'google' (default), 'amazon' (products with prices), 'youtube' (videos)",
                        "default": "google",
                    },
                },
                "required": ["query"],
            },
            handler=browser_search,
            category="browser",
            risk_level="low",
        ),
        ToolDefinition(
            name="click_element",
            description="Click a button, link, or element on the current browser page. Use CSS selector or visible text. Examples: click_element(text='Add to Cart'), click_element(selector='#submit-btn')",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of element to click", "default": ""},
                    "text": {"type": "string", "description": "Visible text of element to click (easier than selector)", "default": ""},
                },
                "required": [],
            },
            handler=click_element,
            category="browser",
            risk_level="medium",
        ),
        ToolDefinition(
            name="fill_form",
            description="Type text into a form field on the current page. Useful for search boxes, login forms, contact forms. Use CSS selector to identify the field.",
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of the input field (e.g., '#search', 'input[name=email]')"},
                    "value": {"type": "string", "description": "Text to type into the field"},
                },
                "required": ["selector", "value"],
            },
            handler=fill_form,
            category="browser",
            risk_level="medium",
        ),
        ToolDefinition(
            name="get_page_info",
            description="Get detailed info about the current browser page: title, URL, visible text, all links, buttons, and form fields. Use this to understand what's on the page before deciding what to click or fill.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=get_page_info,
            category="browser",
            risk_level="low",
        ),
        ToolDefinition(
            name="scroll_page",
            description="Scroll the browser page up or down to see more content.",
            input_schema={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["down", "up"], "default": "down"},
                },
                "required": [],
            },
            handler=scroll_page,
            category="browser",
            risk_level="low",
        ),
        ToolDefinition(
            name="browser_back",
            description="Go back to the previous page in the browser.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=browser_back,
            category="browser",
            risk_level="low",
        ),
        ToolDefinition(
            name="close_browser",
            description="Close the browser when done with web tasks.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=close_browser,
            category="browser",
            risk_level="low",
        ),
    ]
