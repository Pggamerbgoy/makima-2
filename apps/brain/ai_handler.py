"""
Makima v7.1 — AI Handler

Manages all LLM backends with per-backend circuit breaker and task-based routing.
6 backends: Groq, Gemini, Claude (via OpenRouter), GPT-4o, Cerebras, Ollama.

Routing logic:
  - fast_chat → Groq (fastest)
  - research/analysis → Gemini (largest context)
  - code → Claude (best code quality)
  - offline/privacy → Ollama (local)
  - general → GPT-4o (fallback)

Safety:
  - Circuit breaker per backend (fail_count + cooldown)
  - RateLimitManager.can_send() checked BEFORE every API call
  - Response sanitization: strips leaked API keys from responses
  - 3x retry with fence-strip for invalid JSON
  - All backends down → all_backends_down WS event
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional, Literal

logger = logging.getLogger("makima.ai_handler")

# ─── API Key Leak Patterns ───────────────────────────────────────────────────
# Regex patterns to detect and strip leaked API keys from LLM responses (P11)
API_KEY_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),          # OpenAI keys
    re.compile(r"AIza[a-zA-Z0-9_\-]{30,}"),       # Google API keys
    re.compile(r"gsk_[a-zA-Z0-9]{20,}"),          # Groq keys
    re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),     # Anthropic keys
    re.compile(r"xai-[a-zA-Z0-9]{20,}"),          # xAI keys
]

# The "claude" backend routes through OpenRouter using pg's free-tier key —
# no paid model. configs/default.yaml sets a general-purpose free model;
# for coding tasks we swap to a coding-specialized free model.
# Free-tier availability on OpenRouter rotates — check
# https://openrouter.ai/models?max_price=0 for current free models.
# As of 2026-07-22: general=google/gemma-4-26b-a4b-it:free,
# code=poolside/laguna-m.1:free.
# OpenRouter Free-Tier Dynamic Auto-Routing & Model Suite
# Every free model below was live-verified (2026-08-04) as
# pricing=0 and supported_parameters includes "tools" — i.e. each one can
# run as a full agent with native tool-calling.
_OPENROUTER_AGENT_MODELS = {
    "code": "cohere/north-mini-code:free",
    "research": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "creative": "google/gemma-4-26b-a4b-it:free",
    "data_analysis": "openai/gpt-oss-20b:free",
    "automation": "google/gemma-4-31b-it:free",
    "media": "google/gemma-4-31b-it:free",
    "general": "nvidia/nemotron-3-super-120b-a12b:free",
}
_CODE_TASKS = frozenset({"code", "debugging", "refactoring"})
_RESEARCH_TASKS = frozenset({"research", "analysis", "daily_briefing"})
_CREATIVE_TASKS = frozenset({"creative"})
_DATA_TASKS = frozenset({"data_analysis"})

# Groq deprecated llama-3.3-70b-versatile AND llama-3.1-8b-instant on
# 2026-06-17 (shutdown 2026-08-16) — configs/default.yaml's groq.model must
# move off llama-3.3-70b-versatile before that date or every backend call
# starts erroring. This fast-lane model is also the perf fix for the
# command router: intent classification was paying full 70B latency for a
# ~15-word JSON classification. gpt-oss-20b is Groq's official replacement
# tier for the old 8b-instant and is dramatically faster for small,
# low-reasoning tasks like this.
_GROQ_FAST_MODEL = "llama-3.3-70b-versatile"
_FAST_TASKS = frozenset({"intent_classification", "trivial", "entity_extraction"})


def _resolve_openrouter_model(task: str) -> str:
    """Map a task tag to the best free OpenRouter model for that role.

    Used by both the streaming and non-streaming paths so that every
    agent (code/research/creative/data/media/automation/system) runs on
    its own free model with full tool support instead of one shared model.
    """
    if task in _CODE_TASKS:
        return _OPENROUTER_AGENT_MODELS["code"]
    if task in _RESEARCH_TASKS:
        return _OPENROUTER_AGENT_MODELS["research"]
    if task in _CREATIVE_TASKS:
        return _OPENROUTER_AGENT_MODELS["creative"]
    if task in _DATA_TASKS:
        return _OPENROUTER_AGENT_MODELS["data_analysis"]
    t = (task or "").lower()
    if "media" in t:
        return _OPENROUTER_AGENT_MODELS["media"]
    if "automation" in t or "system" in t:
        return _OPENROUTER_AGENT_MODELS["automation"]
    return _OPENROUTER_AGENT_MODELS["general"]


@dataclass
class CircuitBreaker:
    """
    Per-backend circuit breaker. Opens after max_failures consecutive failures.
    Stays open for an *effective* cooldown (base + jitter) before allowing a
    retry (half-open state). Jitter de-synchronizes retry storms when several
    backends / parallel subtasks all fail at once (thundering-herd defense).
    """
    max_failures: int = 3
    cooldown_s: float = 60.0
    jitter_range_s: float = 20.0
    fail_count: int = 0
    last_failure_time: float = 0.0
    state: Literal["closed", "open", "half_open"] = "closed"
    # Effective cooldown deadline (base + jitter), set when opening.
    _open_until: float = 0.0
    
    def record_failure(self) -> None:
        now = time.time()
        # Debounce rapid burst failures from parallel subtasks (e.g. asyncio.gather)
        if now - self.last_failure_time < 0.5 and self.fail_count > 0:
            return
        self.fail_count += 1
        self.last_failure_time = now
        if self.fail_count >= self.max_failures:
            if self.state != "open":
                # Jitter the cooldown so concurrent backends don't all retry at once
                self._open_until = now + self.cooldown_s + random.uniform(0.0, self.jitter_range_s)
            self.state = "open"
            logger.warning(f"Circuit breaker OPENED after {self.fail_count} failures")
    
    def record_success(self) -> None:
        self.fail_count = 0
        self.state = "closed"
        self._open_until = 0.0
    
    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            deadline = self._open_until if self._open_until else (
                self.last_failure_time + self.cooldown_s)
            if time.time() > deadline:
                self.state = "half_open"
                return True
            return False
        # half_open: allow one attempt
        return True


@dataclass
class BackendConfig:
    """Configuration for a single LLM backend."""
    name: str
    enabled: bool = True
    api_key: str = ""
    api_keys: list[str] = field(default_factory=list)
    model: str = ""
    context_limit: int = 128000
    tasks: list[str] = field(default_factory=list)
    base_url: Optional[str] = None
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    rate_limit_tpm: int = 0
    rate_limit_rpm: int = 0
    _key_idx: int = 0

    def get_api_key(self) -> str:
        if self.api_keys:
            key = self.api_keys[self._key_idx % len(self.api_keys)]
            self._key_idx += 1
            return key
        return self.api_key


@dataclass
class LLMResponse:
    """Standardized response from any LLM backend."""
    text: str
    backend: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    is_fallback: bool = False  # True if this wasn't the primary backend
    tool_calls: list[dict] = field(default_factory=list)


class AIHandler:
    """
    Central LLM handler with 6-backend failover, circuit breakers,
    rate limit awareness, and response sanitization.
    """

    # Model-specific stop/control tokens that sometimes leak into streamed output.
    # Covers Qwen (ChatML), Llama 3, Mistral, DeepSeek, and Hermes family.
    _STOP_TOKENS: tuple[str, ...] = (
        "<|im_end|>", "<|im_start|>", "<|im_sep|>",
        "<|endoftext|>", "<|end|>", "<|eot_id|>",
        "<|end_of_turn|>", "[/INST]", "</s>",
        "<|assistant|>", "<|user|>", "<|system|>",
    )

    def __init__(self, config: dict[str, Any], rate_limit_manager=None,
                 ws_broadcast=None):
        self.backends: dict[str, BackendConfig] = {}
        self.rate_limit_manager = rate_limit_manager
        self.ws_broadcast = ws_broadcast  # callable to broadcast WS events

        # Task -> preferred backend mapping
        self.task_routing = {
            "fast_chat":             ["qwen", "qwen35_flash", "groq"],
            "intent_classification": ["qwen", "groq"],
            "entity_extraction":     ["qwen", "groq"],
            "general":               ["qwen", "groq"],

            # Specialized agentic & tool tasks routed to Hermes 3 (Hugging Face)
            "research":              ["huggingface", "qwen"],
            "analysis":              ["huggingface", "qwen"],
            "automation":            ["huggingface", "qwen"],
            "code":                  ["huggingface", "qwen"],
            "debugging":             ["huggingface", "qwen"],
            "refactoring":           ["huggingface", "qwen"],
            "data_analysis":         ["huggingface", "qwen"],
            "browser":               ["huggingface", "qwen"],
            "system_control":        ["huggingface", "qwen"],
            "media":                 ["huggingface", "qwen"],

            # Other tasks fallback/defaults
            "vision":                ["qwen", "qwen35_flash", "groq"],
            "creative":              ["qwen", "qwen35_flash", "groq"],
            "daily_briefing":        ["qwen", "qwen35_flash", "groq"],
            "offline":               ["qwen", "qwen35_flash", "groq"],
            "privacy_mode":          ["qwen", "qwen35_flash", "groq"],
            "reflexion":             ["qwen", "qwen35_flash", "groq"],
        }

        self._init_backends(config)

        # Live latency tracking per backend for adaptive routing.
        # deque(maxlen=8) keeps a rolling average (~last minute of traffic).
        self._backend_latency: dict[str, deque] = {}

    def _record_latency(self, backend_name: str, latency_ms: float) -> None:
        """Record a successful backend latency for adaptive routing."""
        if latency_ms <= 0:
            return
        dq = self._backend_latency.setdefault(backend_name, deque(maxlen=8))
        dq.append(latency_ms)
    
    def _init_backends(self, config: dict[str, Any]) -> None:
        """Initialize backend configs from YAML config dict."""
        import os
        
        backends_cfg = config.get("llm", {}).get("backends", {})
        if not backends_cfg:
            import yaml
            from pathlib import Path
            cfg_file = Path(__file__).resolve().parents[2] / "configs" / "default.yaml"
            if cfg_file.exists():
                try:
                    with open(cfg_file, encoding="utf-8") as f:
                        default_cfg = yaml.safe_load(f) or {}
                    backends_cfg = default_cfg.get("llm", {}).get("backends", {})
                except Exception as e:
                    logger.warning(f"Error reading default.yaml config: {e}")

        # Auto-load root .env if present so API keys are always available
        from pathlib import Path
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            try:
                with open(env_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            except Exception as e:
                logger.warning(f"Error reading root .env file: {e}")

        for name, cfg in backends_cfg.items():
            if not cfg.get("enabled", True):
                continue
            
            # Get API key from environment first (supporting MAKIMA_* and standard *_API_KEY names), then fallback to config
            api_key_env = cfg.get("api_key_env", "")
            raw_keys = []
            
            env_keys = [
                (os.environ.get(api_key_env, "") if api_key_env else ""),
                os.environ.get(f"MAKIMA_{name.upper()}_KEY", ""),
                os.environ.get(f"{name.upper()}_API_KEY", ""),
            ]
            if name == "groq":
                env_keys.append(os.environ.get("GROQ_API_KEY", ""))
                
            for k_str in env_keys:
                if k_str:
                    raw_keys.append(k_str)
                    
            if cfg.get("api_key"):
                raw_keys.append(cfg.get("api_key"))
            
            # Split and clean all comma-separated keys
            api_keys = []
            for k_str in raw_keys:
                for k in k_str.split(","):
                    k = k.strip()
                    if k and k not in api_keys:
                        api_keys.append(k)
            
            api_key = api_keys[0] if api_keys else ""
            if len(api_keys) <= 1:
                api_keys = []
            
            cb_cfg = cfg.get("circuit_breaker", {})
            
            self.backends[name] = BackendConfig(
                name=name,
                enabled=cfg.get("enabled", True),
                api_key=api_key,
                api_keys=api_keys,
                model=cfg.get("model", ""),
                context_limit=cfg.get("context_limit", 128000),
                tasks=cfg.get("tasks", []),
                base_url=cfg.get("host"),  # for Ollama
                circuit_breaker=CircuitBreaker(
                    max_failures=cb_cfg.get("max_failures", 3),
                    cooldown_s=cb_cfg.get("cooldown_s", 60),
                ),
                rate_limit_tpm=cfg.get("rate_limit_tpm", 0),
                rate_limit_rpm=cfg.get("rate_limit_rpm", 0),
            )
        
        backend_info = {name: len(b.api_keys) if b.api_keys else (1 if b.api_key else 0) for name, b in self.backends.items()}
        logger.info(f"Initialized {len(self.backends)} LLM backends: {backend_info}")
    
    def _get_backend_order(self, task: str) -> list[str]:
        """
        Get ordered list of backends to try for a given task.
        Applies adaptive latency routing: if the primary (fastest-ranked)
        backend's rolling avg latency is slow (>4s) AND an alternate in the
        same task's route is at least 2x faster sustained, swap them so the
        user isn't penalized by a degraded-but-configured primary.
        """
        candidates = self.task_routing.get(task, self.task_routing["general"])
        if len(candidates) < 2 or not self._backend_latency:
            return candidates
        order = list(candidates)

        def _avg(name: str) -> float:
            dq = self._backend_latency.get(name)
            return sum(dq) / len(dq) if dq else 0.0

        primary = order[0]
        p_avg = _avg(primary)
        if p_avg > 4000.0:  # 4s rolling avg threshold
            # Find the fastest alternate among the remaining routed backends.
            best_alt = min(order[1:], key=_avg, default=None)
            b_avg = _avg(best_alt) if best_alt else 0.0
            if b_avg and b_avg < p_avg * 0.5:
                order.remove(best_alt)
                order.insert(0, best_alt)
                logger.info(f"Adaptive latency swap: {best_alt} ({b_avg:.0f}ms) "
                            f"→ front of route over {primary} ({p_avg:.0f}ms)")
        return order
    
    def _sanitize_response(self, text: str) -> str:
        """
        Strip leaked API keys (P11) and model-specific stop/control tokens
        (e.g. Qwen's <|im_end|>) from LLM response text.
        Safe for both full responses and individual stream chunks.
        """
        sanitized = text
        # 1. Strip model stop/control tokens
        for token in self._STOP_TOKENS:
            if token in sanitized:
                sanitized = sanitized.replace(token, "")
        # 2. Strip leaked API keys
        for pattern in API_KEY_PATTERNS:
            if pattern.search(sanitized):
                logger.warning("Detected potential API key leak in LLM response — sanitizing")
                sanitized = pattern.sub("[API_KEY_REDACTED]", sanitized)
        # 3. Collapse runs of 3+ blank lines down to 2 (preserve intentional paragraph breaks)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        return sanitized
    
    def try_parse_json(self, text: str) -> Optional[dict]:
        """
        Bulletproof JSON sanitizer + parser.

        LLMs routinely wrap valid JSON in conversational garbage:
          - Markdown fences:  ```json\n{...}\n```
          - Prose prefix:     "Sure! Here is the result: {...}"
          - Trailing notes:   {"ok": true} \n\nLet me know if...
          - Single-quotes:    {'key': 'value'}
          - Trailing commas:  {"a": 1,}

        Strategy (in order, first success wins):
          1. Raw parse — fastest path, no work if LLM was well-behaved.
          2. Strip ALL markdown fences (handles ```json, ```, ~~~).
          3. Extract first balanced JSON object OR array via brace-counting
             (more reliable than greedy regex which picks wrong { on nested JSON).
          4. Single-quote coercion + trailing-comma strip via ast.literal_eval.
          5. ast.literal_eval on full stripped text as final fallback.

        This eliminates the "JSON parse failed, retry 1/2" log on any response
        where the JSON itself is structurally valid — only genuinely malformed
        output (truncated, wrong field types) will still fail and retry.
        """
        import ast as _ast

        if not text:
            return None

        def _loads(s: str):
            try:
                result = json.loads(s)
                return result if isinstance(result, (dict, list)) else None
            except (json.JSONDecodeError, ValueError):
                return None

        def _strip_fences(s: str) -> str:
            s = s.strip()
            for fence in ("```", "~~~"):
                if s.startswith(fence):
                    lines = s.split("\n")
                    lines = lines[1:]  # drop opening fence line (may be ```json, ```JSON…)
                    if lines and lines[-1].strip().startswith(fence[:3]):
                        lines = lines[:-1]  # drop closing fence line
                    s = "\n".join(lines).strip()
                    break
            return s

        def _extract_balanced(s: str, open_ch: str, close_ch: str):
            """Walk char-by-char tracking brace depth — handles nested braces correctly."""
            start = s.find(open_ch)
            if start == -1:
                return None
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(s)):
                ch = s[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                if in_string:
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        return s[start:i + 1]
            return None

        def _coerce(s: str) -> str:
            """Strip trailing commas, then try ast.literal_eval → json.dumps round-trip."""
            s = re.sub(r",\s*([}\]])", r"\1", s)
            try:
                return json.dumps(_ast.literal_eval(s))
            except Exception:
                return s

        # 1. Raw parse
        r = _loads(text)
        if r is not None:
            return r

        # 2. Strip fences
        stripped = _strip_fences(text)
        r = _loads(stripped)
        if r is not None:
            return r

        # 3. Extract first balanced object or array
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            candidate = _extract_balanced(stripped, open_ch, close_ch)
            if candidate:
                r = _loads(candidate)
                if r is not None:
                    return r
                r = _loads(_coerce(candidate))
                if r is not None:
                    return r

        # 4. Coerce full stripped text
        r = _loads(_coerce(stripped))
        if r is not None:
            return r

        # 5. ast.literal_eval on full stripped
        try:
            r = _ast.literal_eval(stripped)
            if isinstance(r, (dict, list)):
                return r
        except (ValueError, SyntaxError):
            pass

        return None

    def _clean_messages(self, messages: list[dict]) -> list[dict]:
        """Strip unsupported non-standard metadata keys and enforce string content types."""
        allowed_keys = {"role", "content", "name", "tool_call_id", "tool_calls"}
        cleaned = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            cleaned_msg = {k: v for k, v in msg.items() if k in allowed_keys and v is not None}
            if "role" in cleaned_msg:
                content = cleaned_msg.get("content")
                if content is None:
                    cleaned_msg["content"] = ""
                elif not isinstance(content, (str, list)):
                    cleaned_msg["content"] = str(content)
                cleaned.append(cleaned_msg)
        return cleaned

    async def _call_openai_compatible(self, backend: BackendConfig, 
                                       messages: list[dict], model: str,
                                       base_url: str, **kwargs) -> LLMResponse:
        """Call any OpenAI-compatible API (Groq, OpenRouter, OpenAI, Cerebras)."""
        import httpx
        
        
        body = {
            "model": model,
            "messages": self._clean_messages(messages),
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        
        # Add tools if provided
        if "tools" in kwargs:
            body["tools"] = kwargs["tools"]
        
        for attempt in range(3):
            key = backend.get_api_key()
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                
                is_rate_limit = resp.status_code == 429 or (resp.status_code == 403 and "quota" in resp.text.lower())
                is_server_error = resp.status_code >= 500
                quota_exhausted = resp.status_code == 403 and "quota" in resp.text.lower()

                if is_rate_limit or is_server_error:
                    # Quota exhaustion (403 + "quota") is NOT transient — it will
                    # not recover in a few seconds, so hammering it with 8s sleeps
                    # just burns latency and blocks faster fallback backends.
                    # Record the circuit-breaker failure and fail fast so the
                    # breaker opens and future calls skip this backend entirely.
                    if quota_exhausted:
                        backend.circuit_breaker.record_failure()
                        if self.rate_limit_manager:
                            retry_after_quota = resp.headers.get("Retry-After", "60")
                            try:
                                self.rate_limit_manager.handle_429(
                                    backend.name, float(retry_after_quota)
                                )
                            except Exception:
                                pass
                        raise RateLimitError(
                            f"{backend.name} quota exhausted, status {resp.status_code}"
                        )

                    if is_server_error:
                        wait_time = 1.5 * (attempt + 1)
                        err_label = f"Server error {resp.status_code}"
                    else:
                        retry_after_val = resp.headers.get("Retry-After", "")
                        try:
                            wait_time = float(retry_after_val)
                        except ValueError:
                            wait_time = 8.0
                        wait_time = max(3.0, min(wait_time, 15.0))
                        err_label = f"Rate limited/Quota exhausted ({resp.status_code})"

                    if attempt < 2:
                        if is_rate_limit and len(backend.api_keys) > 1:
                            logger.warning(f"[{backend.name}] {err_label}. Rotating key immediately (attempt {attempt + 1}/3)...")
                            await asyncio.sleep(0.5)
                        else:
                            logger.warning(f"[{backend.name}] {err_label}. Retrying in {wait_time}s (attempt {attempt + 1}/3)...")
                            await asyncio.sleep(wait_time)
                        continue

                    if is_rate_limit:
                        retry_after = resp.headers.get("Retry-After", "60")
                        if self.rate_limit_manager and resp.status_code == 429:
                            self.rate_limit_manager.handle_429(backend.name, float(retry_after))
                        raise RateLimitError(
                            f"{backend.name} rate limited/quota exhausted, status {resp.status_code}",
                            retry_after_s=wait_time,
                        )
                    else:
                        logger.error(f"[{backend.name}] Server error persisted after {attempt + 1} attempts: {resp.status_code}")
                
                if resp.status_code >= 400:
                    logger.error(f"[{backend.name}] HTTP {resp.status_code}: {resp.text}")
                resp.raise_for_status()
                data = resp.json()
                break
        
        choice = data["choices"][0]
        usage = data.get("usage", {})
        msg_obj = choice.get("message", {})
        
        raw_tool_calls = msg_obj.get("tool_calls", [])
        tool_calls = []
        for tc in raw_tool_calls:
            try:
                fn = tc.get("function", {})
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })
            except Exception:
                pass
        
        return LLMResponse(
            text=msg_obj.get("content") or "",
            backend=backend.name,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            tool_calls=tool_calls,
        )
    
    async def _call_gemini(self, backend: BackendConfig, messages: list[dict],
                            model: str, **kwargs) -> LLMResponse:
        """Call Google Gemini API."""
        import httpx
        
        # Convert OpenAI message format to Gemini format
        contents = []
        system_text = ""
        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_text = msg["content"]
                continue
            gemini_role = "user" if role == "user" else "model"
            content = msg["content"]
            if isinstance(content, list):
                parts = []
                for item in content:
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        url = item["image_url"]["url"]
                        if url.startswith("data:"):
                            mime_type, b64_data = url.split(";", 1)
                            mime_type = mime_type[5:]
                            if b64_data.startswith("base64,"):
                                b64_data = b64_data[7:]
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64_data
                                }
                            })
                contents.append({
                    "role": gemini_role,
                    "parts": parts
                })
            else:
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": str(content)}]
                })
        
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.7),
                "maxOutputTokens": kwargs.get("max_tokens", 4096),
            }
        }
        
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={backend.get_api_key()}")
        
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.post(url, json=body)
            
            if resp.status_code == 429:
                raise RateLimitError(f"Gemini rate limited")
            resp.raise_for_status()
            data = resp.json()
        
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        
        return LLMResponse(
            text=text,
            backend="gemini",
            model=model,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
        )
    
    async def _call_ollama(self, backend: BackendConfig, messages: list[dict],
                            model: str, **kwargs) -> LLMResponse:
        """Call local Ollama instance."""
        import httpx
        
        host = backend.base_url or "http://127.0.0.1:11434"
        
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.7),
                "num_predict": kwargs.get("max_tokens", 4096),
            }
        }
        
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.post(f"{host}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        
        return LLMResponse(
            text=data["message"]["content"],
            backend="ollama",
            model=model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        )

    async def _call_backend(self, backend_name: str, messages: list[dict],
                            task: str = "general", **kwargs) -> LLMResponse:
        """Route a generate() call to the provider-specific implementation.

        Dispatches on backend name: gemini -> native Google API, ollama ->
        local server, everything else -> OpenAI-compatible /chat/completions
        endpoint (Groq, Qwen/DashScope, OpenRouter, Cerebras, OpenAI).
        """
        backend = self.backends[backend_name]
        if backend_name == "claude" and "model" not in kwargs:
            kwargs["model"] = _resolve_openrouter_model(task)
        model = kwargs.pop("model", None) or backend.model
        if not model:
            raise ValueError(f"No model configured for backend '{backend_name}'")

        if backend_name == "gemini":
            return await self._call_gemini(backend, messages, model, **kwargs)
        if backend_name == "ollama":
            return await self._call_ollama(backend, messages, model, **kwargs)

        base_url = kwargs.pop("base_url", None) or backend.base_url or self._default_base_urls.get(backend_name)
        if not base_url:
            raise ValueError(f"No base_url configured for backend '{backend_name}'")
        return await self._call_openai_compatible(backend, messages, model, base_url, **kwargs)

    _default_base_urls = {
        "groq": "https://api.groq.com/openai/v1",
        "claude": "https://openrouter.ai/api/v1",
        "gpt4o": "https://api.openai.com/v1",
        "cerebras": "https://api.cerebras.ai/v1",
    }

    async def generate(self, messages: list[dict], task: str = "general",
                       require_json: bool = False, **kwargs) -> LLMResponse:
        """
        Generate a response using the best available backend for the task.
        Tries backends in priority order, skipping circuit-broken and rate-limited ones.
        
        Args:
            messages: OpenAI-format message list
            task: Task type for routing (fast_chat, research, code, etc.)
            require_json: If True, retry up to 3x for valid JSON response
            **kwargs: Passed to backend (temperature, max_tokens, tools, etc.)
        
        Returns:
            LLMResponse with sanitized text
            
        Raises:
            AllBackendsDownError: If all backends are unavailable
        """
        backend_order = self._get_backend_order(task)
        errors = []
        
        for i, backend_name in enumerate(backend_order):
            if backend_name not in self.backends:
                continue
            
            backend = self.backends[backend_name]
            
            # Skip backends with no configured API key immediately — no
            # point spending a network round-trip to learn what we already
            # know, and a 401 shouldn't be recorded as a circuit-breaker
            # failure (that's meant to reflect real outages, not
            # misconfiguration).
            if backend_name != "ollama" and not backend.api_key and not backend.api_keys:
                logger.debug(f"Skipping {backend_name}: no API key configured")
                continue
            
            # Check circuit breaker
            if not backend.circuit_breaker.can_attempt():
                logger.debug(f"Skipping {backend_name}: circuit breaker open")
                continue
            
            # Check rate limiter (prevents wasted API calls)
            estimated_tokens = min(kwargs.get("max_tokens", 500), 1000)
            if self.rate_limit_manager and not self.rate_limit_manager.can_send(
                backend_name, estimated_tokens
            ):
                logger.debug(f"Skipping {backend_name}: rate limit would be exceeded")
                continue
            
            try:
                import asyncio
                _t0 = time.monotonic()
                response = await asyncio.wait_for(
                    self._call_backend(backend_name, messages, task=task, **kwargs),
                    timeout=90.0
                )
                response.latency_ms = (time.monotonic() - _t0) * 1000.0
                self._record_latency(backend_name, response.latency_ms)
                # A successful backend call resets the circuit breaker, so a
                # backend that recovers is allowed to serve traffic again.
                backend.circuit_breaker.record_success()
                
                if i > 0:
                    response.is_fallback = True
                    logger.info(f"Used fallback backend: {backend_name}")
                
                # JSON validation if required (P10)
                if require_json:
                    parsed = self.try_parse_json(response.text)
                    if parsed is None:
                        # KAMI-13 FIX: Inject error feedback into the retry prompt
                        retry_messages = list(messages)
                        retry_messages.append({"role": "assistant", "content": response.text})
                        retry_messages.append({"role": "user", "content": "CRITICAL ERROR: Your previous response was not valid JSON. You must output ONLY raw, valid JSON. No markdown fences, no conversational text. Fix the formatting."})
                        
                        for retry in range(2):
                            logger.warning(f"JSON parse failed, retry {retry + 1}/2 with error feedback prompt")
                            response = await asyncio.wait_for(
                                self._call_backend(backend_name, retry_messages, task=task, **kwargs),
                                timeout=30.0
                            )
                            parsed = self.try_parse_json(response.text)
                            if parsed is not None:
                                break
                        
                        if parsed is None:
                            logger.error("JSON parse failed after 3 attempts, using plain text")
                            # Don't fail — return plain text (P10 recovery)
                
                return response
                
            except RateLimitError as e:
                # Count toward the circuit breaker so a persistently rate-limited
                # / quota-exhausted backend gets opened and skipped on later calls
                # instead of being retried on every message.
                backend.circuit_breaker.record_failure()
                retry_after = getattr(e, "retry_after_s", None)
                if backend_name == "groq" and retry_after is not None and retry_after <= 20.0:
                    wait_time = max(1.0, min(15.0, retry_after + 0.5))
                    logger.info(f"Groq rate limited — auto-waiting {wait_time:.1f}s and retrying once...")
                    await asyncio.sleep(wait_time)
                    try:
                        response = await asyncio.wait_for(
                            self._call_backend(backend_name, messages, task=task, **kwargs),
                            timeout=30.0
                        )
                        if require_json:
                            self.try_parse_json(response.text)
                        response.latency_ms = max(response.latency_ms or 0.0,
                                                 (time.monotonic() - _t0) * 1000.0)
                        self._record_latency(backend_name, response.latency_ms)
                        return response
                    except Exception as retry_err:
                        logger.warning(f"Groq retry after wait failed: {retry_err}")
                errors.append(f"{backend_name}: {e}")
                continue
            except Exception as e:
                # Catch ALL exceptions from _call_backend (like httpx.TimeoutException, ConnectError, etc.)
                # so that we can successfully fall back to the next backend in the list!
                # Count it against the circuit breaker so broken backends open up
                # and stop degrading every request with timeout waits.
                backend.circuit_breaker.record_failure()
                errors.append(f"{backend_name}: {e}")
                logger.warning(f"Backend {backend_name} failed during generate: {e}, falling back...")
                continue
        # All backends failed (P9)
        error_summary = "; ".join(errors) if errors else "No backends configured with API keys. Please set an API key in Settings or run Ollama locally."
        logger.warning(f"ALL BACKENDS DOWN / UNCONFIGURED: {error_summary}")
        
        if self.ws_broadcast:
            from . import ws_protocol
            await self.ws_broadcast(ws_protocol.WSMessage(
                v=ws_protocol.PROTOCOL_VERSION,
                type=ws_protocol.ServerMessageType.ALL_BACKENDS_DOWN,
                payload={"retry_in_s": 30},
            ))
        
        return LLMResponse(
            text="⚠️ **No AI Model Backend Available**\n\nI need an API key to generate answers. Please go to the **Settings** page in the UI and enter your API key (Gemini, Groq, OpenRouter, or GPT-4o), or start **Ollama** locally on your machine.",
            backend="fallback",
            model="none",
            is_fallback=True,
        )

    async def chat_complete(self, messages: list[dict], task: str = "general", **kwargs) -> str:
        """Helper method for simple chat completion returning text string."""
        res = await self.generate(messages, task=task, **kwargs)
        return res.text

    
    async def generate_stream(self, messages: list[dict], task: str = "general",
                               **kwargs) -> AsyncGenerator[str, None]:
        """
        Stream a response token-by-token from the best available backend.
        Uses per-backend streaming APIs for OpenAI-compatible, Gemini, and Ollama.
        Falls back to non-streaming for any backend that doesn't support it.
        """
        import httpx
        import json as json_module

        kwargs.pop("backend", None)
        backend_order = self._get_backend_order(task)
        last_error = None

        for backend_name in backend_order:
            if backend_name not in self.backends:
                continue
            backend = self.backends[backend_name]

            if backend_name != "ollama" and not backend.api_key and not backend.api_keys:
                logger.debug(f"Skipping {backend_name}: no API key configured")
                continue

            if not backend.circuit_breaker.can_attempt():
                continue

            estimated_tokens = kwargs.get("max_tokens", 4096)
            if self.rate_limit_manager and not self.rate_limit_manager.can_send(
                backend_name, estimated_tokens
            ):
                continue

            chunks_yielded = False
            try:
                if backend_name == "ollama":
                    async for chunk in self._stream_ollama(backend, messages, **kwargs):
                        chunks_yielded = True
                        yield chunk
                elif backend_name == "gemini":
                    async for chunk in self._stream_gemini(backend, messages, **kwargs):
                        chunks_yielded = True
                        yield chunk
                elif backend_name in ("groq", "gpt4o", "claude", "cerebras", "qwen", "qwen36_flash", "qwen35_flash", "qwen_plus"):
                    base_url = backend.base_url or {
                        "groq": "https://api.groq.com/openai/v1",
                        "gpt4o": "https://api.openai.com/v1",
                        "claude": "https://openrouter.ai/api/v1",
                        "cerebras": "https://api.cerebras.ai/v1",
                    }.get(backend_name, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
                    stream_kwargs = dict(kwargs)
                    if backend_name == "claude" and "model" not in stream_kwargs:
                        stream_kwargs["model"] = _resolve_openrouter_model(task)
                    async for chunk in self._stream_openai_compatible(
                        backend, messages, base_url, **stream_kwargs
                    ):
                        chunks_yielded = True
                        yield chunk
                else:
                    # Fallback: non-streaming for unknown backends
                    response = await self.generate(messages, task, **kwargs)
                    text = self._sanitize_response(response.text)
                    chunk_size = 4
                    for i in range(0, len(text), chunk_size):
                        chunks_yielded = True
                        yield text[i:i+chunk_size]

                return  # Successfully streamed from a backend

            except RateLimitError as e:
                last_error = str(e)
                if chunks_yielded:
                    raise
                continue
            except Exception as e:
                last_error = str(e)
                if chunks_yielded:
                    logger.warning(f"Backend {backend_name} failed mid-stream, aborting retry to prevent duplicates: {e}")
                    raise
                continue

        # All backends failed — try non-streaming fallback
        logger.warning(f"Streaming failed on all backends, trying non-streaming: {last_error}")
        response = await self.generate(messages, task, **kwargs)
        text = self._sanitize_response(response.text)
        chunk_size = 4
        for i in range(0, len(text), chunk_size):
            yield text[i:i+chunk_size]

    async def _stream_openai_compatible(self, backend: BackendConfig,
                                         messages: list[dict], base_url: str,
                                         **kwargs) -> AsyncGenerator[str, None]:
        """Stream from an OpenAI-compatible API using SSE."""
        import httpx
        import json

        headers = {
            "Authorization": f"Bearer {backend.get_api_key()}",
            "Content-Type": "application/json",
        }
        body = {
            "model": kwargs.get("model", backend.model),
            "messages": self._clean_messages(messages),
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions",
                headers=headers, json=body,
            ) as resp:
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "60")
                    if self.rate_limit_manager:
                        self.rate_limit_manager.handle_429(backend.name, float(retry_after))
                    raise RateLimitError(f"{backend.name} rate limited")
                resp.raise_for_status()

                buffer = ""
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                content = self._sanitize_response(content)
                                if content:  # may be empty after stripping a lone stop token
                                    yield content
                        except json.JSONDecodeError:
                            continue

    async def _stream_gemini(self, backend: BackendConfig,
                              messages: list[dict], **kwargs) -> AsyncGenerator[str, None]:
        """Stream from Gemini API."""
        import httpx
        import json

        contents = []
        system_text = ""
        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_text = msg["content"]
                continue
            gemini_role = "user" if role == "user" else "model"
            content = msg["content"]
            if isinstance(content, list):
                parts = []
                for item in content:
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        url = item["image_url"]["url"]
                        if url.startswith("data:"):
                            mime_type, b64_data = url.split(";", 1)
                            mime_type = mime_type[5:]
                            if b64_data.startswith("base64,"):
                                b64_data = b64_data[7:]
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64_data
                                }
                            })
                contents.append({
                    "role": gemini_role,
                    "parts": parts
                })
            else:
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": str(content)}]
                })

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.7),
                "maxOutputTokens": kwargs.get("max_tokens", 4096),
            },
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        model = kwargs.get("model", backend.model)
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:streamGenerateContent?alt=sse&key={backend.get_api_key()}")

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            async with client.stream("POST", url, json=body) as resp:
                if resp.status_code == 429:
                    raise RateLimitError("Gemini rate limited")
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for part in parts:
                                    if "text" in part:
                                        yield part["text"]
                        except json.JSONDecodeError:
                            continue

    async def _stream_ollama(self, backend: BackendConfig,
                              messages: list[dict], **kwargs) -> AsyncGenerator[str, None]:
        """Stream from local Ollama instance using NDJSON."""
        import httpx
        import json

        host = backend.base_url or "http://127.0.0.1:11434"
        body = {
            "model": kwargs.get("model", backend.model),
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": kwargs.get("temperature", 0.7),
                "num_predict": kwargs.get("max_tokens", 4096),
            },
        }

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            async with client.stream("POST", f"{host}/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                        if data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
    
    def get_backend_status(self) -> dict[str, dict]:
        """Get status of all backends for health dashboard."""
        status = {}
        for name, backend in self.backends.items():
            status[name] = {
                "enabled": backend.enabled,
                "model": backend.model,
                "circuit_breaker_state": backend.circuit_breaker.state,
                "fail_count": backend.circuit_breaker.fail_count,
                "has_api_key": bool(backend.api_key) or bool(backend.api_keys),
                "context_limit": backend.context_limit,
            }
        return status


class RateLimitError(Exception):
    """Raised when a backend is rate limited."""
    def __init__(self, message: str = "", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AllBackendsDownError(Exception):
    """Raised when all LLM backends are unavailable (P9)."""
    pass
