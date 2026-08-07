"""
Makima v7.2 — Elite Browser Agent
Upgrades: Advanced context management, semantic stuck-loop detection, 
stealth evasion orchestration, vision-DOM fusion, and resilient tool execution.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import json as _json
import time
from typing import Any, cast
from collections import deque

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.browser")

_BROWSER_TOOLS = frozenset({
    "browser_navigate", "browser_click", "browser_fill", "browser_get_text",
    "browser_set_range", "browser_screenshot", "browser_run_js", "browser_press_key",
    "browser_select_option", "browser_hover", "browser_scroll", "browser_wait_for",
    "browser_go_back", "browser_go_forward", "browser_reload", "browser_extract_links",
    "browser_get_attribute", "browser_check_exists", "browser_upload_file",
    "browser_list_tabs", "browser_close_tab", "browser_distill_dom", "browser_switch_tab",
    "browser_evaluate_xpath", "browser_network_intercept"
})

# Transient errors that warrant an automatic retry
_TRANSIENT_ERROR_KEYWORDS = frozenset({
    "timeout", "timed out", "detached", "intercepted", "net::err", 
    "navigation failed", "target closed", "session closed", "element not found"
})

_MAX_ITERATIONS = 12

# Human-readable status labels for WebSocket telemetry (no raw tool names)
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
}

_URL_STUCK_THRESHOLD = 6

class BrowserAgent(BaseAgent):
    """
    Makima v7.2 Elite Browser Agent.
    Orchestrates stealth web browsing, advanced DOM distillation, vision-DOM fusion,
    and resilient data extraction with enterprise-grade context management.
    """
    
    AGENT_NAME = "browser"
    DESCRIPTION = "Stealth web browsing, DOM distillation, vision-fusion data extraction"
    CAPABILITIES = ["web_browsing", "dom_distillation", "stealth_browsing", "form_filling", "screenshot_capture", "scraping"]
    AGENT_TOOLS = ["browser_navigate", "browser_click", "browser_fill", "browser_get_text", "browser_distill_dom", "browser_screenshot"]
    TAGS = ["browser", "web", "scraping", "playwright", "stealth"]

    SYSTEM_PROMPT = """You are Makima's Elite Browser Agent, an autonomous web automation expert.
You navigate, interact, and extract data from the web with stealth, precision, and resilience.

━━━ Elite Capabilities & Tools ━━━
CORE NAVIGATION:
  browser_navigate(url, expected_domain?, stealth_mode=True)
  browser_go_back() | browser_go_forward() | browser_reload()
  browser_list_tabs() | browser_switch_tab(index) | browser_close_tab(index)

INTERACTION (DOM & VISION FUSION):
  browser_click(selector?, text?, vision_description?, index?) — Use vision_description if DOM selectors fail.
  browser_fill(selector, value, clear_first=True)
  browser_press_key(key, selector?)
  browser_select_option(selector, value_or_text)
  browser_hover(selector) | browser_scroll(direction, amount)
  browser_upload_file(selector, file_path)

EXTRACTION & ANALYSIS:
  browser_distill_dom(max_depth?, include_hidden?) — ELITE: Extracts semantic text/links, stripping noise. ALWAYS prefer over get_text for large pages.
  browser_get_text(selector?) — Raw text extraction.
  browser_extract_links(limit?, filter_regex?)
  browser_get_attribute(selector, attribute)
  browser_evaluate_xpath(xpath)
  browser_screenshot(full_page?, highlight_selector?) — Use for visual verification or when DOM is heavily obfuscated.

ADVANCED / RESILIENCE:
  browser_run_js(script, timeout_ms?) — Execute custom JS. Use for Shadow DOM, infinite scroll, or complex state extraction.
  browser_wait_for(selector?, state?, timeout_ms?) — Crucial for SPAs and dynamic content.
  browser_check_exists(selector)
  browser_network_intercept(url_pattern, action) — Block/modify network requests to speed up loading.

━━━ Elite Operational Rules ━━━
1. CONTEXT EFFICIENCY: ALWAYS use `browser_distill_dom()` instead of `browser_get_text()` on large/complex pages to prevent context overflow.
2. DYNAMIC CONTENT: On SPAs or after clicks, ALWAYS use `browser_wait_for()` before interacting with new elements.
3. STEALTH & ANTI-BOT: Use human-like delays implicitly. If a site blocks you, try `browser_run_js` to bypass or alter the User-Agent via JS if possible.
4. CAPTCHA/BOT CHECK: If you detect a CAPTCHA, Cloudflare challenge, or bot-protection, STOP immediately and report it. Do not loop.
5. SHADOW DOM / IFRAMES: If standard selectors fail, use `browser_run_js` to pierce Shadow DOM or switch iframe contexts.
6. STUCK RECOVERY: If an action fails, do not blindly retry the exact same selector. Re-evaluate the DOM with `browser_distill_dom()` or take a `browser_screenshot()`.
7. OUTPUT FORMAT: Respond with EXACTLY ONE valid JSON object per turn. No markdown formatting around the JSON.
   - Single action: {"tool": "browser_click", "params": {...}, "reply": "..."}
   - BATCH: For short sequential steps (e.g. filling a login form), output {"actions": [{"tool": "browser_fill", "params": {...}}, {"tool": "browser_click", "params": {...}}], "reply": "..."} to save API round-trips. Keep batches to at most 4 actions.
"""

    def __init__(
        self,
        ai_handler,
        memory,
        tool_registry,
        ws_broadcast: Any = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        url_stuck_threshold: int = 6,
        wall_clock_timeout_s: float = 60.0,
        **kwargs: Any
    ) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails)
        self._max_iterations = _MAX_ITERATIONS
        self._max_context_tokens = 12000  # Approximate token limit for context window management
        self._shared_state = None  # Set by EcosystemHub.register_worker (cross-agent blackboard)
        # Configurable loop guards — previously hardcoded constants.
        self._url_stuck_threshold = int(url_stuck_threshold)
        self._wall_clock_timeout_s = float(wall_clock_timeout_s)
        
        # State tracking for advanced loop detection and context
        self._visited_urls: set[str] = set()
        self._current_url: str = ""
        self._action_history: deque[dict[str, Any]] = deque(maxlen=15)
        self._snapshot_indices: list[int] = []
        self._stuck_actions: int = 0

    def _step_label(self, tool: str) -> str:
        """Map raw tool name to a human-readable loading label for the UI."""
        return _BROWSER_STEP_LABELS.get(tool, f"Running {tool}...")

    def _save_to_blackboard(self, content: str, tool: str, idx: int) -> str:
        """Offload large tool output to a local file; return a compact context reference."""
        try:
            from pathlib import Path
            bb_dir = Path(__file__).resolve().parents[3] / "data" / "blackboard"
            bb_dir.mkdir(parents=True, exist_ok=True)
            ref_id = f"{tool.replace('browser_', '')}_{idx}_{int(time.monotonic() * 1000)}"
            path = bb_dir / f"{ref_id}.md"
            path.write_text(content, encoding="utf-8")
            summary = " ".join(content[:400].split())
            return (f"[SYSTEM] Large {tool} output saved to blackboard. Reference ID: {ref_id}. "
                    f"Summary: {summary}... Full data at {path.name}.")
        except Exception:
            return content[:12000] + "\n... [TRUNCATED FOR CONTEXT WINDOW PROTECTION]"

    def _is_privacy_mode(self) -> bool:
        """Check if privacy mode is enabled in the orchestrator config."""
        try:
            cfg = (self.orchestrator.config or {}) if self.orchestrator else {}
            return bool(cfg.get("privacy_mode", False))
        except Exception:
            return False

    def _calculate_dom_hash(self, content: str) -> str:
        """Generate a fast hash of the DOM/text to detect stagnant pages."""
        return hashlib.md5(content.encode('utf-8', errors='ignore')).hexdigest()

    def _is_transient_error(self, error_msg: str) -> bool:
        """Determine if a tool error is transient and worth retrying."""
        err_lower = error_msg.lower()
        return any(keyword in err_lower for keyword in _TRANSIENT_ERROR_KEYWORDS)

    def _compress_context(self, messages: list[dict[str, Any]]) -> None:
        """
        Elite context window management.
        Replaces older large snapshots (DOM/Screenshots) with concise summaries 
        to prevent context overflow while retaining operational history.
        """
        if len(self._snapshot_indices) <= 2:
            return

        # Keep the last 2 snapshots intact, compress the rest
        indices_to_compress = self._snapshot_indices[:-2]
        
        for idx in indices_to_compress:
            if 0 <= idx < len(messages):
                msg = messages[idx]
                content = msg.get("content", "")
                tool = msg.get("_snapshot_tool", "unknown")
                
                if isinstance(content, str) and len(content) > 800:
                    # Retain a small fraction or just a placeholder
                    if tool == "browser_screenshot":
                        msg["content"] = f"[SYSTEM] Previous {tool} cleared to save context. Visual state was recorded."
                    else:
                        # Keep first 300 chars as summary
                        msg["content"] = f"[SYSTEM] Previous {tool} compressed. Summary: {content[:300]}..."
                    
                    # Remove the marker so we don't process it again
                    if "_snapshot_tool" in msg:
                        del msg["_snapshot_tool"]

        # Update the tracked indices
        self._snapshot_indices = [i for i in self._snapshot_indices if i not in indices_to_compress]

    def _detect_stuck_loop(self, tool: str, params: dict[str, Any]) -> str | None:
        """
        Advanced semantic stuck-loop detection.
        Returns a recovery prompt if the agent is stuck, else None.
        """
        if len(self._action_history) < 4:
            return None

        recent_actions = list(self._action_history)[-4:]
        
        # Check for exact oscillation (A -> B -> A -> B)
        if (recent_actions[0]["tool"] == recent_actions[2]["tool"] == tool and
            recent_actions[1]["tool"] == recent_actions[3]["tool"]):
            return ("[CRITICAL LOOP DETECTED] You are oscillating between states. "
                    "STOP repeating the same actions. Use `browser_distill_dom()` or `browser_screenshot()` "
                    "to re-evaluate the page, or use `browser_run_js()` to force a state change.")

        # Check for repeated failures on the same tool/selector
        fail_count = sum(1 for a in recent_actions if a["tool"] == tool and a.get("failed", False))
        if fail_count >= 2:
            return (f"[REPEATED FAILURE] '{tool}' has failed multiple times. "
                    "The selector is likely invalid or the element is obscured. "
                    "Take a `browser_screenshot()` to verify visually, or use `browser_distill_dom()` to find a better selector.")

        return None

    async def _execute_tool_with_retry(self, tool: str, params: dict[str, Any], max_retries: int = 2) -> tuple[Any, bool]:
        """
        Executes a tool with automatic retry logic for transient network/DOM errors.
        Returns (result, success_flag).
        """
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                res = await self._use_tool(tool, **params)
                res_str = str(res)
                
                # KAMI-15 FIX: Check if BaseAgent returned a tool failure string
                if self._tool_failed(res):
                    last_error = res_str
                    if attempt < max_retries and self._is_transient_error(last_error):
                        logger.warning(f"Transient error in {tool} (attempt {attempt+1}): {last_error}. Retrying...")
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    return res, False

                # Check for CAPTCHA/Bot protection
                if any(kw in res_str.lower() for kw in ["captcha", "verify you are human", "cloudflare", "bot protection"]):
                    return "⚠️ CAPTCHA or Bot Protection detected. Manual intervention required.", False
                
                return res, True
                
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries and self._is_transient_error(last_error):
                    logger.warning(f"Transient exception in {tool} (attempt {attempt+1}): {last_error}. Retrying...")
                    await asyncio.sleep(1.5 * (attempt + 1))  # Exponential backoff
                    continue
                break
                
        return f"[Tool Error: {last_error}]", False

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """
        Main execution loop for the Elite Browser Agent.
        """
        self._reset_state()
        if self._is_privacy_mode():
            return "Browser automation is disabled in Privacy Mode."

        await self._broadcast_status(task_id, "thinking")
        messages = self._build_messages(message, context)
        
        # Clear state trackers
        self._visited_urls.clear()
        self._action_history.clear()
        self._snapshot_indices.clear()
        self._current_url = ""
        self._stuck_actions = 0

        _start_time = time.monotonic()
        _consec_json_fail = 0

        for iteration in range(self._max_iterations):
            if self._cancelled:
                return self._partial_result or "[Cancelled by user/system]"

            # Configurable wall-clock timeout — prevents UI from being stuck forever
            if time.monotonic() - _start_time > self._wall_clock_timeout_s:
                logger.warning("[browser] task %s timed out at iteration %d", task_id, iteration)
                return "Browser task timed out ({}s). The page may be unresponsive or the task is too complex — please try a simpler request.".format(self._wall_clock_timeout_s)

            # Context compression to prevent token overflow
            self._compress_context(messages)

            raw = await self._llm_call(
                messages, 
                task="automation", 
                require_json=True, 
                temperature=0.15, 
                max_tokens=1024
            )
            logger.info(f"[browser] raw LLM response: {raw}")
            
            parsed = self.ai_handler.try_parse_json(raw)
            if not parsed:
                # Bug 3 fix: break after 3 consecutive JSON failures instead of silently burning all 20 iters
                _consec_json_fail += 1
                if _consec_json_fail >= 3:
                    logger.error("[browser] 3 consecutive JSON parse failures at iteration %d — aborting", iteration)
                    return "Browser agent could not parse model responses after 3 attempts. Please rephrase your request."
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "[SYSTEM] Invalid JSON format. Return EXACTLY ONE valid JSON object with 'tool', 'params', and 'reply'."})
                continue

            _consec_json_fail = 0  # reset on successful parse

            tool = parsed.get("tool") or parsed.get("action")
            reply = parsed.get("reply", "")
            params = parsed.get("params") or {}
            if not isinstance(params, dict) or not params:
                params = {k: v for k, v in parsed.items() if k not in ("tool", "action", "reply", "actions")}

            # Batch mode: sequential multi-action execution in a single turn
            actions = parsed.get("actions")
            if isinstance(actions, list) and actions:
                batch_results: list[str] = []
                for a in actions:
                    if not isinstance(a, dict):
                        continue
                    a_tool = a.get("tool") or a.get("action")
                    a_params = a.get("params") or {}
                    if a_tool not in _BROWSER_TOOLS:
                        batch_results.append(f"[{a_tool}] unknown tool, skipped")
                        continue
                    a_res, a_ok = await self._execute_tool_with_retry(a_tool, a_params)
                    await self._broadcast_status(task_id, f"step {iteration + 1}: {self._step_label(a_tool)}")
                    if not a_ok and "CAPTCHA" in str(a_res):
                        self._cancelled = True
                        return f"{a_res} (Stopped for manual intervention)"
                    batch_results.append(f"[{a_tool}]\n{str(a_res)}")
                self._action_history.append({"tool": "batch", "params": {}, "failed": False})
                messages.append({"role": "assistant", "content": raw})
                res_str = "\n\n".join(batch_results)
                if len(res_str) > 12000:
                    res_str = self._save_to_blackboard(res_str, "browser_batch", iteration)
                messages.append({
                    "role": "user",
                    "content": f"[TOOL RESULT: browser_batch]\n{res_str}",
                    "_snapshot_tool": "browser_batch",
                })
                continue

            # Terminal condition: 'complete', 'finish', 'done' action or no tool means the agent is done
            if not tool or tool.lower() in ("complete", "finish", "done", "final_answer"):
                res_output = reply or parsed.get("result") or parsed.get("answer") or "Task completed successfully."
                self._partial_result = str(res_output)
                return self._partial_result

            # Temporal stuck heuristic: URL unchanged despite repeated interactions
            if self._current_url and tool != "browser_navigate":
                self._stuck_actions += 1
                if self._stuck_actions > self._url_stuck_threshold:
                    self._stuck_actions = 0
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": (
                        "[SYSTEM] The URL has not changed after multiple interactions. "
                        "Check for blocking modals/cookie banners, or take a browser_screenshot() "
                        "to verify the page state before continuing.")})
                    self._action_history.append({"tool": tool, "params": params, "failed": True})
                    continue

            if tool not in _BROWSER_TOOLS:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"[TOOL ERROR] Unknown tool '{tool}'. Refer to the Available Tools list."})
                continue

            # Advanced Stuck-Loop Detection
            loop_prompt = self._detect_stuck_loop(tool, params)
            if loop_prompt:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": loop_prompt})
                self._action_history.append({"tool": tool, "params": params, "failed": True})
                continue

            # URL protocol whitelist
            if tool == "browser_navigate":
                url = params.get("url", "")
                if not self._is_valid_url(url):
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"[TOOL ERROR] Invalid URL protocol: {url}. Only http:// and https:// are allowed."})
                    self._action_history.append({"tool": tool, "params": params, "failed": True})
                    continue

            # Track URL navigation
            if tool == "browser_navigate":
                url = params.get("url", "")
                if url in self._visited_urls and len(self._visited_urls) > 3:
                    logger.info(f"Re-visiting URL: {url}")
                self._visited_urls.add(url)
                self._current_url = url
                self._stuck_actions = 0

            # Execute tool with resilience
            res, success = await self._execute_tool_with_retry(tool, params)

            # Bug 3 fix: broadcast per-step progress so UI shows activity (not just '[browser: thinking]')
            await self._broadcast_status(task_id, f"step {iteration + 1}: {self._step_label(tool)}")

            # Record action for loop detection
            self._action_history.append({
                "tool": tool, 
                "params": params, 
                "failed": not success or "[Tool Error" in str(res)
            })

            # Cross-agent blackboard: publish distilled DOM to shared state for other agents
            if success and tool == "browser_distill_dom" and self._shared_state is not None:
                try:
                    await self._shared_state.set(
                        "agent:browser:dom:latest",
                        {"url": self._current_url, "preview": str(res)[:500],
                         "timestamp": time.time()},
                        ttl=300.0,
                        source_agent="browser",
                    )
                except Exception:
                    pass

            if not success and "CAPTCHA" in str(res):
                self._cancelled = True
                return f"{res} (Stopped for manual intervention)"

            # Append assistant response and tool result to context
            messages.append({"role": "assistant", "content": raw})
            
            res_str = str(res)
            if len(res_str) > 12000:
                res_str = self._save_to_blackboard(res_str, tool, iteration)
            msg_dict = {
                "role": "user", 
                "content": f"[TOOL RESULT: {tool}]\n{res_str}", 
                "_snapshot_tool": tool
            }
            messages.append(msg_dict)

            # Track snapshot indices for context compression
            if tool in ("browser_get_text", "browser_distill_dom", "browser_screenshot", "browser_run_js"):
                self._snapshot_indices.append(len(messages) - 1)

        return "Max browser steps reached. The task may be too complex or the site is unresponsive. Please refine your request."

    def _is_valid_url(self, url: str) -> bool:
        """Check if the URL has a valid protocol."""
        return url.startswith("http://") or url.startswith("https://")
