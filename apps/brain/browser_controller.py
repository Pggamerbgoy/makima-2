"""
Makima v7.1 — Browser Controller

Playwright-based browser automation.
- Managed mode: launches/reuses a Makima-owned Chromium instance
- CDP attach mode: attaches to user's existing Chrome via --remote-debugging-port=9222
- Vision fallback: screenshot → Gemini Vision if element not found by selector
- CAPTCHA detection: stops and surfaces to user immediately (never retries)
- URL validation: fuzzy-match domain vs. user intent before navigation
- Max page timeout: 30s
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("makima.browser_controller")

CAPTCHA_SIGNALS = [
    "captcha", "are you human", "verify you are not a robot",
    "recaptcha", "hcaptcha", "cloudflare challenge",
]

# Best-effort selectors for the most common cookie/consent overlays. These
# don't just look nicer — an undismissed banner sits on top of the real
# content and silently eats clicks meant for whatever's underneath it
# (confirmed live: this is exactly what broke the Spotify search box before
# the persistent profile had consent cookies saved). Tried in order, first
# visible match wins, all failures swallowed — this must never be able to
# break a navigation.
_CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",       # OneTrust (very common)
    "#L2AGLb",                             # Google "I agree"
    "button[aria-label='Accept all']",
    "button[aria-label='Accept All']",
    "button[aria-label='Accept cookies']",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
]

# Patches the most common headless/automation fingerprints before any page
# script runs. Doesn't defeat determined bot-detection, but stops the cheap
# `navigator.webdriver === true` checks that otherwise trigger CAPTCHAs on
# sites that wouldn't challenge a normal Chrome profile.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
    window.navigator.permissions.query = (params) => (
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(params)
    );
}
"""


class BrowserController:
    """
    Playwright browser controller.
    Lazy-initialised: browser starts only when first needed.
    """

    def __init__(self, config: dict, ai_handler=None, ws_broadcast=None):
        self.config = config.get("browser", {})
        self.ai_handler = ai_handler
        self.ws_broadcast = ws_broadcast

        self._playwright = None
        self._browser = None
        self._pages: dict[str, Any] = {}
        self._cdp_attached = False
        self._cdp_page_is_new = False  # True if we created the CDP page ourselves

        self._page_timeout_ms = self.config.get("page_timeout_ms", 30_000)
        self._playwright_available: Optional[bool] = None
        # NOTE: headless is ALWAYS False for Makima desktop assistant — the browser
        # must be visible to the user. This is hardcoded at all launch sites below
        # and is NOT configurable. Removing the config-readable flag prevents any
        # accidental headless=True configuration from breaking desktop UX.

    @property
    def _page(self):
        """Backwards-compatibility accessor for the default tab."""
        return self._pages.get("default") or self._pages.get("browser")

    async def get_page(self, tab: str = "default"):
        """Get or create a named Playwright Page tab with active tab pruning & RAM optimization."""
        page = self._pages.get(tab)
        if page and not page.is_closed():
            try:
                await page.bring_to_front()
            except Exception:
                pass
            self._ensure_window_visible()
            return page

        if not await self._ensure_ready():
            logger.warning("get_page: _ensure_ready failed, attempting browser recovery...")
            await self._recover_browser()
            if not await self._ensure_ready():
                return None

        context = self._browser if hasattr(self._browser, "new_page") else (self._browser.contexts[0] if (self._browser and getattr(self._browser, "contexts", None)) else None)
        if not context and self._browser and hasattr(self._browser, "new_context"):
            try:
                context = await self._browser.new_context(viewport=None)
            except Exception:
                if getattr(self._browser, "contexts", None):
                    context = self._browser.contexts[0]
        if not context:
            return None

        # ── RAM & TAB OPTIMIZATION: Clean up leftover blank tabs and reuse unassigned pages ──
        existing_pages = [p for p in getattr(context, "pages", []) if not p.is_closed()]
        if len(existing_pages) > 4:
            for p in existing_pages:
                if len([pg for pg in getattr(context, "pages", []) if not pg.is_closed()]) <= 3:
                    break
                if p not in self._pages.values():
                    try:
                        await p.close()
                    except Exception:
                        pass
            existing_pages = [p for p in getattr(context, "pages", []) if not p.is_closed()]

        blank_unassigned = [p for p in existing_pages if p not in self._pages.values() and p.url in ("about:blank", "", None)]
        other_unassigned = [p for p in existing_pages if p not in self._pages.values() and p.url not in ("about:blank", "", None)]

        if blank_unassigned:
            new_p = blank_unassigned[0]
            # Close any extra duplicate blank tabs
            for extra_blank in blank_unassigned[1:]:
                try:
                    await extra_blank.close()
                except Exception:
                    pass
        elif other_unassigned:
            new_p = other_unassigned[0]
        else:
            new_p = await context.new_page()
            for p in existing_pages:
                if p not in self._pages.values() and p.url in ("about:blank", "", None):
                    try:
                        await p.close()
                    except Exception:
                        pass

        new_p.set_default_timeout(self._page_timeout_ms)
        self._pages[tab] = new_p
        await self._apply_default_blocklist(new_p)
        try:
            await new_p.bring_to_front()
        except Exception:
            pass
        self._ensure_window_visible()
        return new_p

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _find_browser_executable(self, target_browser: str | None = None) -> tuple[str | None, str | None]:
        """Resolve (executable_path, channel) for requested browser ('brave', 'edge', 'chrome')."""
        target = (target_browser or "").lower()
        if sys.platform == "win32":
            if "brave" in target:
                for b_p in [
                    os.path.expandvars(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe"),
                    os.path.expandvars(r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe"),
                    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
                ]:
                    if os.path.isfile(b_p):
                        return (b_p, None)

            if "edge" in target or "msedge" in target:
                for e_p in [
                    os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
                ]:
                    if os.path.isfile(e_p):
                        return (e_p, None)
                return (None, "msedge")

            if "chrome" in target:
                for c_p in [
                    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                ]:
                    if os.path.isfile(c_p):
                        return (c_p, None)
                return (None, "chrome")

        # Default fallback: check if Brave is installed, else Chrome
        if sys.platform == "win32":
            for b_p in [
                os.path.expandvars(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            ]:
                if os.path.isfile(b_p):
                    return (b_p, None)

        return (None, "chrome")

    async def start(self, preferred_browser: str | None = None, try_cdp: bool = True) -> bool:
        """Start Playwright and launch a managed Chromium browser."""
        try:
            # 1. Socket check: Only attach CDP if port 9222 is actively listening
            import socket
            port_alive = False
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    port_alive = s.connect_ex(("127.0.0.1", 9222)) == 0
            except Exception:
                port_alive = False

            if try_cdp and port_alive:
                try:
                    if await self.attach_cdp(9222):
                        logger.info("BrowserController: Attached to existing open browser window via CDP (9222)")
                        return True
                except Exception as e_cdp:
                    logger.debug(f"CDP attach attempt failed: {e_cdp}")

            from playwright.async_api import async_playwright
            if not self._playwright:
                self._playwright = await async_playwright().start()

            # 2. Launch with persistent user profile so logins and cookies are retained across sessions
            from pathlib import Path
            profile_path = Path.home() / ".makima" / "browser_profile"
            profile_path.mkdir(parents=True, exist_ok=True)
            managed_profile_dir = str(profile_path)
            exec_path, channel = self._find_browser_executable(preferred_browser)

            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": False,
                    "viewport": None,
                    "args": [
                        "--remote-debugging-port=9222",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--start-maximized",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                        "--disable-background-media-suspend",
                        "--autoplay-policy=no-user-gesture-required",
                    ],
                }
                if exec_path:
                    launch_kwargs["executable_path"] = exec_path
                    logger.info(f"BrowserController: launching requested browser ({exec_path})")
                else:
                    launch_kwargs["channel"] = channel or "chrome"
                    logger.info(f"BrowserController: launching browser channel '{channel}'")

                self._browser = await self._playwright.chromium.launch_persistent_context(
                    managed_profile_dir,
                    **launch_kwargs
                )
            except Exception as e_chrome:
                logger.info(f"Could not launch channel='chrome' with persistent profile ({e_chrome}), trying standard chromium with persistent profile...")
                try:
                    self._browser = await self._playwright.chromium.launch_persistent_context(
                        managed_profile_dir,
                        headless=False,
                        viewport=None,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--disable-infobars",
                            "--start-maximized",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-background-timer-throttling",
                            "--disable-backgrounding-occluded-windows",
                            "--disable-renderer-backgrounding",
                            "--disable-background-media-suspend",
                            "--autoplay-policy=no-user-gesture-required",
                        ],
                    )
                except Exception as e_temp:
                    logger.error(f"Fallback standard chromium launch failed: {e_temp}")
                    raise e_temp
            p = self._browser.pages[0] if getattr(self._browser, "pages", None) else await self._browser.new_page()
            p.set_default_timeout(self._page_timeout_ms)
            self._pages["default"] = p
            try:
                await self._browser.add_init_script(_STEALTH_INIT_SCRIPT)
            except Exception as e_stealth:
                logger.debug(f"Stealth init script not applied: {e_stealth}")
            logger.info("BrowserController: managed Chromium started")
            self._ensure_window_visible()
            return True
        except ImportError:
            self._playwright_available = False
            logger.error("playwright not installed. Run: pip install playwright && playwright install")
            return False
        except Exception as e:
            logger.error(f"BrowserController start failed: {e}")
            return False

    async def attach_cdp(self, port: int = 9222) -> bool:
        """Attach to user's existing Chrome via CDP."""
        try:
            from playwright.async_api import async_playwright
            if not self._playwright:
                self._playwright = await async_playwright().start()
            # Use 127.0.0.1 (IPv4) explicitly — Windows resolves localhost to ::1 (IPv6)
            # first, but Chrome only listens on 127.0.0.1 by default.
            self._browser = await self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            pages = self._browser.contexts[0].pages if self._browser.contexts else []
            # Smart Reuse: Scan open pages to reuse an existing YouTube/Spotify tab
            target_page = None
            for page_item in pages:
                try:
                    p_url = page_item.url or ""
                    if any(domain in p_url.lower() for domain in ["youtube.com", "spotify.com"]):
                        target_page = page_item
                        if "youtube.com" in p_url.lower():
                            self._pages["media"] = page_item
                        break
                except Exception:
                    pass
            if target_page:
                p = target_page
                self._cdp_page_is_new = False
            elif pages:
                p = pages[0]
                self._cdp_page_is_new = False
            else:
                p = await self._browser.contexts[0].new_page()
                self._cdp_page_is_new = True
            p.set_default_timeout(self._page_timeout_ms)
            self._pages["default"] = p
            self._cdp_attached = True
            logger.info(f"BrowserController: CDP attached on port {port}")
            return True
        except Exception as e:
            logger.info("No active CDP browser found on port %d (%s). Tip: Launch Brave/Chrome with '--remote-debugging-port=%d' to attach directly to your existing session.", port, e, port)
            self._cdp_attached = False
            return await self.start(try_cdp=False)

    async def stop(self) -> None:
        """Shut down browser resources.

        In CDP-attach mode we do NOT close the user's browser — we only
        close the page we created ourselves (if any). In managed mode we
        close the entire browser.
        """
        try:
            if self._cdp_attached:
                for tab, page in list(self._pages.items()):
                    if page and not page.is_closed():
                        if self._cdp_page_is_new or tab != "default":
                            await page.close()
            elif self._browser:
                await self._browser.close()
        except Exception as e:
            logger.warning(f"BrowserController stop error: {e}")
        finally:
            self._pages.clear()
            self._browser = None
            self._cdp_attached = False
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    async def _recover_browser(self) -> None:
        """Recovers the browser by starting a new instance if the current one is closed."""
        if self._playwright_available is False:
            return
        if self._browser and not self._browser.is_connected():
            logger.info("Browser has been closed, recovering...")
            await self._browser.disconnect()
            self._browser = None
            await self.start()  # Start a new browser instance
        elif not self._browser:
            logger.info("No browser instance, starting fresh...")
            await self.start()

    # ------------------------------------------------------------------
    # Core Navigation
    # ------------------------------------------------------------------

    async def navigate(self, url: str = None, expected_domain: str = "", tab: str = "default", **kwargs) -> str:
        """Navigate to URL on a specific named tab. Validates domain against intent."""
        if not url:
            params = kwargs.get("parameters", {})
            if isinstance(params, dict):
                url = params.get("url")
            if not url:
                url = kwargs.get("url")
        if not url:
            return "Error: Missing required argument 'url'."

        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."

        # Domain validation
        if expected_domain:
            parsed_host = (urlparse(url).hostname or "").lower().rstrip(".")
            expected_host = expected_domain.lower().strip().rstrip(".")
            if not parsed_host or not (
                parsed_host == expected_host
                or parsed_host.endswith("." + expected_host)
            ):
                return (
                    f"⚠️ URL domain mismatch: '{url}' doesn't match expected '{expected_domain}'. "
                    "Navigation cancelled for safety."
                )

        # Skip the reload if this tab is already on the right site. Every
        # action (pause/resume/next/volume, not just play) calls this via
        # _ensure_connector_open() unconditionally on every single command —
        # without this check, that meant a full SPA reload (2-5+ seconds)
        # before every click, even just to hit "pause". This was the single
        # biggest source of latency in the media flow.
        current_url = page.url or ""
        current_host = (urlparse(current_url).hostname or "").lower().rstrip(".")
        expected_host = expected_domain.lower().strip().rstrip(".")
        current_domain_matches = bool(
            expected_host
            and (current_host == expected_host or current_host.endswith("." + expected_host))
        )
        if (expected_domain and current_url != "about:blank"
                and current_domain_matches
                and (url.strip().rstrip('/') == current_url.strip().rstrip('/') or current_url.startswith(url))):
            self._ensure_window_visible()
            return f"Already on {expected_domain} [tab={tab}] — skipped reload."

        try:
            response = await page.goto(url, wait_until="domcontentloaded",
                                        timeout=self._page_timeout_ms)
            await self._check_captcha(tab=tab)
            await self._dismiss_consent_banners(tab=tab)
            self._ensure_window_visible()
            status = response.status if response else "unknown"
            return f"Navigated to {url} (HTTP {status}) [tab={tab}]"
        except RuntimeError:
            raise  # CAPTCHA — never retry, never swallow
        except Exception as e:
            err_str = str(e).lower()
            is_closed = any(kw in err_str for kw in ["target closed", "context closed", "browser has been closed", "page closed"])
            # One retry with a short backoff for transient network hiccups
            # (DNS blip, slow TLS handshake) before giving up — this alone
            # used to cost a full failed turn on a flaky connection.
            logger.debug(f"Navigate attempt 1 failed for {url}: {e}. Retrying once...")
            await asyncio.sleep(1.5)
            try:
                # If the page/context was closed, get a fresh page before retrying
                if is_closed:
                    logger.info(f"Page/context closed during navigate to {url}, recovering fresh page")
                    self._pages.pop(tab, None)
                    page = await self.get_page(tab)
                    if not page:
                        # Browser itself might be closed — recover it
                        logger.warning(f"Browser unavailable for navigation to {url}, recovering browser...")
                        await self._recover_browser()
                        page = await self.get_page(tab)
                        if not page:
                            return f"[NAV_FAILED] Browser recovery failed for navigation to {url}"
                response = await page.goto(url, wait_until="domcontentloaded",
                                            timeout=self._page_timeout_ms)
                await self._check_captcha(tab=tab)
                await self._dismiss_consent_banners(tab=tab)
                status = response.status if response else "unknown"
                return f"Navigated to {url} (HTTP {status}) [tab={tab}] (after retry)"
            except RuntimeError:
                raise
            except Exception as e2:
                return f"[NAV_FAILED] Navigation failed after retry: {e2}"

    async def get_text(self, selector: str = None, tab: str = "default", **kwargs) -> str:
        """Get visible text content of specified tab or specific selector."""
        page = await self.get_page(tab)
        if not page:
            return ""
        try:
            target_sel = selector if selector else "body"
            return await page.inner_text(target_sel, timeout=3000)
        except Exception as e:
            return f"Element '{selector}' text not found or timed out: {e}"

    async def distill_dom(self, selector: str = None, tab: str = "default", max_chars: int = 4000, **kwargs) -> str:
        """
        Hyper-Compressed Semantic DOM Distillation Engine.
        Strips noise tags (<script>, <style>, <svg>, <noscript>, <header>, <footer>, <nav>),
        collapses whitespace, extracts main content, and clamps output text to max_chars (~1000 tokens).
        Prevents LLM token overflow (HTTP 413 Payload Too Large) and eliminates chattiness/timeouts.
        """
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser page not active."
        try:
            # Use JS-based extraction as primary — more reliable than BeautifulSoup
            # for dynamic pages and doesn't require bs4 dependency
            js_clean = """
            (() => {
                const docTitle = document.title || '';
                const root = document.querySelector('""" + (selector or "body") + """') || document.body;
                const removeSelectors = ['script', 'style', 'noscript', 'svg', 'iframe', 'header', 'footer', 'nav', 'link', 'meta'];
                const links = Array.from(root.querySelectorAll('a[href]')).map(a => `[LINK: ${(a.innerText || a.textContent || '').trim().slice(0,80)}] -> ${a.href}`).join('\\n');
                const buttons = Array.from(root.querySelectorAll('button, input[type=submit], [role=button]')).map(b => `[BUTTON: ${(b.innerText || b.textContent || '').trim().slice(0,80)}]`).join('\\n');
                const clone = root.cloneNode(true);
                ['h1', 'h2', 'h3', 'h4'].forEach(tag => {
                    clone.querySelectorAll(tag).forEach(el => {
                        const prefix = tag === 'h1' ? '# H1: ' : tag === 'h2' ? '## H2: ' : '### H3: ';
                        el.textContent = prefix + (el.textContent || '').trim();
                    });
                });
                removeSelectors.forEach(sel => clone.querySelectorAll(sel).forEach(el => el.remove()));
                const text = (clone.innerText && clone.innerText.trim()) ? clone.innerText : (clone.textContent || '');
                return 'Title: ' + docTitle + '\\n--- Links ---\\n' + links + '\\n--- Buttons ---\\n' + buttons + '\\n--- Content ---\\n' + text;
            })()
            """
            text = await page.evaluate(js_clean)

            import re
            cleaned_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
            compressed = "\n".join(cleaned_lines)
            compressed = re.sub(r' +', ' ', compressed)

            if len(compressed) > max_chars:
                compressed = compressed[:max_chars] + f"\n... [Distilled & truncated to {max_chars} chars]"

            return compressed or "No readable text content found on page."
        except Exception as e:
            logger.warning(f"distill_dom JS extraction failed: {e}")
            # Fallback: try BeautifulSoup if available
            try:
                html_content = await page.content()
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, "html.parser")
                for element in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "iframe"]):
                    element.decompose()
                text = soup.get_text(separator="\n", strip=True)
                import re
                cleaned_lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
                compressed = "\n".join(cleaned_lines)
                compressed = re.sub(r' +', ' ', compressed)
                if len(compressed) > max_chars:
                    compressed = compressed[:max_chars] + f"\n... [Distilled & truncated to {max_chars} chars]"
                return compressed or "No readable text content found on page."
            except Exception:
                return await self.get_text(selector=selector, tab=tab)

    async def get_screenshot_b64(self, tab: str = "default", **kwargs) -> Optional[str]:
        """Take screenshot of specified tab and return base64-encoded JPEG."""
        page = await self.get_page(tab)
        if not page:
            logger.warning("Screenshot: no page available for tab=%s", tab)
            return None
        try:
            full_page = bool(kwargs.get("full_page", False))
            # Ensure viewport is set — persistent context may have null viewport
            # which causes screenshot to fail on some pages
            if page.viewport_size is None:
                try:
                    await page.set_viewport_size({"width": 1280, "height": 720})
                except Exception:
                    pass
            img_bytes = await page.screenshot(type="jpeg", quality=70, full_page=full_page)
            return base64.b64encode(img_bytes).decode("utf-8")
        except Exception as e:
            # Retry once — sometimes the page is still loading
            logger.warning(f"Screenshot attempt 1 failed: {e}. Retrying once...")
            await asyncio.sleep(1)
            try:
                img_bytes = await page.screenshot(type="jpeg", quality=70, full_page=False)
                return base64.b64encode(img_bytes).decode("utf-8")
            except Exception as e2:
                logger.error(f"Screenshot failed after retry: {e2}")
                return None

    async def click_element(self, selector: str = "", text: str = "", vision_description: str = "", tab: str = "default", **kwargs) -> str:
        """Click an element on specified tab by CSS selector or visible text."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."

        # Try selector first
        last_error: Exception | None = None
        if selector:
            try:
                await page.click(selector, timeout=5_000)
                await self._check_captcha(tab=tab)
                return f"Clicked element: {selector}"
            except RuntimeError:
                raise  # CAPTCHA — never swallow, must bubble to the agent loop
            except Exception as e:
                last_error = e
                logger.debug(f"click_element selector failed ({selector}): {e}")

        # Try by text
        if text:
            try:
                await page.get_by_text(text, exact=False).first.click(timeout=5_000)
                await self._check_captcha(tab=tab)
                return f"Clicked text: '{text}'"
            except RuntimeError:
                raise
            except Exception as e:
                last_error = e
                logger.debug(f"click_element text failed ('{text}'): {e}")

        # Vision fallback
        if self.ai_handler:
            if vision_description:
                return await self._vision_click(vision_description, tab=tab)
            return await self._vision_click(selector or text, tab=tab)

        # No vision available — surface the REAL reason instead of a generic
        # message, so failures are debuggable from the reply text alone
        # (this used to swallow the actual Playwright error, e.g. strict-mode
        # "resolved to N elements" or a plain timeout, making every failure
        # look identical and undiagnosable without re-running by hand).
        reason = f" ({type(last_error).__name__}: {last_error})" if last_error else ""
        return f"Could not find element to click: {selector or text}{reason}"

    async def click(self, selector: str = "", text: str = "", tab: str = "default", **kwargs) -> str:
        """Alias for tool compatibility."""
        return await self.click_element(selector=selector, text=text, tab=tab, **kwargs)

    async def fill(self, selector: str, text: str = "", value: str = "", tab: str = "default", **kwargs) -> str:
        """Alias for tool compatibility."""
        return await self.fill_input(selector=selector, value=text or value, tab=tab, **kwargs)

    async def run_js(self, script: str = "", tab: str = "default", **kwargs) -> str:
        """Run custom JavaScript snippet in browser with IIFE wrapper to fix top-level return SyntaxErrors."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        js_code = (script or "").strip()
        if not js_code:
            return "Error: No JavaScript code provided."
        
        if "return " in js_code and not (js_code.startswith("(") or js_code.startswith("function")):
            js_code = f"(() => {{ {js_code} }})()"
            
        try:
            res = await page.evaluate(js_code)
            return str(res) if res is not None else "JavaScript executed cleanly (returned None)."
        except Exception as e:
            logger.warning(f"JS eval failed: {e}")
            return f"JS Execution Failed: {e}"

    async def fill_input(self, selector: str, value: str, tab: str = "default", **kwargs) -> str:
        """Fill an input field on specified tab."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.fill(selector, value, timeout=5_000)
            return f"Filled '{selector}' with value"
        except Exception as e:
            return f"Fill failed: {e}"

    # ------------------------------------------------------------------
    # Elite pass additions — form/keyboard/navigation/tab tools that were
    # missing entirely (agent had no way to submit-via-Enter, pick a
    # dropdown option, scroll, or manage more than a click/fill/navigate
    # loop). Same error-handling style as the rest of the file: never raise
    # except CAPTCHA, always return a plain-English result string.
    # ------------------------------------------------------------------

    async def press_key(self, key: str, selector: str = "", tab: str = "default") -> str:
        """Press a keyboard key (e.g. 'Enter', 'Tab', 'Escape'). Focuses
        `selector` first if given, otherwise presses on whatever currently
        has focus. Most search boxes submit on Enter — this is usually more
        reliable than hunting for a submit button that may not exist."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            if selector:
                await page.press(selector, key, timeout=5_000)
            else:
                await page.keyboard.press(key)
            await self._check_captcha(tab=tab)
            return f"Pressed '{key}'" + (f" on '{selector}'" if selector else "")
        except RuntimeError:
            raise
        except Exception as e:
            return f"Press key failed: {e}"

    async def select_option(self, selector: str, value: str = "", label: str = "", tab: str = "default") -> str:
        """Pick an option in a <select> dropdown by value or visible label."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            if label:
                await page.select_option(selector, label=label, timeout=5_000)
            else:
                await page.select_option(selector, value=value, timeout=5_000)
            return f"Selected option on '{selector}'"
        except Exception as e:
            return f"Select option failed: {e}"

    async def hover(self, selector: str, tab: str = "default") -> str:
        """Hover over an element — needed for hover-triggered menus that
        selectors alone can't reach (they don't exist in the DOM until
        hovered)."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.hover(selector, timeout=5_000)
            return f"Hovered over '{selector}'"
        except Exception as e:
            return f"Hover failed: {e}"

    async def scroll(self, direction: str = "down", amount: int = 800, selector: str = "", tab: str = "default") -> str:
        """Scroll the page, or a specific scrollable element if `selector`
        is given. Needed for infinite-scroll feeds and anything below the
        fold that click/fill can't see yet."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        dx, dy = 0, 0
        if direction == "down":
            dy = amount
        elif direction == "up":
            dy = -amount
        elif direction == "right":
            dx = amount
        elif direction == "left":
            dx = -amount
        else:
            return f"Invalid scroll direction: '{direction}' (use up/down/left/right)"
        try:
            if selector:
                await page.eval_on_selector(
                    selector, "(el, d) => el.scrollBy(d.dx, d.dy)", {"dx": dx, "dy": dy}
                )
            else:
                await page.mouse.wheel(dx, dy)
            return f"Scrolled {direction} by {amount}px" + (f" within '{selector}'" if selector else "")
        except Exception as e:
            return f"Scroll failed: {e}"

    async def wait_for(self, selector: str = "", state: str = "visible",
                        timeout_ms: int = 8_000, network_idle: bool = False,
                        tab: str = "default") -> str:
        """Explicitly wait for a selector to reach a state (visible/hidden/
        attached/detached) and/or for network activity to settle. Use this
        instead of blind retries on dynamic pages — call it BEFORE reading
        text or clicking something that loads asynchronously."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            if network_idle:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            if selector:
                await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            return f"Wait condition met (selector={selector or 'none'}, state={state}, network_idle={network_idle})"
        except Exception as e:
            return f"[TIMEOUT] Wait condition not met: {e}"

    async def go_back(self, tab: str = "default") -> str:
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.go_back(timeout=self._page_timeout_ms, wait_until="domcontentloaded")
            await self._check_captcha(tab=tab)
            return f"Went back. Now at {page.url}"
        except RuntimeError:
            raise
        except Exception as e:
            return f"Go back failed: {e}"

    async def go_forward(self, tab: str = "default") -> str:
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.go_forward(timeout=self._page_timeout_ms, wait_until="domcontentloaded")
            await self._check_captcha(tab=tab)
            return f"Went forward. Now at {page.url}"
        except RuntimeError:
            raise
        except Exception as e:
            return f"Go forward failed: {e}"

    async def reload_page(self, tab: str = "default") -> str:
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.reload(timeout=self._page_timeout_ms, wait_until="domcontentloaded")
            await self._check_captcha(tab=tab)
            return f"Reloaded {page.url}"
        except RuntimeError:
            raise
        except Exception as e:
            return f"Reload failed: {e}"

    async def extract_links(self, tab: str = "default", limit: int = 50) -> str:
        """Return visible link text + href pairs — for picking a search
        result, a nav item, or any link by its actual text instead of
        guessing a CSS selector for it."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            links = await page.evaluate(
                "(lim) => Array.from(document.querySelectorAll('a[href]'))"
                ".filter(e => e.offsetWidth > 0 || e.offsetHeight > 0)"
                ".slice(0, lim)"
                ".map(e => ({text: e.innerText.trim().slice(0, 80), href: e.href}))"
                ".filter(l => l.text)",
                limit,
            )
            if not links:
                return "No visible links found."
            return "\n".join(f"- {l['text']}: {l['href']}" for l in links)
        except Exception as e:
            return f"Link extraction failed: {e}"

    async def get_element_attribute(self, selector: str, attribute: str, tab: str = "default") -> str:
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            val = await page.get_attribute(selector, attribute, timeout=5_000)
            return val if val is not None else f"Attribute '{attribute}' not set on '{selector}'"
        except Exception as e:
            return f"Get attribute failed: {e}"

    async def check_element_exists(self, selector: str, tab: str = "default") -> str:
        """Cheap existence/visibility probe that never raises — use this to
        decide between two candidate selectors before committing to a click,
        instead of finding out the hard way."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            counts = await page.evaluate(
                "(sel) => { const els = document.querySelectorAll(sel);"
                " return {total: els.length, visible: Array.from(els).filter(e => e.offsetWidth>0||e.offsetHeight>0).length}; }",
                selector,
            )
            return f"{counts['total']} match(es), {counts['visible']} visible for '{selector}'"
        except Exception as e:
            return f"Check failed: {e}"

    async def upload_file(self, selector: str, file_path: str, tab: str = "default") -> str:
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.set_input_files(selector, file_path, timeout=8_000)
            return f"Uploaded '{file_path}' to '{selector}'"
        except Exception as e:
            return f"Upload failed: {e}"

    async def switch_tab(self, tab: str = "default") -> str:
        """Switch focus to a named tab. Creates the tab if it doesn't exist."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.bring_to_front()
            return f"Switched to tab '{tab}' ({page.url})"
        except Exception as e:
            return f"Switch tab failed: {e}"

    async def evaluate_xpath(self, xpath: str, tab: str = "default") -> str:
        """Evaluate an XPath expression and return the text content of matching elements."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            elements = await page.query_selector_all(f"xpath={xpath}")
            if not elements:
                return f"No elements found for XPath: {xpath}"
            texts = []
            for el in elements[:20]:
                try:
                    text = await el.inner_text()
                    texts.append(text.strip()[:200])
                except Exception:
                    pass
            if not texts:
                return f"XPath matched {len(elements)} element(s) but no text content."
            return "\n".join(f"- {t}" for t in texts if t)
        except Exception as e:
            return f"XPath evaluation failed: {e}"

    # Default aggressive anti-noise ruleset: ad/tracker domains + heavy media.
    _DEFAULT_AD_BLOCKLIST = (
        "doubleclick", "googlesyndication", "google-analytics", "googletagmanager",
        "googletagservices", "googleadservices", "facebook.net", "fbcdn", "hotjar",
        "matomo", "scorecardresearch", "amazon-adsystem", "taboola", "outbrain",
        "adservice", "2mdn", "adnxs", "criteo", "adsystem", "quantserve", "moatads",
    )

    def set_default_blocklist(self, enabled: bool = True) -> None:
        """Enable/disable the default ad/tracker/media blocking ruleset."""
        self._default_blocklist_enabled = bool(enabled)
        if not enabled:
            self._blocklisted_pages = set()

    async def _apply_default_blocklist(self, page) -> None:
        """Apply per-page route handler that aborts ad/tracker/media requests."""
        if not getattr(self, "_default_blocklist_enabled", True):
            return
        if not hasattr(self, "_blocklisted_pages"):
            self._blocklisted_pages = set()
        if id(page) in self._blocklisted_pages:
            return
        self._blocklisted_pages.add(id(page))

        async def _default_route(route):
            try:
                req = route.request
                url = (req.url or "").lower()
                # Ad and tracker blocking
                if any(d in url for d in self._DEFAULT_AD_BLOCKLIST):
                    await route.abort()
                    return
            except Exception:
                pass
            try:
                await route.continue_()
            except Exception:
                pass

        try:
            await page.route("**/*", _default_route)
        except Exception as e:
            logger.debug(f"Default blocklist route failed: {e}")

    async def network_intercept(self, url_pattern: str = "", action: str = "block", tab: str = "default") -> str:
        """Block or allow network requests matching a URL pattern.
        action: 'block' to block, 'allow' to unblock/remove intercept."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            if not hasattr(self, '_route_handlers'):
                self._route_handlers = {}
            
            if action == "block":
                async def block_route(route):
                    await route.abort()
                await page.route(url_pattern or "**/*", block_route)
                self._route_handlers[url_pattern] = block_route
                return f"Blocking requests matching: {url_pattern or '**/*'}"
            elif action == "allow":
                handler = self._route_handlers.pop(url_pattern, None)
                if handler:
                    await page.unroute(url_pattern or "**/*", handler)
                    return f"Removed intercept for: {url_pattern or '**/*'}"
                return f"No active intercept for: {url_pattern or '**/*'}"
            else:
                return f"Unknown action: {action} (use 'block' or 'allow')"
        except Exception as e:
            return f"Network intercept failed: {e}"

    async def list_tabs(self) -> str:
        """List all currently open named tabs and their URLs."""
        if not self._pages:
            return "No open tabs."
        lines = []
        for name, p in self._pages.items():
            try:
                closed = p.is_closed()
                lines.append(f"- {name}: {'<closed>' if closed else p.url}")
            except Exception:
                lines.append(f"- {name}: <unavailable>")
        return "\n".join(lines)

    async def close_tab(self, tab: str) -> str:
        page = self._pages.get(tab)
        if not page:
            return f"No such tab: '{tab}'"
        if tab == "default":
            return "Cannot close the default tab."
        try:
            if not page.is_closed():
                await page.close()
            del self._pages[tab]
            return f"Closed tab '{tab}'"
        except Exception as e:
            return f"Close tab failed: {e}"

    async def set_range_value(self, selector: str, value: float, tab: str = "default") -> str:
        """Set an <input type="range"> value on specified tab and fire input/change events."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."
        try:
            await page.eval_on_selector(
                selector,
                "(el, v) => {"
                "  if (!el) return false;"
                "  el.value = String(v);"
                "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                "  return true;"
                "}",
                value,
            )
            return f"Set range '{selector}' to {value}"
        except Exception as e:
            logger.error(f"Set range failed: {e}")
            return f"Set range failed: {e}"

    # ------------------------------------------------------------------
    # Parallel Multi-Tab Scraping & Fan-Out Search
    # ------------------------------------------------------------------

    async def parallel_scrape(
        self,
        urls: list[str] | str,
        max_concurrency: int = 4,
        timeout_ms: int = 15_000,
    ) -> str:
        """
        Scrapes multiple URLs concurrently using isolated browser tabs via asyncio.gather.
        Ideal for multi-product comparison, multi-article reading, or batch research.
        """
        if isinstance(urls, str):
            import re
            parsed_urls = re.findall(r'https?://[^\s,;"\'<>]+', urls)
            if not parsed_urls:
                parsed_urls = [u.strip() for u in urls.split(",") if u.strip().startswith("http")]
            urls = parsed_urls

        if not urls:
            return "No valid URLs provided for parallel scrape."

        # Cap batch size to 6 to prevent memory exhaustion
        target_urls = [u for u in urls if isinstance(u, str) and u.startswith("http")][:6]
        if not target_urls:
            return "No valid http/https URLs found."

        sem = asyncio.Semaphore(max(1, min(max_concurrency, 4)))

        async def _scrape_single(idx: int, target_url: str) -> dict[str, Any]:
            tab_id = f"par_tab_{idx}_{int(time.monotonic() * 1000) % 10000}"
            async with sem:
                try:
                    p = await self.get_page(tab_id)
                    if not p:
                        return {"url": target_url, "title": "Error", "content": "Could not allocate browser tab", "status": "fail"}
                    
                    try:
                        await p.goto(target_url, timeout=timeout_ms, wait_until="domcontentloaded")
                    except Exception as goto_err:
                        logger.debug("goto '%s' warning: %s", target_url, goto_err)

                    # Extract page title and distilled content
                    title = await p.title() or target_url
                    
                    # Distill DOM / extract readable text
                    text_content = await p.evaluate(
                        "() => {"
                        "  const clone = document.body.cloneNode(true);"
                        "  clone.querySelectorAll('script, style, svg, nav, footer, noscript, iframe').forEach(e => e.remove());"
                        "  return clone.innerText.replace(/\\s+/g, ' ').trim().slice(0, 2000);"
                        "}"
                    )
                    
                    return {
                        "url": target_url,
                        "title": title.strip()[:100],
                        "content": text_content.strip(),
                        "status": "ok",
                    }
                except Exception as e:
                    return {
                        "url": target_url,
                        "title": "Failed to scrape",
                        "content": f"[Error: {e}]",
                        "status": "error",
                    }
                finally:
                    try:
                        await self.close_tab(tab_id)
                    except Exception:
                        pass

        tasks = [_scrape_single(i, u) for i, u in enumerate(target_urls)]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        formatted_outputs = []
        for idx, res in enumerate(raw_results, 1):
            if isinstance(res, dict):
                formatted_outputs.append(
                    f"### [{idx}] {res.get('title', 'Page')}\n"
                    f"- **URL**: {res.get('url')}\n"
                    f"- **Status**: {res.get('status')}\n"
                    f"- **Content**:\n{res.get('content', '')[:1200]}\n"
                )
            elif isinstance(res, Exception):
                formatted_outputs.append(f"### [{idx}] Error: {res}\n")

        return (
            f"⚡ **Parallel Scrape Results ({len(target_urls)} pages extracted concurrently)**:\n\n"
            + "\n---\n\n".join(formatted_outputs)
        )

    async def parallel_search(
        self,
        query: str = "",
        search_query: str = "",
        max_results: int = 3,
        tab: str = "default",
        **kwargs: Any,
    ) -> str:
        """
        Executes a web search and immediately fans out to scrape the top organic result pages
        concurrently in parallel browser tabs, returning full live content from all top pages.
        """
        effective_query = (query or search_query or kwargs.get("q") or kwargs.get("search") or "").strip()
        if not effective_query:
            return "Search query cannot be empty."

        import urllib.parse
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(effective_query)}"
        
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."

        try:
            await page.goto(search_url, timeout=12_000, wait_until="domcontentloaded")
            
            # Extract organic result URLs from DuckDuckGo HTML
            extracted_links = await page.evaluate(
                "() => {"
                "  const links = [];"
                "  const anchors = document.querySelectorAll('a.result__url, a.result__snippet, .result__title a');"
                "  for (const a of anchors) {"
                "    let href = a.href || '';"
                "    if (href.includes('uddg=')) {"
                "      try {"
                "        const match = href.match(/uddg=([^&]+)/);"
                "        if (match) href = decodeURIComponent(match[1]);"
                "      } catch (e) {}"
                "    }"
                "    if (href.startsWith('http') && !href.includes('duckduckgo.com') && !links.includes(href)) {"
                "      links.push(href);"
                "    }"
                "  }"
                "  return links.slice(0, 6);"
                "}"
            )
        except Exception as e:
            return f"Search navigation failed: {e}"

        target_urls = [u for u in extracted_links if isinstance(u, str) and u.startswith("http")][:max_results]
        if not target_urls:
            try:
                from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    for r in ddgs.text(query, max_results=max_results):
                        href = r.get("href") or r.get("link")
                        if href and href.startswith("http"):
                            target_urls.append(href)
            except Exception:
                pass

        if not target_urls:
            return f"No organic search result links could be extracted for query: '{query}'."

        # Fan out concurrently across isolated tabs
        scrape_summary = await self.parallel_scrape(urls=target_urls, max_concurrency=len(target_urls))
        return (
            f"🔍 **Parallel Fan-Out Search for '{query}'**\n"
            f"Extracted top {len(target_urls)} organic links and scraped in parallel:\n\n"
            f"{scrape_summary}"
        )

    # Alias for set_range_value
    set_range = set_range_value

    # ------------------------------------------------------------------
    # CAPTCHA Detection
    # ------------------------------------------------------------------

    async def _check_captcha(self, tab: str = "default") -> None:
        """Check page content for CAPTCHA signals on specified tab."""
        page = await self.get_page(tab)
        if not page:
            return
        try:
            # 1. Page title checks (Cloudflare / challenge pages)
            title = await page.title()
            if title and any(x in title for x in ["Just a moment...", "Attention Required!", "Security Challenge"]):
                await self._emit_captcha("cloudflare challenge", tab=tab)

            # 2. Known blocking challenge selectors (ignore invisible background recaptcha scripts)
            captcha_selectors = {
                '#cf-challenge-form': "cloudflare challenge",
                '#challenge-running': "cloudflare challenge",
            }
            for selector, signal in captcha_selectors.items():
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    await self._emit_captcha(signal, tab=tab)

        except RuntimeError:
            raise
        except Exception as e:
            logger.debug(f"Captcha check error: {e}")

    async def _dismiss_consent_banners(self, tab: str = "default") -> None:
        """Best-effort click on the first visible cookie/consent banner
        button. Never raises, never blocks a navigation on failure — this
        is a courtesy pass, not a requirement. Skips entirely once the
        persistent profile already has consent cookies set (nothing will
        be visible), so it's cheap on repeat visits."""
        page = await self.get_page(tab)
        if not page:
            return
        for sel in _CONSENT_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=1_500)
                    logger.debug(f"Dismissed consent banner via '{sel}' on tab '{tab}'")
                    return
            except Exception:
                continue

    async def _emit_captcha(self, signal: str, tab: str = "default") -> None:
        logger.warning(f"CAPTCHA detected: '{signal}' on tab '{tab}'")
        page = await self.get_page(tab)
        page_url = page.url if page else ""
        if self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.WSMessage(
                v=ws_protocol.PROTOCOL_VERSION,
                type=ws_protocol.ServerMessageType.CAPTCHA_DETECTED,
                payload={"signal": signal, "url": page_url, "tab": tab},
            ))
        raise RuntimeError(
            f"CAPTCHA detected ({signal}). Please solve it manually, then retry."
        )

    # ------------------------------------------------------------------
    # Vision Fallback
    # ------------------------------------------------------------------

    async def _vision_click(self, target_description: str, tab: str = "default") -> str:
        """Use Gemini Vision to find and click an element by description on specified tab."""
        page = await self.get_page(tab)
        if not page:
            return "Error: Browser not available."

        screenshot_b64 = await self.get_screenshot_b64(tab=tab)
        if not screenshot_b64 or not self.ai_handler:
            return f"Vision fallback unavailable for: {target_description}"

        prompt = (
            f"Look at this screenshot. Find the element described as: '{target_description}'. "
            f"Return ONLY a JSON object: {{\"x\": <coord_x>, \"y\": <coord_y>, \"normalized\": true}} "
            f"using 0 to 1000 normalized coordinate scale (where 0,0 is top-left and 1000,1000 is bottom-right), "
            f"or {{\"error\": \"not found\"}} if not visible."
        )
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}}
            ]}
        ]

        try:
            response = await self.ai_handler.generate(messages, task="vision", require_json=True)
            parsed = self.ai_handler.try_parse_json(response.text)
            if parsed and "x" in parsed and "y" in parsed:
                x_val = float(parsed["x"])
                y_val = float(parsed["y"])
                
                # Normalize 0..1000 coordinates relative to viewport size
                vp = page.viewport_size or {"width": 1280, "height": 720}
                if parsed.get("normalized", True) and 0 <= x_val <= 1000 and 0 <= y_val <= 1000:
                    click_x = int((x_val / 1000.0) * vp["width"])
                    click_y = int((y_val / 1000.0) * vp["height"])
                else:
                    click_x, click_y = int(x_val), int(y_val)
                    
                await page.mouse.click(click_x, click_y)
                await self._check_captcha(tab=tab)
                return f"Vision click at ({click_x}, {click_y}) [normalized from ({x_val}, {y_val})] for '{target_description}'"
            return f"Vision could not locate: {target_description}"
        except RuntimeError:
            raise
        except Exception as e:
            return f"Vision click failed: {e}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_window_visible(self) -> None:
        """On Windows, ensure the Chromium/Chrome browser window is restored and visible on the desktop."""
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            IsWindowVisible = user32.IsWindowVisible
            ShowWindow = user32.ShowWindow
            SetForegroundWindow = user32.SetForegroundWindow
            BringWindowToTop = user32.BringWindowToTop
            SetWindowPos = user32.SetWindowPos
            IsIconic = user32.IsIconic

            SW_RESTORE = 9
            SW_SHOW = 5
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040

            def foreach_window(hwnd, lParam):
                length = GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()
                    if any(k in title for k in ["chrome", "chromium"]):
                        try:
                            if IsIconic(hwnd):
                                ShowWindow(hwnd, SW_RESTORE)
                            else:
                                ShowWindow(hwnd, SW_SHOW)
                            # Z-Order trick: raise to TOPMOST temporarily to bypass Windows Foreground Lock, then remove TOPMOST
                            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                            SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                            BringWindowToTop(hwnd)
                            SetForegroundWindow(hwnd)
                        except Exception:
                            pass
                return True

            cb_func = EnumWindowsProc(foreach_window)
            EnumWindows(cb_func, 0)
        except Exception as e:
            logger.debug(f"Could not ensure window visible via Win32: {e}")

    async def _ensure_ready(self) -> bool:
        """Ensure browser is started, attempting lazy start if needed."""
        if self._playwright_available is False:
            return False
        if self._browser:
            if hasattr(self._browser, "is_connected") and self._browser.is_connected():
                return True
            if hasattr(self._browser, "browser") and self._browser.browser and self._browser.browser.is_connected():
                return True
        # Smart Reuse: First attempt to attach to an existing Chrome browser via CDP
        if self.config.get("use_cdp", True):
            try:
                if await self.attach_cdp():
                    logger.info("BrowserController: Successfully attached to existing open Chrome browser!")
                    return True
            except Exception:
                pass
        return await self.start(try_cdp=False)
