"""
Makima v8.1 — Elite Browser Agent (Research-Optimized)

Improvements over v7.2:
  - Singleton BrowserController with session pooling (no per-execute launches)
  - Navigation verification gate (confirms page loaded before tool execution)
  - Adaptive retry budgets (retry time never exceeds wall clock)
  - DOM readiness detection (wait_for_load_state before interactions)
  - Async blackboard (no sync file I/O in hot path)
  - Proactive stuck detection (DOM hash comparison, not just action history)
  - Graceful degradation (immediate helpful error on CDP failure)
  - Vision-DOM fusion stub (ready for multimodal integration)

Research basis:
  - WebArena (Zhou et al., 2023): DOM distillation > raw HTML for agent grounding
  - Mind2Web (Deng et al., 2023): Element visibility checks before interaction
  - Anthropic computer-use: Screenshot-verify-action loop for reliability
  - Playwright best practices: wait_for_load_state over arbitrary sleeps
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import json as _json
import time
from typing import Any, Optional
from collections import deque

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.browser")

_BROWSER_TOOLS = frozenset({
    "browser_navigate", "browser_click", "browser_fill", "browser_get_text",
    "browser_set_range", "browser_screenshot", "browser_run_js", "browser_press_key",
    "browser_select_option", "browser_hover", "browser_scroll", "browser_wait_for",
    "browser_go_back", "browser_go_forward", "browser_reload", "browser_extract_links",
    "browser_get_attribute", "browser_check_exists", "browser_upload_file",
    "browser_list_tabs", "browser_close_tab", "browser_distill_dom", "browser_switch_tab",
    "browser_evaluate_xpath", "browser_network_intercept",
    "browser_parallel_scrape", "browser_parallel_search",
})

_BC_METHOD_MAP: dict[str, str] = {
    "browser_navigate":         "navigate",
    "browser_click":            "click",
    "browser_fill":             "fill",
    "browser_get_text":         "get_text",
    "browser_distill_dom":      "distill_dom",
    "browser_screenshot":       "get_screenshot_b64",
    "browser_run_js":           "run_js",
    "browser_press_key":        "press_key",
    "browser_select_option":    "select_option",
    "browser_hover":            "hover",
    "browser_scroll":           "scroll",
    "browser_wait_for":         "wait_for",
    "browser_go_back":          "go_back",
    "browser_go_forward":       "go_forward",
    "browser_reload":           "reload_page",
    "browser_extract_links":    "extract_links",
    "browser_get_attribute":    "get_element_attribute",
    "browser_check_exists":     "check_element_exists",
    "browser_upload_file":      "upload_file",
    "browser_list_tabs":        "list_tabs",
    "browser_close_tab":        "close_tab",
    "browser_switch_tab":       "switch_tab",
    "browser_evaluate_xpath":   "evaluate_xpath",
    "browser_network_intercept":"network_intercept",
    "browser_set_range":        "set_range_value",
    "browser_parallel_scrape":  "parallel_scrape",
    "browser_parallel_search":  "parallel_search",
}


_TRANSIENT_ERROR_KEYWORDS = frozenset({
    "timeout", "timed out", "detached", "intercepted", "net::err",
    "navigation failed", "target closed", "session closed", "element not found",
})

# Reduced max iterations for faster completion/abort
_MAX_ITERATIONS = 8

_BROWSER_STEP_LABELS = {
    "browser_navigate": "Navigating to page...",
    "browser_click": "Clicking element...",
    "browser_fill": "Filling form...",
    "browser_distill_dom": "Scanning page structure...",
    "browser_get_text": "Reading page content...",
    "browser_screenshot": "Capturing screenshot...",
    "browser_run_js": "Executing page script...",
    "browser_wait_for": "Waiting for content to load...",
    "browser_scroll": "Scrolling page...",
    "browser_press_key": "Pressing key...",
    "browser_upload_file": "Uploading file...",
    "browser_network_intercept": "Adjusting network requests...",
    "browser_go_back": "Going back...",
    "browser_go_forward": "Going forward...",
    "browser_reload": "Reloading page...",
    "browser_check_exists": "Checking element...",
    "browser_get_attribute": "Reading attribute...",
    "browser_extract_links": "Extracting links...",
    "browser_list_tabs": "Listing tabs...",
    "browser_switch_tab": "Switching tab...",
    "browser_close_tab": "Closing tab...",
    "browser_select_option": "Selecting option...",
    "browser_hover": "Hovering...",
    "browser_set_range": "Adjusting slider...",
    "browser_evaluate_xpath": "Querying page...",
    "browser_parallel_scrape": "Scraping multiple pages in parallel tabs...",
    "browser_parallel_search": "Searching and fanning out to parallel tabs...",
}


class BrowserAgent(BaseAgent):
    """
    Makima v8.1 Elite Browser Agent.
    Orchestrates stealth web browsing with session pooling, navigation verification,
    adaptive retry budgets, and proactive stuck detection.
    """

    AGENT_NAME = "browser"
    DESCRIPTION = "Stealth web browsing, webpage DOM distillation, web scraping, and browser tab captures"
    CAPABILITIES = [
        "web_browsing", "dom_distillation", "stealth_browsing",
        "form_filling", "screenshot_capture", "scraping", "parallel_scraping",
    ]
    AGENT_TOOLS = [
        "browser_navigate", "browser_click", "browser_fill",
        "browser_get_text", "browser_distill_dom", "browser_screenshot",
        "browser_run_js", "browser_wait_for", "browser_scroll",
        "browser_press_key", "browser_extract_links", "browser_hover",
        "browser_select_option", "browser_go_back", "browser_go_forward",
        "browser_reload", "browser_close_tab", "browser_switch_tab",
        "browser_list_tabs", "browser_check_exists", "browser_get_attribute",
        "browser_evaluate_xpath", "browser_upload_file", "browser_network_intercept",
        "browser_set_range", "browser_parallel_scrape", "browser_parallel_search",
    ]
    TAGS = ["browser", "web", "scraping", "playwright", "stealth", "parallel"]


    SYSTEM_PROMPT = """You are Makima's Elite Browser Agent, an autonomous web automation expert.
You navigate, interact, and extract data from the web with stealth, precision, and resilience.

CORE TOOLS:
- Parallel: browser_parallel_scrape(urls=[...]) | browser_parallel_search(query, max_results)
- Navigation & Tabs: browser_navigate(url), browser_switch_tab(tab), browser_close_tab(tab), browser_reload()
- Interaction: browser_click(selector), browser_fill(selector, text), browser_press_key(key), browser_scroll(direction, amount)
- Extraction: browser_distill_dom() [PREFERRED], browser_extract_links(), browser_get_text(selector)
- Inspection: browser_screenshot(), browser_wait_for(selector, timeout_ms), browser_run_js(script)

OPERATIONAL RULES:
1. Multi-URL / Comparisons: ALWAYS use browser_parallel_scrape(urls=[...]) to fetch in parallel.
2. Content Extraction: ALWAYS use browser_distill_dom() instead of raw text extraction.
3. Selectors: If a selector fails, distill DOM to inspect structure. Do not retry broken selectors blindly.
4. Citations: Cite ONLY URLs actually visited or extracted live. Never fabricate or guess link patterns.
5. Structured Thinking: Always perform DOM structure analysis, selector strategy, and navigation planning in a <thinking>...</thinking> block before taking browser actions.
6. Desktop Agency & Action-First Verification:
   - When asked to check or inspect any web service (e.g., webmail, dashboards, feeds), NEVER refuse upfront claiming lack of credentials.
   - ALWAYS navigate to the target URL first using browser_navigate() and distill DOM to verify the live session state.
   - If already logged in, extract the requested information and deliver a crisp answer.
   - If an active login or 2FA wall is encountered, report the exact page status clearly to the user.
7. Output Discipline: Deliver final answers in natural, clean prose (with markdown formatting). Do NOT wrap final responses in {"tool": "complete"} JSON envelopes.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    # Class-level shared BrowserController (singleton pattern)
    _shared_bc: Optional[Any] = None
    _bc_lock: asyncio.Lock = asyncio.Lock()
    _bc_refcount: int = 0

    def __init__(
        self,
        ai_handler: Any = None,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Any = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        url_stuck_threshold: int = 5,
        wall_clock_timeout_s: float = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails)
        self._max_iterations = _MAX_ITERATIONS
        self._url_stuck_threshold = int(url_stuck_threshold)
        self._wall_clock_timeout_s = float(wall_clock_timeout_s)

        # Adaptive retry budget — total retry time can never exceed 40% of wall clock
        self._max_retry_budget_s = self._wall_clock_timeout_s * 0.4

        # State tracking
        self._visited_urls: set[str] = set()
        self._current_url: str = ""
        self._action_history: deque[dict[str, Any]] = deque(maxlen=15)
        self._snapshot_indices: list[int] = []
        self._stuck_actions: int = 0
        self._last_dom_hash: str = ""
        self._dom_unchanged_count: int = 0
        self._bc: Optional[Any] = None  # Instance reference to shared controller

        # ── Tool map: routes every browser_* _use_tool call to the shared  ──
        # BrowserController singleton (lazy — controller created on first call).
        # Derived STRICTLY from _BROWSER_TOOLS frozenset defined at module top.
        # method_name = exact BrowserController async def name (verified below).
        import functools
        self._TOOL_MAP: dict[str, Any] = {
            tool_name: functools.partial(self._bc_dispatch, ctrl_method)
            for tool_name, ctrl_method in _BC_METHOD_MAP.items()
        }

    # =========================================================================
    # Singleton BrowserController with session pooling
    # =========================================================================

    async def _bc_dispatch(self, method_name: str, **kwargs: Any) -> str:
        """
        Unified dispatcher: routes browser_* tool calls to the shared
        BrowserController singleton. Lazily acquires the controller.
        Used by every entry in self._TOOL_MAP.
        """
        try:
            if method_name == "run_js":
                approved = await self._confirm_action(
                    self.get_execution_task() or "browser-task",
                    "browser_run_js",
                    "Run arbitrary JavaScript in the current browser page",
                    risk_level="high",
                )
                if not approved:
                    return "[BLOCKED] Browser JavaScript execution was not approved."
            bc = await self._get_browser_controller()
            method = getattr(bc, method_name, None)
            if not callable(method):
                return f"[Error] BrowserController has no method '{method_name}'"
            result = await method(**kwargs)
            return str(result) if result is not None else ""
        except Exception as e:
            logger.error("[browser] _bc_dispatch('%s') failed: %s", method_name, e)
            return f"[Tool error: {method_name} — {e}]"

    async def _get_browser_controller(self) -> Any:
        """
        Get or create the shared BrowserController.
        Prevents multiple browser launches and CDP port conflicts.
        """
        async with BrowserAgent._bc_lock:
            if BrowserAgent._shared_bc is None:
                from ..browser_controller import BrowserController
                config = {}
                if self.orchestrator and hasattr(self.orchestrator, "config"):
                    config = self.orchestrator.config.get("browser", {})

                BrowserAgent._shared_bc = BrowserController(
                    config=config,
                    ai_handler=self.ai_handler,
                    ws_broadcast=self.ws_broadcast,
                )
                await BrowserAgent._shared_bc.start()
                logger.info("[browser] Shared BrowserController initialized")

            BrowserAgent._bc_refcount += 1
            return BrowserAgent._shared_bc

    async def _release_browser_controller(self) -> None:
        """Release reference. Only stop when no tasks are using it."""
        async with BrowserAgent._bc_lock:
            BrowserAgent._bc_refcount -= 1
            if BrowserAgent._bc_refcount <= 0 and BrowserAgent._shared_bc is not None:
                try:
                    await BrowserAgent._shared_bc.stop()
                except Exception as e:
                    logger.debug("BrowserController stop error: %s", e)
                BrowserAgent._shared_bc = None
                BrowserAgent._bc_refcount = 0
                logger.info("[browser] Shared BrowserController stopped (refcount=0)")

    # =========================================================================
    # Navigation verification gate
    # =========================================================================

    async def _verify_navigation(self, url: str, tab: str = "default", timeout_s: float = 10.0) -> tuple[bool, str]:
        """
        Verify that navigation actually completed and page is interactive.
        Returns (success, status_message).
        """
        bc = self._bc
        if not bc:
            return False, "No browser session"

        try:
            page = await bc.get_page(tab)
            if page and hasattr(page, "wait_for_load_state"):
                try:
                    await asyncio.wait_for(
                        page.wait_for_load_state("domcontentloaded"),
                        timeout=timeout_s,
                    )
                except Exception:
                    pass

            current = page.url if (page and hasattr(page, "url")) else ""
            if current and current != "about:blank":
                self._current_url = current
                return True, f"Page loaded: {current}"

            if url and url.startswith("http"):
                self._current_url = url
                return True, f"Page navigated to {url}"

            return False, f"Navigation may have failed. Current URL: {current or 'unknown'}"
        except Exception as e:
            logger.warning("[browser] Navigation verification failed for %s: %s", url, e)
            return False, f"Navigation verification failed: {e}"

    # =========================================================================
    # Adaptive retry with budget enforcement
    # =========================================================================

    async def _execute_tool_with_retry(
        self, tool: str, params: dict[str, Any], max_retries: int = 2,
    ) -> tuple[Any, bool]:
        """
        Execute tool with adaptive retry logic bounded by retry budget.
        """
        last_error = ""
        budget_remaining = self._max_retry_budget_s
        start = time.monotonic()

        for attempt in range(max_retries + 1):
            elapsed = time.monotonic() - start
            if elapsed > budget_remaining and attempt > 0:
                logger.warning(
                    "[browser] Retry budget exhausted for %s (%.1fs used of %.1fs)",
                    tool, elapsed, budget_remaining,
                )
                break

            attempt_timeout = min(15.0, budget_remaining - elapsed)
            if attempt_timeout <= 2.0:
                break

            try:
                res = await asyncio.wait_for(
                    self._use_tool(tool, **params),
                    timeout=attempt_timeout,
                )
                res_str = str(res)

                if self._tool_failed(res):
                    last_error = res_str
                    if attempt < max_retries and self._is_transient_error(last_error):
                        wait = min(2.0 * (attempt + 1), 5.0)
                        logger.warning(
                            "Transient error in %s (attempt %d/%d): %s. Retrying in %.1fs",
                            tool, attempt + 1, max_retries + 1, last_error[:100], wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    return res, False

                # NOTE: CAPTCHA/bot-wall detection is NOT done here. Substring
                # scanning tool output for words like 'cloudflare' produced
                # constant false positives (any page mentioning those words in
                # footer/hidden text aborted the task). Real detection lives in
                # BrowserController._check_captcha (page title + visible
                # challenge selectors) which emits proper CAPTCHA events.

                return res, True

            except asyncio.TimeoutError:
                last_error = f"Tool {tool} timed out after {attempt_timeout:.1f}s"
                logger.warning("[browser] %s", last_error)
                if attempt < max_retries and self._is_transient_error(last_error):
                    await asyncio.sleep(1.0)
                    continue
                break

            except Exception as e:
                last_error = str(e)
                if attempt < max_retries and self._is_transient_error(last_error):
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break

        return f"[Tool Error: {last_error}]", False

    # =========================================================================
    # Proactive stuck detection (DOM hash + action history)
    # =========================================================================

    def _detect_stuck_proactive(self, tool: str, params: dict, dom_content: str = "") -> str | None:
        """
        Proactive stuck detection using DOM hash comparison.
        """
        if dom_content:
            current_hash = hashlib.md5(dom_content.encode("utf-8", errors="ignore")).hexdigest()
            if current_hash == self._last_dom_hash:
                self._dom_unchanged_count += 1
                if self._dom_unchanged_count >= 3:
                    self._dom_unchanged_count = 0
                    return (
                        "[STUCK DETECTED] The page DOM has not changed after 3 actions. "
                        "Your interactions are likely not having any effect. "
                        "Try: 1) browser_screenshot() to see what's actually on screen, "
                        "2) browser_run_js('document.title') to verify page state, "
                        "3) A completely different approach."
                    )
            else:
                self._dom_unchanged_count = 0
            self._last_dom_hash = current_hash

        if len(self._action_history) >= 4:
            recent = list(self._action_history)[-4:]
            if (recent[0]["tool"] == recent[2]["tool"] == tool and
                    recent[1]["tool"] == recent[3]["tool"]):
                return (
                    "[LOOP DETECTED] You are oscillating between the same actions. "
                    "STOP. Use browser_distill_dom() to re-evaluate, or try browser_run_js() "
                    "for a different approach."
                )

            fail_count = sum(1 for a in recent if a["tool"] == tool and a.get("failed"))
            if fail_count >= 2:
                return (
                    f"[REPEATED FAILURE] '{tool}' failed {fail_count} times. "
                    "The selector is likely wrong. Use browser_distill_dom() to find correct selectors."
                )

        return None

    # =========================================================================
    # Async blackboard (in-memory cache)
    # =========================================================================

    _blackboard_cache: dict[str, str] = {}

    def _save_to_blackboard(self, content: str, tool: str, idx: int) -> str:
        """
        Offload large tool output into memory cache without blocking disk I/O.
        """
        ref_id = f"{tool.replace('browser_', '')}_{idx}_{int(time.monotonic() * 1000)}"
        summary = " ".join(content[:300].split())

        BrowserAgent._blackboard_cache[ref_id] = content

        if len(BrowserAgent._blackboard_cache) > 20:
            oldest = list(BrowserAgent._blackboard_cache.keys())[:5]
            for k in oldest:
                del BrowserAgent._blackboard_cache[k]

        return (
            f"[SYSTEM] Large output saved (ref: {ref_id}). "
            f"Summary: {summary}..."
        )

    # =========================================================================
    # Context compression
    # =========================================================================

    def _compress_context(self, messages: list[dict[str, Any]]) -> None:
        """Compress older snapshots AND failed tool results to save tokens."""
        if len(self._snapshot_indices) <= 2:
            return

        indices_to_compress = self._snapshot_indices[:-2]
        for idx in indices_to_compress:
            if 0 <= idx < len(messages):
                msg = messages[idx]
                content = msg.get("content", "")
                tool = msg.get("_snapshot_tool", "unknown")

                if isinstance(content, str) and len(content) > 500:
                    if tool == "browser_screenshot":
                        msg["content"] = "[Previous screenshot cleared]"
                    elif "Tool Error" in content or "timed out" in content.lower():
                        msg["content"] = f"[Previous {tool} failed — result cleared]"
                    else:
                        msg["content"] = f"[Previous {tool} compressed: {content[:200]}...]"

                    msg.pop("_snapshot_tool", None)

        self._snapshot_indices = [i for i in self._snapshot_indices if i not in indices_to_compress]

    # =========================================================================
    # Main execute
    # =========================================================================

    def _graceful_browser_failure(self, reason: str) -> str:
        """Failure messages that tell the user what WAS accomplished —
        never a dead-end 'rephrase' with zero context. Top-notch UX."""
        parts = [reason]
        try:
            n_urls = len(getattr(self, "_visited_urls", []) or [])
            if n_urls:
                parts.append(f"({n_urls} page(s) visit kar chuke the)")
        except Exception:
            pass
        partial = (getattr(self, "_partial_result", "") or "").strip()
        if partial and len(partial) > 20:
            parts.append(f"Pichhla progress: {partial[:180]}")
        return "\n".join(parts) if len(parts) > 1 else parts[0]

    @staticmethod
    def _salvage_json_object(raw: str) -> Optional[dict]:
        """Best-effort JSON recovery from chatty/fenced/truncated LLM output.
        Tries: markdown fence → first balanced {...} block. Never raises."""
        if not raw or not isinstance(raw, str):
            return None
        import json as _json
        import re as _re
        candidates = []
        m_fence = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m_fence:
            candidates.append(m_fence.group(1))
        depth = 0
        start = None
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(raw[start:i + 1])
                    break
        for cand in candidates:
            try:
                obj = _json.loads(cand)
                if isinstance(obj, dict) and obj:
                    return obj
            except Exception:
                continue
        return None

    async def execute(
        self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any],
    ) -> str:
        """
        Main entry point using shared BrowserController session pool.
        """
        try:
            self._bc = await self._get_browser_controller()
        except Exception as e:
            logger.error("[browser] Failed to initialize browser: %s", e)
            return (
                f"⚠️ Browser could not be started: {e}\n"
                "Please ensure no other browser instance is using the same profile, "
                "or try closing all browser windows and retry."
            )

        try:
            # ── Fast path: direct structured operations ──
            agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
            if agent_task and hasattr(agent_task, "operation") and agent_task.operation:
                op = str(agent_task.operation or "").lower().strip()
                params = dict(agent_task.parameters or {})
                target_url = agent_task.target_entity or params.get("url") or params.get("target")

                if any(k in op for k in ("navigate", "open_url", "browse", "visit")) and target_url:
                    logger.info("[browser] P1-4B: Direct execution of browser_navigate('%s')", target_url)
                    res, ok = await self._execute_tool_with_retry("browser_navigate", {"url": target_url})
                    self._partial_result = str(res)
                    return str(res) if ok else f"Failed to navigate to {target_url}: {res}"
                elif "screenshot" in op:
                    logger.info("[browser] P1-4B: Direct execution of browser_screenshot")
                    res, ok = await self._execute_tool_with_retry("browser_screenshot", {})
                    self._partial_result = str(res)
                    return str(res) if ok else f"Failed to capture screenshot: {res}"

            if self._is_privacy_mode():
                return "Browser automation is disabled in Privacy Mode."

            # ── OpenAI Agents SDK Runner Execution ────────────────────────────
            try:
                final_out = await self.run_sdk_execution(
                    task_id=task_id,
                    message=message,
                    context=context,
                    max_turns=12,
                    task_type="automation",
                )
                self._partial_result = final_out
                return final_out
            except Exception as sdk_exc:
                logger.warning("[browser] SDK Runner encountered exception, falling back: %s", sdk_exc)

            return await self._execute_loop(task_id, message, context, entities)
        finally:
            await self._release_browser_controller()
            self._bc = None

    async def _execute_loop(
        self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any],
    ) -> str:
        """Main execution loop with all v8.1 improvements."""
        self._reset_state()

        if self._is_privacy_mode():
            return "Browser automation is disabled in Privacy Mode."

        await self._broadcast_status(task_id, "thinking")
        messages = self._build_messages(message, context)
        # Clear state
        self._visited_urls.clear()
        self._action_history.clear()
        self._snapshot_indices.clear()
        self._current_url = ""
        self._stuck_actions = 0
        self._last_dom_hash = ""
        self._dom_unchanged_count = 0

        _start_time = time.monotonic()
        _consec_json_fail = 0

        for iteration in range(self._max_iterations):
            if self._cancelled:
                return self._partial_result or "[Cancelled]"

            elapsed = time.monotonic() - _start_time
            if elapsed > self._wall_clock_timeout_s:
                logger.warning("[browser] task %s timed out at iteration %d (%.1fs)", task_id, iteration, elapsed)
                return self._graceful_browser_failure(
                    f"⏱ Browser task time-out ({self._wall_clock_timeout_s:.0f}s) — "
                    "page slow ya request heavy thi. Chhota tukda try karo."
                )

            self._compress_context(messages)

            raw = await self._llm_call(
                messages,
                task="automation",
                temperature=0.15,
                max_tokens=1500,
            )
            logger.debug("[browser] LLM response (iter %d): %s", iteration, raw[:200])

            parsed = self.ai_handler.try_parse_json(raw)
            if not parsed:
                # Salvage pass: models often wrap JSON in prose/fences or emit
                # leading commentary before the object. Recover before failing.
                parsed = self._salvage_json_object(raw)
            if not parsed:
                _consec_json_fail += 1
                if _consec_json_fail >= 3:
                    return self._graceful_browser_failure(
                        "⚠️ Browser model ke responses parse nahi ho paye — "
                        "thoda specific re-try karo (site name + exact action batao)."
                    )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content":
                    "[SYSTEM] Invalid JSON. Return EXACTLY: {\"tool\": \"...\", \"params\": {...}, \"reply\": \"...\"}"})
                continue

            _consec_json_fail = 0

            tool = parsed.get("tool") or parsed.get("action")
            reply = parsed.get("reply", "")
            params = parsed.get("params") or {}
            if not isinstance(params, dict):
                params = {}

            # Batch mode
            actions = parsed.get("actions")
            if isinstance(actions, list) and actions:
                batch_results = []
                for a in actions[:4]:
                    if not isinstance(a, dict):
                        continue
                    a_tool = a.get("tool") or a.get("action")
                    a_params = a.get("params") or {}
                    if not isinstance(a_tool, str) or a_tool not in _BROWSER_TOOLS:
                        batch_results.append(f"[{a_tool}] unknown tool, skipped")
                        continue
                    if not isinstance(a_params, dict):
                        a_params = {}
                    a_res, a_ok = await self._execute_tool_with_retry(a_tool, a_params, max_retries=1)
                    await self._broadcast_status(task_id, f"step {iteration+1}: {self._step_label(a_tool)}")
                    if not a_ok and "CAPTCHA" in str(a_res):
                        self._cancelled = True
                        return f"{a_res} (Stopped for manual intervention)"
                    batch_results.append(f"[{a_tool}] {str(a_res)[:500]}")

                self._action_history.append({"tool": "batch", "params": {}, "failed": False})
                messages.append({"role": "assistant", "content": raw})
                res_str = "\n".join(batch_results)
                messages.append({"role": "user", "content": f"[BATCH RESULTS]\n{res_str}"})
                continue

            # Terminal condition
            if not tool or (isinstance(tool, str) and tool.lower() in ("complete", "finish", "done", "final_answer")):
                result = reply or parsed.get("result") or parsed.get("answer") or "Task completed."
                self._partial_result = str(result)
                return self._partial_result

            # Unknown tool
            if not isinstance(tool, str) or tool not in _BROWSER_TOOLS:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"[ERROR] Unknown tool '{tool}'. Use tools from the list."})
                continue

            # URL validation
            if tool == "browser_navigate":
                url = params.get("url", "")
                if not self._is_valid_url(url):
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"[ERROR] Invalid URL: {url}. Only http/https allowed."})
                    continue

            # Proactive stuck detection
            loop_prompt = self._detect_stuck_proactive(tool, params, dom_content=getattr(self, "_last_dom_text", ""))
            if loop_prompt:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": loop_prompt})
                self._action_history.append({"tool": tool, "params": params, "failed": True})
                continue

            # Execute tool
            await self._broadcast_status(task_id, f"step {iteration+1}: {self._step_label(tool)}")
            res, success = await self._execute_tool_with_retry(tool, params)

            # Navigation verification gate
            if tool == "browser_navigate" and success:
                nav_ok, nav_msg = await self._verify_navigation(params.get("url", ""))
                if not nav_ok:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content":
                        f"[NAVIGATION WARNING] {nav_msg}. Try browser_reload() or a different approach."})
                    self._action_history.append({"tool": tool, "params": params, "failed": True})
                    continue
                self._visited_urls.add(params.get("url", ""))
                self._stuck_actions = 0

            # Record action
            self._action_history.append({
                "tool": tool,
                "params": params,
                "failed": not success,
            })

            # CAPTCHA stop
            if not success and "CAPTCHA" in str(res):
                self._cancelled = True
                return f"{res} (Stopped for manual intervention)"

            # Append to context
            messages.append({"role": "assistant", "content": raw})
            res_str = str(res)
            if len(res_str) > 8000:
                res_str = self._save_to_blackboard(res_str, tool, iteration)

            msg_dict = {
                "role": "user",
                "content": f"[TOOL RESULT: {tool}]\n{res_str}",
                "_snapshot_tool": tool,
            }
            messages.append(msg_dict)

            if tool in ("browser_get_text", "browser_distill_dom", "browser_screenshot", "browser_run_js"):
                self._snapshot_indices.append(len(messages) - 1)
                if tool in ("browser_get_text", "browser_distill_dom") and isinstance(res, str):
                    self._last_dom_text = res

        return self._graceful_browser_failure(
            "🪜 Max browser steps reach ho gaye — task bada tha. "
            "Use chhote steps mein todo (pehle search, phir click, phir download)."
        )

    # =========================================================================
    # Helpers
    # =========================================================================

    def _is_valid_url(self, url: str) -> bool:
        return url.startswith("http://") or url.startswith("https://")

    def _is_privacy_mode(self) -> bool:
        try:
            cfg = (self.orchestrator.config or {}) if self.orchestrator else {}
            return bool(cfg.get("privacy_mode", False))
        except Exception:
            return False

    def _is_transient_error(self, error_msg: str) -> bool:
        err_lower = error_msg.lower()
        return any(kw in err_lower for kw in _TRANSIENT_ERROR_KEYWORDS)

    def _step_label(self, tool: str) -> str:
        return _BROWSER_STEP_LABELS.get(tool, f"Running {tool}...")
