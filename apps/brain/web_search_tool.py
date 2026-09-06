"""
Makima v7.3 — Web Search Tool Backend

Multi-strategy web search (no API key required):

  Strategy 1 — DDG JSON Instant Answer API
    Fast, structured, reliable for factual/entity queries.
    Endpoint: https://api.duckduckgo.com/?q=...&format=json

  Strategy 2 — DDG HTML Scraping (improved HTMLParser)
    Broader web results. Uses Python's stdlib HTMLParser instead of the
    previous regex approach — immune to attribute-order changes in DDG markup.

Bug 4 fix: the previous _RESULT_RE regex broke when DDG moved the class
attribute after the href attribute in <a> tags, returning 0 matches and
triggering the research_agent fallback ("Live web data unavailable").

Contract: web_search(query) -> str — never raises, always returns a string.
"""
from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse, parse_qs
from typing import Any

import httpx

logger = logging.getLogger("makima.web_search")

_DDG_HTML_URL = "https://lite.duckduckgo.com/lite/"
_DDG_JSON_URL = "https://api.duckduckgo.com/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")

# NOTE (2026-07-18): verify=False is an interim workaround for a local AV/proxy
# doing HTTPS inspection that breaks Python's certificate trust chain.
# Tracked in CLAUDE_SESSION_LOG.md — proper fix is adding the root CA to Python's trust store.
_HTTPX_KWARGS: dict[str, Any] = dict(timeout=10.0, follow_redirects=True, verify=False)


def _clean_text(fragment: str) -> str:
    """Strip HTML tags and unescape entities from a string fragment."""
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _extract_real_url(ddg_url: str) -> str:
    """
    Extract the real destination URL from a DuckDuckGo redirect wrapper.
    DDG wraps hrefs as: //duckduckgo.com/l/?uddg=https%3A%2F%2F...
    """
    if not ddg_url:
        return ddg_url
    if "duckduckgo.com/l/" in ddg_url:
        full = ddg_url if ddg_url.startswith("http") else "https:" + ddg_url
        try:
            qs = parse_qs(urlparse(full).query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:
            pass
    return ddg_url


# ─── Strategy 2: HTMLParser-based scraper ────────────────────────────────────

class _DDGHTMLParser(HTMLParser):
    """
    Robust HTML parser for DuckDuckGo HTML search results.

    Uses Python's HTMLParser (not regex) so it handles attribute-order
    variations and DDG markup changes gracefully. Class-name matching is
    order-independent; href extraction uses the attrs dict regardless of
    position relative to class.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_result_a = False
        self._in_snippet = False
        self._pending_url = ""
        self._pending_title = ""
        self._pending_snippet = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ("a", "td"):
            return
        attrs_d = dict(attrs)
        classes = (attrs_d.get("class") or "").split()
        raw_href = attrs_d.get("href") or ""

        if "result__a" in classes or "result-link" in classes:
            self._pending_url = _extract_real_url(raw_href)
            self._pending_title = ""
            self._in_result_a = True
            self._in_snippet = False

        elif "result__snippet" in classes or "result-snippet" in classes:
            self._pending_snippet = ""
            self._in_snippet = True
            self._in_result_a = False
        
        # In lite version, snippet is in a td class='result-snippet', not an <a> tag
        if tag == "td" and "result-snippet" in classes:
            self._pending_snippet = ""
            self._in_snippet = True
            self._in_result_a = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_a:
            self._in_result_a = False
            if self._pending_url and self._pending_title.strip():
                self.results.append({
                    "url": self._pending_url,
                    "title": self._pending_title.strip(),
                    "snippet": "",
                })
        elif tag in ("a", "td") and self._in_snippet:
            self._in_snippet = False
            if self.results:
                self.results[-1]["snippet"] = self._pending_snippet.strip()

    def handle_data(self, data: str) -> None:
        if self._in_result_a:
            self._pending_title += data
        elif self._in_snippet:
            self._pending_snippet += data


# ─── Strategy 1: DDG JSON Instant Answer API ─────────────────────────────────

async def _json_search(
    client: httpx.AsyncClient, query: str, max_results: int
) -> list[dict[str, str]]:
    """
    DDG Instant Answer JSON API — fast, structured, reliable for facts.
    Returns up to max_results dicts with url/title/snippet keys.
    """
    try:
        resp = await client.get(
            _DDG_JSON_URL,
            params={
                "q": query,
                "format": "json",
                "no_redirect": "1",
                "no_html": "1",
                "t": "makima",
            },
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        logger.debug("DDG JSON API failed for %r: %s", query, exc)
        return []

    results: list[dict[str, str]] = []

    # Wikipedia-style abstract (highest quality single-source answer)
    abstract = (data.get("AbstractText") or "").strip()
    abstract_url = (data.get("AbstractURL") or "").strip()
    heading = (data.get("Heading") or query).strip()
    if abstract and abstract_url:
        results.append({"url": abstract_url, "title": heading, "snippet": abstract[:600]})

    # Top-level Results array
    for item in data.get("Results", []):
        if not isinstance(item, dict):
            continue
        url = (item.get("FirstURL") or "").strip()
        text = _clean_text(item.get("Text") or "")
        if url and text:
            results.append({"url": url, "title": text[:80], "snippet": text})
        if len(results) >= max_results:
            return results

    # RelatedTopics — flat items + one level of grouped items
    for topic in data.get("RelatedTopics", []):
        if not isinstance(topic, dict):
            continue
        if "Topics" in topic:
            for sub in topic.get("Topics", []):
                if not isinstance(sub, dict):
                    continue
                url = (sub.get("FirstURL") or "").strip()
                text = _clean_text(sub.get("Text") or "")
                if url and text:
                    results.append({"url": url, "title": text[:80], "snippet": text})
                if len(results) >= max_results:
                    return results
        else:
            url = (topic.get("FirstURL") or "").strip()
            text = _clean_text(topic.get("Text") or "")
            if url and text:
                results.append({"url": url, "title": text[:80], "snippet": text})
        if len(results) >= max_results:
            break

    return results[:max_results]


# ─── Strategy 2: DDG HTML scraping ───────────────────────────────────────────

async def _html_search(
    client: httpx.AsyncClient, query: str, max_results: int
) -> list[dict[str, str]]:
    """
    DDG HTML search — broader web results via improved HTMLParser.
    Falls back gracefully on HTTP or parse errors.
    """
    try:
        resp = await client.post(
            _DDG_HTML_URL,
            data={"q": query},
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("DDG HTML search timed out for %r", query)
        return []
    except Exception as exc:
        logger.warning("DDG HTML search failed for %r: %s", query, exc)
        return []

    # Force UTF-8 — DDG response headers don't always reflect the actual encoding
    body = resp.content.decode("utf-8", errors="replace")
    parser = _DDGHTMLParser()
    try:
        parser.feed(body)
    except Exception as exc:
        logger.debug("DDG HTML parser error: %s", exc)
        return []

    return [r for r in parser.results[:max_results] if r.get("url") and r.get("title")]


# ─── Public API ──────────────────────────────────────────────────────────────

def _format_results(query: str, results: list[dict[str, str]], max_results: int) -> str:
    """Format result dicts into a plain-text string for the LLM to synthesize."""
    if not results:
        return f"No results found for '{query}'."

    lines = [f"Web search results for '{query}':"]
    for i, r in enumerate(results[:max_results], start=1):
        title = r.get("title") or "(untitled)"
        url = r.get("url") or ""
        snippet = r.get("snippet") or ""
        lines.append(f"Title: {title}\nURL: {url}\nSnippet: {snippet}\n")

    return "\n".join(lines)


async def web_search(
    query: str, max_results: int = 5, limit: int | None = None, **kwargs: Any
) -> str:
    """
    Search the web and return a formatted string of results for agent synthesis.

    Uses two strategies in order:
      1. DDG JSON Instant Answer API (fast, structured, no regex)
      2. DDG HTML scraping with stdlib HTMLParser fallback (broader web results)

    Contract: never raises — always returns a formatted string.
    """
    query = (query or "").strip()
    if not query:
        return "No search query was provided."

    if limit is not None:
        max_results = limit
    try:
        max_results = max(1, min(int(max_results), 10))
    except (TypeError, ValueError):
        max_results = 5

    async with httpx.AsyncClient(**_HTTPX_KWARGS) as client:
        # Strategy 1: DDG JSON Instant Answer API
        results = await _json_search(client, query, max_results)
        logger.debug("DDG JSON: %d results for %r", len(results), query)

        # Strategy 2: HTML scraping fallback when JSON gives < 2 useful results
        if len(results) < 2:
            html_results = await _html_search(client, query, max_results)
            logger.debug("DDG HTML: %d results for %r", len(html_results), query)
            # Merge: HTML results first (more web-like), then non-duplicate JSON entries
            seen_urls = {r["url"] for r in html_results}
            extra = [r for r in results if r["url"] not in seen_urls]
            results = html_results + extra

    return _format_results(query, results, max_results)


async def fetch_url(url: str, timeout: int = 15, **kwargs: Any) -> str:
    """Fetch raw URL content (JSON/XML/text/HTML) and return clean text as a string."""
    clean_url = (url or "").strip()
    if not clean_url:
        return "[fetch_url] No URL provided."
    try:
        headers = {"User-Agent": _USER_AGENT}
        async with httpx.AsyncClient(**_HTTPX_KWARGS) as client:
            r = await client.get(clean_url, timeout=timeout, headers=headers)
            if r.status_code >= 400:
                return f"[fetch_url] HTTP {r.status_code} error fetching {clean_url}"
            body = r.text
            if len(body.encode("utf-8")) > 512 * 1024:
                return f"[fetch_url] Response too large (> 512KB)."
            # Strip html tags if it looks like HTML
            if "<html" in body.lower() or "<body" in body.lower():
                cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.DOTALL | re.IGNORECASE)
                cleaned = html.unescape(_TAG_RE.sub(" ", cleaned))
                cleaned = re.sub(r"[ \t]+", " ", cleaned)
                cleaned = re.sub(r"\n\s*\n\s*\n+", "\n\n", cleaned).strip()
                return cleaned or body
            return body
    except Exception as e:
        logger.warning("fetch_url failed for %r: %s", clean_url, e)
        return f"[fetch_url] Error fetching {clean_url}: {e}"
