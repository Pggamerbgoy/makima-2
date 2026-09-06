"""
Makima OS â€” NEXUS LLM Gateway (v9.0)
Location: apps/brain/ai_handler.py

A research-backed Multi-Provider LLM Gateway implementing:
  1. Provider Strategy/Adapter Pattern (OpenAICompatible, Gemini, Ollama)
  2. Dynamic Pareto Routing (RouteLLM: Latency Tier + EWMA + Failures + Capabilities)
  3. Native Structured Outputs (response_format, responseMimeType, format=json)
  4. Resilient Multi-Pass JSON Parser (try_parse_json)
  5. Active Key Redaction & ChatML Control Token Sanitization
  6. FrugalGPT Quality Cascading on JSON failure
  7. Multi-Key Sticky Rotation & Circuit Breaker Isolation

100% Backward Compatible with all Makima 9.0 agents, main.py lifespan, and UI settings.
"""

from __future__ import annotations

import ast as _ast
import asyncio
import json
import logging
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Literal, Optional, Set, Tuple, Union

try:
    import dotenv
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        dotenv.load_dotenv(dotenv_path=env_path)
    else:
        dotenv.load_dotenv()
except ImportError:
    pass

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

logger = logging.getLogger("makima.ai_handler")


# =============================================================================
# 1. EXCEPTIONS & CONSTANTS
# =============================================================================

class StreamingUnavailableError(Exception):
    """Raised when streaming response is unavailable or yields no tokens."""
    pass


class RateLimitError(Exception):
    """Raised when a backend is rate limited."""
    def __init__(self, message: str = "", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AllBackendsDownError(Exception):
    """Raised when all LLM backends are unavailable."""
    pass


# Model-specific stop/control tokens that sometimes leak into streamed output.
_STOP_TOKENS: tuple[str, ...] = (
    "<|im_end|>", "<|im_start|>", "<|im_sep|>",
    "<|endoftext|>", "<|end|>", "<|eot_id|>",
    "<|end_of_turn|>", "[/INST]", "</s>",
    "<|assistant|>", "<|user|>", "<|system|>",
)

# Regex patterns to detect and strip leaked API keys from LLM responses
API_KEY_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),          # OpenAI keys
    re.compile(r"AIza[a-zA-Z0-9_\-]{20,}"),       # Google API keys
    re.compile(r"gsk_[a-zA-Z0-9]{20,}"),          # Groq keys
    re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),     # Anthropic keys
    re.compile(r"xai-[a-zA-Z0-9]{20,}"),          # xAI keys
]

# OpenRouter Free-Tier Dynamic Auto-Routing & Model Suite
_OPENROUTER_AGENT_MODELS = {
    "code": "qwen/qwen-2.5-coder-32b-instruct:free",
    "research": "meta-llama/llama-3.3-70b-instruct:free",
    "creative": "meta-llama/llama-3.3-70b-instruct:free",
    "data_analysis": "meta-llama/llama-3.3-70b-instruct:free",
    "automation": "meta-llama/llama-3.3-70b-instruct:free",
    "media": "meta-llama/llama-3.3-70b-instruct:free",
    "general": "meta-llama/llama-3.3-70b-instruct:free",
}
_CODE_TASKS = frozenset({"code", "debugging", "refactoring"})
_RESEARCH_TASKS = frozenset({"research", "analysis", "daily_briefing"})
_CREATIVE_TASKS = frozenset({"creative"})
_DATA_TASKS = frozenset({"data_analysis"})


def _resolve_openrouter_model(task: str) -> str:
    """Map a task tag to the best free OpenRouter model for that role."""
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


# =============================================================================
# 2. CIRCUIT BREAKER & DATA MODELS
# =============================================================================

_EXACT_PLACEHOLDER_KEYS = frozenset({
    "none", "null", "placeholder", "dummy", "fake", "todo", "changeme", "example", "xxx", "test", "sk-...", "sk-xxx"
})

_SUBSTRING_PLACEHOLDERS = (
    "your_", "_here", "placeholder", "insert_", "replace_me", "sk-...", "your-", "-here"
)


def is_valid_api_key(key: Optional[str]) -> bool:
    """Check whether a provided API key is genuine and non-placeholder."""
    if not key or not isinstance(key, str):
        return False
    clean = key.strip().lower()
    if len(clean) < 8:
        return False
    if clean in _EXACT_PLACEHOLDER_KEYS:
        return False
    if any(p in clean for p in _SUBSTRING_PLACEHOLDERS):
        return False
    return True


@dataclass
class CircuitBreaker:
    """
    Per-backend circuit breaker with jittered backoff.
    Opens after max_failures consecutive failures.
    """
    max_failures: int = 3
    cooldown_s: float = 60.0
    jitter_range_s: float = 20.0
    fail_count: int = 0
    last_failure_time: float = 0.0
    state: Literal["closed", "open", "half_open"] = "closed"
    _open_until: float = 0.0

    def record_failure(self) -> None:
        now = time.time()
        if now - self.last_failure_time < 0.5 and self.fail_count > 0:
            return
        self.fail_count += 1
        self.last_failure_time = now
        if self.fail_count >= self.max_failures:
            if self.state != "open":
                self._open_until = now + self.cooldown_s + random.uniform(0.0, self.jitter_range_s)
            self.state = "open"
            logger.warning("Circuit breaker OPENED after %d failures", self.fail_count)

    def trip_open(self, cooldown_s: float = 300.0) -> None:
        now = time.time()
        self.fail_count = self.max_failures
        self.last_failure_time = now
        self._open_until = now + cooldown_s
        self.state = "open"
        logger.warning("Circuit breaker TRIPPED OPEN for %.0fs", cooldown_s)

    def record_success(self) -> None:
        self.fail_count = 0
        self.state = "closed"
        self._open_until = 0.0

    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            deadline = self._open_until if self._open_until else (self.last_failure_time + self.cooldown_s)
            if time.time() > deadline:
                self.state = "half_open"
                return True
            return False
        return True


@dataclass
class BackendProfile:
    """Dynamic representation of an LLM backend's configuration, capabilities, and health."""
    name: str
    enabled: bool = True
    adapter_type: str = "openai"  # "openai", "gemini", "ollama"
    api_key: str = ""
    api_keys: list[str] = field(default_factory=list)
    model: str = ""
    base_url: Optional[str] = None
    context_limit: int = 128000
    tasks: list[str] = field(default_factory=list)
    supports_tools: bool = True
    supports_json_mode: bool = True
    latency_tier: Literal["fast", "standard", "slow"] = "standard"
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    rate_limit_tpm: int = 0
    rate_limit_rpm: int = 0
    ewma_latency_ms: float = 0.0
    _key_idx: int = 0

    def get_api_key(self) -> str:
        """Return the active API key and advance index for round-robin rotation."""
        if self.api_keys:
            key = self.api_keys[self._key_idx % len(self.api_keys)]
            self._key_idx = (self._key_idx + 1) % len(self.api_keys)
            return key
        return self.api_key

    def rotate_key(self) -> str:
        """Explicitly rotate to the next API key upon failure, 429, or quota exhaustion."""
        if self.api_keys and len(self.api_keys) > 1:
            self._key_idx = (self._key_idx + 1) % len(self.api_keys)
            logger.info("[%s] Rotated to key index %d/%d", self.name, self._key_idx + 1, len(self.api_keys))
            return self.api_keys[self._key_idx]
        return self.api_key

    def is_available(self, client_api_key: Optional[str] = None) -> bool:
        """Check if backend is healthy, unblocked by circuit breaker, and credentialed."""
        if not self.circuit_breaker.can_attempt():
            return False
        if client_api_key and is_valid_api_key(client_api_key):
            return True
        if not self.enabled:
            return False
        if self.adapter_type == "ollama":
            return True
        if self.api_keys:
            return any(is_valid_api_key(k) for k in self.api_keys)
        return is_valid_api_key(self.api_key)


# Alias for backward compatibility
BackendConfig = BackendProfile


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
    is_fallback: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    tools_stripped: bool = False
    thought: str = ""


# =============================================================================
# 3. PROVIDER ADAPTERS (Strategy Pattern)
# =============================================================================

class BaseProviderAdapter:
    """Base interface for LLM provider adapters."""

    async def generate(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError

    async def stream(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        raise NotImplementedError

    async def stream_events(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream raw event dictionaries (text_delta, tool_call_delta, etc.)."""
        async for chunk in self.stream(profile, messages, client, gateway, **kwargs):
            yield {"type": "text_delta", "text": chunk}


# =============================================================================
# 3a. TOOL-PAIR SAFETY HELPERS
#
# Providers (OpenAI-compatible) reject requests where an assistant message
# carries `tool_calls` without matching role:"tool" results in the same
# request, or vice versa. Truncation and fallback logic used to split these
# pairs, producing 400s that were then "fixed" by silently stripping all
# tools. These helpers keep pairs atomic instead.
# =============================================================================

_TRUNCATION_CHAR_BUDGET = 6000


def _conversation_groups(messages: list[dict]) -> list[list[dict]]:
    """Group messages into atomic units.

    An assistant message carrying tool_calls is glued to the immediately
    following role:"tool" results that answer its call ids; a group is never
    split by downstream truncation.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    open_call_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            if current and open_call_ids:
                current.append(msg)
                open_call_ids.discard(msg.get("tool_call_id", ""))
                if not open_call_ids:
                    groups.append(current)
                    current = []
                    open_call_ids = set()
            else:
                if current:
                    groups.append(current)
                    current = []
                groups.append([msg])
        else:
            if current:
                groups.append(current)
                current = []
            calls = msg.get("tool_calls") or [] if isinstance(msg, dict) else []
            call_ids = {tc.get("id", "") for tc in calls if isinstance(tc, dict)} - {""}
            if role == "assistant" and call_ids:
                current = [msg]
                open_call_ids = call_ids
            else:
                groups.append([msg])
    if current:
        groups.append(current)
    return groups


def _prune_orphaned_tool_messages(messages: list[dict]) -> list[dict]:
    """Drop incomplete tool-call/result pairs so providers never see half a pair."""
    kept: list[dict] = []
    for group in _conversation_groups(messages):
        head = group[0]
        head_role = head.get("role")
        if head_role == "assistant":
            call_ids = {tc.get("id", "") for tc in (head.get("tool_calls") or []) if isinstance(tc, dict)} - {""}
            result_ids = {m.get("tool_call_id", "") for m in group[1:] if m.get("role") == "tool"}
            if call_ids and not call_ids.issubset(result_ids):
                continue
        elif head_role == "tool":
            continue
        kept.extend(group)
    return kept


def _truncate_for_payload(messages: list[dict], char_budget: int = _TRUNCATION_CHAR_BUDGET) -> list[dict]:
    """Rebuild a compact history that never splits tool-call/result pairs.

    Keeps system messages (first one truncated to 3000 chars) plus complete
    tail groups up to and including the group containing the last user message.
    """
    messages = _prune_orphaned_tool_messages(messages)
    sys_msgs = [dict(m) for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") != "system"]
    if sys_msgs and isinstance(sys_msgs[0].get("content"), str) and len(sys_msgs[0]["content"]) > 3000:
        sys_msgs[0]["content"] = sys_msgs[0]["content"][:3000]

    kept: list[list[dict]] = []
    used = 0
    for group in reversed(_conversation_groups(convo)):
        size = sum(len(str(m.get("content") or "")) for m in group)
        if kept and used + size > char_budget:
            break
        kept.insert(0, group)
        used += size
        if any(m.get("role") == "user" for m in group):
            break
    return sys_msgs + [m for g in kept for m in g]


class OpenAICompatibleAdapter(BaseProviderAdapter):
    """Adapter for Groq, OpenRouter, Cerebras, Qwen, and standard OpenAI endpoints."""

    async def generate(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> LLMResponse:
        model = kwargs.get("model") or profile.model
        if not model and profile.name in ("claude", "openrouter"):
            model = _resolve_openrouter_model(kwargs.get("task", "general"))

        base_url = kwargs.get("base_url") or profile.base_url or gateway.get_default_base_url(profile.name)
        if not base_url:
            raise ValueError(f"No base_url configured for backend '{profile.name}'")

        body = {
            "model": model,
            "messages": gateway.clean_messages(messages),
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        # Enable reasoning / thinking ONLY for explicit reasoning models (e.g. deepseek-r1)
        if any(rm in str(model).lower() for rm in ("deepseek-r1", "r1")) or kwargs.get("enable_thinking"):
            body["enable_thinking"] = True

        if kwargs.get("tools"):
            valid_tools = []
            for t in kwargs["tools"]:
                if not isinstance(t, dict):
                    continue
                if t.get("type") == "function" and isinstance(t.get("function"), dict):
                    fn_obj = t["function"]
                    fn_name = fn_obj.get("name")
                    if fn_name and str(fn_name).strip():
                        valid_tools.append({
                            "type": "function",
                            "function": {
                                "name": str(fn_name).strip(),
                                "description": str(fn_obj.get("description", "")).strip(),
                                "parameters": fn_obj.get("parameters", {"type": "object", "properties": {}}),
                            }
                        })
                else:
                    fn_name = t.get("name")
                    if fn_name and str(fn_name).strip():
                        valid_tools.append({
                            "type": "function",
                            "function": {
                                "name": str(fn_name).strip(),
                                "description": str(t.get("description", "")).strip(),
                                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                            }
                        })
            if valid_tools:
                body["tools"] = valid_tools
                if kwargs.get("tool_choice"):
                    body["tool_choice"] = kwargs["tool_choice"]

        if (kwargs.get("require_json") or kwargs.get("response_format")) and not kwargs.get("tools") and profile.supports_json_mode:
            body["response_format"] = kwargs.get("response_format", {"type": "json_object"})

        data = None
        tools_stripped = False
        tool_retry_pruned = False
        for attempt in range(3):
            key = kwargs.get("api_key") or profile.get_api_key()
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            import httpx
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=body,
                timeout=httpx.Timeout(60.0, connect=15.0),
            )

            if resp.status_code == 400:
                if "json_validate_failed" in resp.text or "response_format" in resp.text:
                    body.pop("response_format", None)
                    continue
                if ("tool_use_failed" in resp.text or "tool call validation" in resp.text
                        or "not in request.tools" in resp.text
                        or "invalid parameter: tools" in resp.text.lower()
                        or "tools not supported" in resp.text.lower()
                        or "does not support tools" in resp.text.lower()):
                    original_msgs = body.get("messages", [])
                    pruned = _prune_orphaned_tool_messages(original_msgs)
                    if not tool_retry_pruned and len(pruned) < len(original_msgs):
                        tool_retry_pruned = True
                        logger.warning(
                            "[%s] Tool validation error â€” pruned %d orphaned tool message(s); retrying WITH tools intact.",
                            profile.name, len(original_msgs) - len(pruned),
                        )
                        body["messages"] = pruned
                        continue
                    logger.error("[%s] Tool validation error persisted. Retrying WITHOUT tools (tools degraded).", profile.name)
                    body["messages"] = pruned
                    body.pop("tools", None)
                    body.pop("tool_choice", None)
                    tools_stripped = True
                    continue

            if resp.status_code == 413 or (resp.status_code == 400 and "reduce your message size" in resp.text):
                logger.warning("[%s] Payload Too Large (413/TPM). Truncating message history...", profile.name)
                body["messages"] = _truncate_for_payload(body.get("messages", []))
                body["max_tokens"] = min(body.get("max_tokens", 4096), 1024)
                continue

            is_rate_limit = resp.status_code in (402, 429) or (resp.status_code == 403 and "quota" in resp.text.lower())
            is_server_error = resp.status_code >= 500
            quota_exhausted = resp.status_code == 402 or (resp.status_code == 403 and "quota" in resp.text.lower())

            if is_rate_limit or is_server_error:
                if quota_exhausted:
                    profile.rotate_key()
                    profile.circuit_breaker.trip_open(300.0)
                    if gateway.rate_limit_manager:
                        retry_after_quota = resp.headers.get("Retry-After", "300")
                        try:
                            gateway.rate_limit_manager.handle_429(profile.name, float(retry_after_quota))
                        except Exception:
                            pass
                    raise RateLimitError(f"{profile.name} quota exhausted, status {resp.status_code}", retry_after_s=300.0)

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

                if attempt < len(profile.api_keys) - 1:
                    profile.rotate_key()
                    logger.warning("[%s] %s. Rotated to next key in pool (attempt %d)...", profile.name, err_label, attempt + 1)
                    continue

                if is_rate_limit:
                    retry_after = resp.headers.get("Retry-After", "5")
                    try:
                        retry_after_f = min(10.0, float(retry_after))
                    except ValueError:
                        retry_after_f = 5.0
                    profile.circuit_breaker.trip_open(retry_after_f)
                    if gateway.rate_limit_manager and resp.status_code == 429:
                        gateway.rate_limit_manager.handle_429(profile.name, retry_after_f)
                    raise RateLimitError(f"{profile.name} rate limited, status {resp.status_code}", retry_after_s=retry_after_f)
                else:
                    logger.error("[%s] Server error persisted after %d attempts: %s", profile.name, attempt + 1, resp.status_code)

            if resp.status_code >= 400:
                logger.error("[%s] HTTP %s: %s", profile.name, resp.status_code, resp.text)
            resp.raise_for_status()
            data = resp.json()
            break

        if not data or "choices" not in data:
            raise ValueError(f"Invalid empty response from backend '{profile.name}'")

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

        resp_text = msg_obj.get("content") or ""
        thought_trace = msg_obj.get("reasoning_content") or msg_obj.get("thought") or ""
        if not tool_calls and resp_text and gateway and hasattr(gateway, "extract_embedded_tool_calls"):
            cleaned_text, extracted_calls = gateway.extract_embedded_tool_calls(resp_text)
            if extracted_calls:
                tool_calls = extracted_calls
                resp_text = cleaned_text

        return LLMResponse(
            text=resp_text,
            backend=profile.name,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            tool_calls=tool_calls,
            tools_stripped=tools_stripped,
            thought=thought_trace,
        )

    async def stream_events(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        import httpx

        model = kwargs.get("model") or profile.model
        if not model and profile.name in ("claude", "openrouter"):
            model = _resolve_openrouter_model(kwargs.get("task", "general"))

        base_url = kwargs.get("base_url") or profile.base_url or gateway.get_default_base_url(profile.name)
        key = kwargs.get("api_key") or profile.get_api_key()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "messages": gateway.clean_messages(messages),
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "stream": True,
        }
        # Enable reasoning / thinking for DashScope & reasoning-capable models
        if "dashscope" in str(base_url).lower() or any(rm in str(model).lower() for rm in ("deepseek-r1", "qwen-max", "deepseek-v4", "r1")):
            body["enable_thinking"] = True

        # Include tools if provided and supported
        if kwargs.get("tools") and getattr(profile, "supports_tools", True):
            valid_tools = []
            for t in kwargs["tools"]:
                if not isinstance(t, dict):
                    continue
                if t.get("type") == "function" and isinstance(t.get("function"), dict):
                    fn_obj = t["function"]
                    fn_name = fn_obj.get("name")
                    if fn_name and str(fn_name).strip():
                        valid_tools.append({
                            "type": "function",
                            "function": {
                                "name": str(fn_name).strip(),
                                "description": str(fn_obj.get("description", "")).strip(),
                                "parameters": fn_obj.get("parameters", {"type": "object", "properties": {}}),
                            }
                        })
                else:
                    fn_name = t.get("name")
                    if fn_name and str(fn_name).strip():
                        valid_tools.append({
                            "type": "function",
                            "function": {
                                "name": str(fn_name).strip(),
                                "description": str(t.get("description", "")).strip(),
                                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                            }
                        })
            if valid_tools:
                body["tools"] = valid_tools
                if kwargs.get("tool_choice"):
                    body["tool_choice"] = kwargs["tool_choice"]

        async with client.stream(
            "POST", f"{base_url}/chat/completions",
            headers=headers, json=body,
            timeout=httpx.Timeout(45.0, connect=10.0),
        ) as resp:
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "60")
                if gateway.rate_limit_manager:
                    gateway.rate_limit_manager.handle_429(profile.name, float(retry_after))
                raise RateLimitError(f"{profile.name} rate limited")
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices")
                        if not choices or not isinstance(choices, list) or len(choices) == 0:
                            continue
                        delta = choices[0].get("delta") or {}
                        reasoning_chunk = delta.get("reasoning_content") or delta.get("thought") or ""
                        if reasoning_chunk:
                            yield {"type": "thinking_delta", "text": reasoning_chunk}
                        content = delta.get("content", "")
                        if content:
                            content = gateway.sanitize_stream_delta(content)
                            if content:
                                yield {"type": "text_delta", "text": content}
                        tool_calls = delta.get("tool_calls")
                        if tool_calls and isinstance(tool_calls, list):
                            for tc in tool_calls:
                                if isinstance(tc, dict):
                                    idx = tc.get("index", 0)
                                    fn = tc.get("function") or {}
                                    yield {
                                        "type": "tool_call_delta",
                                        "index": idx,
                                        "id": tc.get("id"),
                                        "name": fn.get("name"),
                                        "arguments_delta": fn.get("arguments", ""),
                                    }
                    except json.JSONDecodeError:
                        continue

    async def stream(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        async for ev in self.stream_events(profile, messages, client, gateway, **kwargs):
            if ev.get("type") == "text_delta" and ev.get("text"):
                yield ev["text"]


class GeminiAdapter(BaseProviderAdapter):
    """Adapter for Google Gemini APIs."""

    async def generate(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> LLMResponse:
        model = kwargs.get("model") or profile.model or "gemini-2.5-flash"
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
                contents.append({"role": gemini_role, "parts": parts})
            else:
                contents.append({"role": gemini_role, "parts": [{"text": str(content)}]})

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.7),
                "maxOutputTokens": kwargs.get("max_tokens", 4096),
            }
        }

        if (kwargs.get("require_json") or kwargs.get("response_format")) and profile.supports_json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {
            "x-goog-api-key": kwargs.get("api_key") or profile.get_api_key(),
            "Content-Type": "application/json",
        }

        import httpx
        resp = await client.post(url, headers=headers, json=body, timeout=httpx.Timeout(30.0, connect=10.0))

        if resp.status_code >= 400:
            if len(profile.api_keys) > 1:
                profile.rotate_key()
            if resp.status_code == 429:
                raise RateLimitError("Gemini rate limited")
            resp.raise_for_status()
        data = resp.json()

        text = ""
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and "text" in parts[0]:
                text = parts[0]["text"]
        usage = data.get("usageMetadata", {})

        tool_calls: list[dict] = []
        if text and gateway and hasattr(gateway, "extract_embedded_tool_calls"):
            cleaned_text, extracted_calls = gateway.extract_embedded_tool_calls(text)
            if extracted_calls:
                tool_calls = extracted_calls
                text = cleaned_text

        return LLMResponse(
            text=text,
            backend=profile.name,
            model=model,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        import httpx

        model = kwargs.get("model") or profile.model or "gemini-2.5-flash"
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
                contents.append({"role": gemini_role, "parts": parts})
            else:
                contents.append({"role": gemini_role, "parts": [{"text": str(content)}]})

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.7),
                "maxOutputTokens": kwargs.get("max_tokens", 4096),
            },
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
        headers = {
            "x-goog-api-key": kwargs.get("api_key") or profile.get_api_key(),
            "Content-Type": "application/json",
        }

        async with client.stream(
            "POST", url, headers=headers, json=body,
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as resp:
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
                                    content = gateway.sanitize_stream_delta(part["text"])
                                    if content:
                                        yield content
                    except json.JSONDecodeError:
                        continue


class OllamaAdapter(BaseProviderAdapter):
    """Adapter for local Ollama server."""

    async def generate(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> LLMResponse:
        model = kwargs.get("model") or profile.model
        host = kwargs.get("base_url") or profile.base_url or "http://127.0.0.1:11434"
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.7),
                "num_predict": kwargs.get("max_tokens", 4096),
            }
        }
        if (kwargs.get("require_json") or kwargs.get("response_format")) and profile.supports_json_mode:
            body["format"] = "json"

        import httpx
        resp = await client.post(f"{host}/api/chat", json=body, timeout=httpx.Timeout(30.0, connect=10.0))
        resp.raise_for_status()
        data = resp.json()

        text = data.get("message", {}).get("content", "")
        tool_calls: list[dict] = []
        if text and gateway and hasattr(gateway, "extract_embedded_tool_calls"):
            cleaned_text, extracted_calls = gateway.extract_embedded_tool_calls(text)
            if extracted_calls:
                tool_calls = extracted_calls
                text = cleaned_text

        return LLMResponse(
            text=text,
            backend=profile.name,
            model=model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        import httpx

        model = kwargs.get("model") or profile.model
        host = kwargs.get("base_url") or profile.base_url or "http://127.0.0.1:11434"
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": kwargs.get("temperature", 0.7),
                "num_predict": kwargs.get("max_tokens", 4096),
            },
        }

        async with client.stream(
            "POST", f"{host}/api/chat", json=body,
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if "message" in data and "content" in data["message"]:
                        content = gateway.sanitize_stream_delta(data["message"]["content"])
                        if content:
                            yield content
                    if data.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue


class AnthropicAdapter(BaseProviderAdapter):
    """Adapter for Anthropic Claude native Messages API (/v1/messages)."""

    async def generate(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        task: str = "general",
        require_json: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        model = kwargs.get("model") or profile.model or "claude-3-5-sonnet-20241022"
        base_url = (kwargs.get("base_url") or profile.base_url or "https://api.anthropic.com/v1").rstrip("/")
        active_key = kwargs.get("api_key") or profile.get_api_key()

        system_text = ""
        anthropic_msgs = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_text += f"{content}\n"
            elif role in ("user", "assistant"):
                anthropic_msgs.append({"role": role, "content": str(content)})

        if not anthropic_msgs:
            anthropic_msgs = [{"role": "user", "content": "Hello"}]

        headers = {
            "x-api-key": active_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "messages": anthropic_msgs,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()

        resp = await client.post(
            f"{base_url}/messages",
            headers=headers,
            json=payload,
            timeout=float(kwargs.get("timeout", 60.0)),
        )
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after", "60")
            raise RateLimitError(f"{profile.name} rate limited", retry_after_s=float(retry_after))
        resp.raise_for_status()
        data = resp.json()

        content_blocks = data.get("content", [])
        text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        resp_text = "".join(text_parts)
        usage = data.get("usage", {})

        return LLMResponse(
            text=resp_text,
            model=data.get("model", model),
            backend=profile.name,
            usage=TokenUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            ),
        )

    async def stream_events(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        task: str = "general",
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        model = kwargs.get("model") or profile.model or "claude-3-5-sonnet-20241022"
        base_url = (kwargs.get("base_url") or profile.base_url or "https://api.anthropic.com/v1").rstrip("/")
        active_key = kwargs.get("api_key") or profile.get_api_key()

        system_text = ""
        anthropic_msgs = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_text += f"{content}\n"
            elif role in ("user", "assistant"):
                anthropic_msgs.append({"role": role, "content": str(content)})

        if not anthropic_msgs:
            anthropic_msgs = [{"role": "user", "content": "Hello"}]

        headers = {
            "x-api-key": active_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "messages": anthropic_msgs,
            "stream": True,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()

        async with client.stream(
            "POST",
            f"{base_url}/messages",
            headers=headers,
            json=payload,
            timeout=float(kwargs.get("timeout", 60.0)),
        ) as resp:
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after", "60")
                raise RateLimitError(f"{profile.name} rate limited", retry_after_s=float(retry_after))
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        break
                    try:
                        ev_data = json.loads(data_str)
                        ev_type = ev_data.get("type")
                        if ev_type == "content_block_delta":
                            delta = ev_data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                chunk = delta.get("text", "")
                                if chunk:
                                    clean_chunk = gateway.sanitize_stream_delta(chunk)
                                    if clean_chunk:
                                        yield {"type": "text_delta", "text": clean_chunk}
                    except Exception:
                        continue

    async def stream(
        self,
        profile: BackendProfile,
        messages: list[dict],
        client: Any,
        gateway: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        async for ev in self.stream_events(profile, messages, client, gateway, **kwargs):
            if ev.get("type") == "text_delta" and ev.get("text"):
                yield ev["text"]


# =============================================================================
# 4. DYNAMIC PARETO ROUTER (RouteLLM SOTA)
# =============================================================================

class ParetoRouter:
    """
    Dynamic Pareto Multi-Backend Router.
    Ranks healthy backends matching constraints along the Pareto frontier:
    1. Latency Tier ('fast' -> 'standard' -> 'slow')
    2. EWMA Latency (smoothed runtime latency)
    3. Failure Count
    """

    TIER_SCORES = {"fast": 0, "standard": 1, "slow": 2}

    def __init__(self, profiles: dict[str, BackendProfile], task_routing: dict[str, list[str]]) -> None:
        self.profiles = profiles
        self.task_routing = task_routing

    def route(
        self,
        task: str = "general",
        constraints: Optional[dict[str, Any]] = None,
        ewma_latencies: Optional[dict[str, float]] = None,
    ) -> list[str]:
        """Compute the prioritized list of backend names to attempt."""
        cons = dict(constraints or {})
        ewma_map = dict(ewma_latencies or {})
        task_candidates = self.task_routing.get(task, self.task_routing.get("general", []))

        pref_backend = cons.get("preferred_backend")
        client_key = cons.get("client_api_key")

        # 1. Capability & Availability filtering
        valid_candidates: list[BackendProfile] = []
        for name in task_candidates:
            profile = self.profiles.get(name)
            if not profile:
                continue
            is_avail = profile.is_available(client_api_key=client_key if name == pref_backend else None)
            if not is_avail:
                continue
            if cons.get("require_tools") and not profile.supports_tools:
                continue
            if cons.get("require_json") and not profile.supports_json_mode:
                continue
            if cons.get("min_context", 0) > profile.context_limit:
                continue
            valid_candidates.append(profile)

        # Append any remaining available backends in profiles as fallback options
        for profile in self.profiles.values():
            if profile in valid_candidates:
                continue
            is_avail = profile.is_available(client_api_key=client_key if profile.name == pref_backend else None)
            if not is_avail:
                continue
            if cons.get("require_tools") and not profile.supports_tools:
                continue
            if cons.get("require_json") and not profile.supports_json_mode:
                continue
            if cons.get("min_context", 0) > profile.context_limit:
                continue
            valid_candidates.append(profile)

        # If preferred backend was requested and available, ensure it is in valid_candidates
        if pref_backend and pref_backend in self.profiles:
            pref_prof = self.profiles[pref_backend]
            if pref_prof not in valid_candidates and pref_prof.is_available(client_api_key=client_key):
                valid_candidates.insert(0, pref_prof)

        if not valid_candidates:
            return list(task_candidates)

        # 2. Dynamic Pareto Frontier Sorting: Preferred Backend -> Task Routing Priority -> Latency Tier -> EWMA Latency -> Fail Count
        pref_backend = cons.get("preferred_backend")

        def _pareto_key(p: BackendProfile) -> tuple[int, int, int, float, int]:
            is_preferred = 0 if (pref_backend and p.name == pref_backend) else 1
            idx = task_candidates.index(p.name) if p.name in task_candidates else 999
            tier = self.TIER_SCORES.get(p.latency_tier, 1)
            ewma = ewma_map.get(p.name, p.ewma_latency_ms)
            lat_val = ewma if ewma > 0 else (100.0 if tier == 0 else (500.0 if tier == 1 else 2000.0))
            return (is_preferred, idx, tier, lat_val, p.circuit_breaker.fail_count)

        valid_candidates.sort(key=_pareto_key)
        return [p.name for p in valid_candidates]


# =============================================================================
# 5. NEXUS LLM GATEWAY (Main AIHandler)
# =============================================================================

class AIHandler:
    """
    NEXUS LLM Gateway v9.0.
    Multi-Provider Strategy Router with Dynamic Pareto Routing, Native Structured Outputs,
    Resilient Multi-Pass JSON Parser, and Active Key Redaction.
    """

    _default_base_urls = {
        "groq": "https://api.groq.com/openai/v1",
        "groq_qwen": "https://api.groq.com/openai/v1",
        "groq_fast": "https://api.groq.com/openai/v1",
        "claude": "https://api.anthropic.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "openai": "https://api.openai.com/v1",
        "gpt4o": "https://api.openai.com/v1",
        "cerebras": "https://api.cerebras.ai/v1",
        "nemotron_free": "https://openrouter.ai/api/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "deepseek": "https://api.deepseek.com",
        "deepseek_v32": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen_flash": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen36_flash": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen35_flash": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen_plus": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "huggingface": "https://router.huggingface.co/v1",
    }

    _BACKEND_ALIASES: dict[str, str] = {
        "anthropic": "claude",
        "alibaba": "qwen_flash",
        "qwen": "qwen_flash",
    }

    def _resolve_backend_name(self, name: str) -> str:
        clean = str(name or "").strip().lower()
        backends_map = getattr(self, "backends", {}) or {}
        if clean in self._BACKEND_ALIASES:
            canonical = self._BACKEND_ALIASES[clean]
            if canonical in backends_map:
                return canonical
            return canonical
        if clean in backends_map:
            return clean
        return clean

    def set_active_provider(self, provider_name: str) -> None:
        """Set the active/default LLM backend dynamically at runtime without server restart."""
        resolved = self._resolve_backend_name(provider_name)
        self.default_provider = resolved
        logger.info("AIHandler: Active provider switched to '%s' (input: '%s')", resolved, provider_name)

    def ensure_backend(
        self,
        provider_name: str,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
    ) -> Optional[BackendProfile]:
        """Ensure a BackendProfile exists and is ready for a given provider/alias."""
        resolved = self._resolve_backend_name(provider_name)
        profile = self.backends.get(resolved) or self.backends.get(provider_name)
        if profile:
            profile.enabled = True
            if api_key:
                profile.api_key = api_key
            if model:
                profile.model = model
            if base_url:
                profile.base_url = base_url
            return profile

        if resolved == "gemini":
            adapter_type = "gemini"
        elif resolved == "ollama":
            adapter_type = "ollama"
        elif resolved in ("claude", "anthropic"):
            adapter_type = "openai" if "openrouter" in (base_url or "") else "anthropic"
        else:
            adapter_type = "openai"

        resolved_base_url = base_url or self.get_default_base_url(resolved) or self.get_default_base_url(provider_name) or ""
        profile = BackendProfile(
            name=resolved,
            enabled=True,
            adapter_type=adapter_type,
            api_key=api_key or os.environ.get(f"{provider_name.upper()}_API_KEY", ""),
            model=model or "",
            base_url=resolved_base_url,
            circuit_breaker=CircuitBreaker(max_failures=5, cooldown_s=15),
        )
        self.backends[resolved] = profile
        self.backends[provider_name] = profile
        return profile

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        rate_limit_manager: Any = None,
        ws_broadcast: Any = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        env_provider = (
            os.environ.get("MAKIMA_PROVIDER")
            or os.environ.get("MAKIMA_ACTIVE_PROVIDER")
            or os.environ.get("MAKIMA_DEFAULT_PROVIDER")
            or ""
        ).strip().lower()
        cfg_provider = (
            self.config.get("llm", {}).get("default_provider")
            or self.config.get("llm", {}).get("active_provider")
            or ""
        ).strip().lower()
        raw_provider = env_provider or cfg_provider
        self.backends: dict[str, BackendProfile] = {}
        self.default_provider: str = self._resolve_backend_name(raw_provider) if (raw_provider and raw_provider != "auto") else ""
        self.rate_limit_manager = rate_limit_manager
        self.ws_broadcast = ws_broadcast
        self._http_client: Optional[Any] = None

        # Provider Strategy Adapters Registry
        self.adapters: dict[str, BaseProviderAdapter] = {
            "openai": OpenAICompatibleAdapter(),
            "gemini": GeminiAdapter(),
            "ollama": OllamaAdapter(),
            "anthropic": AnthropicAdapter(),
        }

        # Canonical task routing map: inclusive priority cascade across all available providers
        # ParetoRouter filters out backends that do not have active credentials in 0.001ms.
        standard_cascade = [
            "gemini", "groq", "groq_fast", "deepseek", "qwen_flash",
            "openai", "claude", "cerebras", "ollama", "openrouter"
        ]
        fast_cascade = [
            "groq_fast", "groq", "cerebras", "gemini", "qwen_flash",
            "deepseek", "openai", "claude", "ollama"
        ]
        code_cascade = [
            "deepseek", "openai", "claude", "qwen_flash", "gemini",
            "groq", "cerebras", "ollama"
        ]
        deep_cascade = [
            "deepseek", "claude", "gemini", "openai", "qwen_flash",
            "groq", "cerebras", "ollama"
        ]

        self.task_routing: dict[str, list[str]] = {
            "fast_chat":             list(fast_cascade),
            "intent_classification": list(fast_cascade),
            "entity_extraction":     list(fast_cascade),
            "routing":               list(fast_cascade),
            "query_rewriting":       list(fast_cascade),
            "persona_extraction":    list(standard_cascade),
            "research_decomp":       list(deep_cascade),
            "general":               list(standard_cascade),
            "research":              list(deep_cascade),
            "analysis":              list(deep_cascade),
            "automation":            list(standard_cascade),
            "code":                  list(code_cascade),
            "debugging":             list(code_cascade),
            "refactoring":           list(code_cascade),
            "data_analysis":         list(deep_cascade),
            "browser":               list(standard_cascade),
            "system_control":        list(fast_cascade),
            "media":                 list(fast_cascade),
            "vision":                ["gemini", "openai", "claude", "qwen_flash"],
            "creative":              list(standard_cascade),
            "daily_briefing":        list(deep_cascade),
            "decompose":             list(deep_cascade),
            "synthesize":            list(deep_cascade),
            "consolidation":         list(deep_cascade),
            "devops":                list(code_cascade),
            "offline":               ["ollama", "gemini", "qwen_flash"],
            "privacy_mode":          ["ollama", "gemini", "qwen_flash"],
            "mock":                  ["mock_primary", "mock_secondary"],
            "reflexion":             list(deep_cascade),
        }

        self._init_backends(self.config)

        # EWMA latency tracking
        self._backend_latency: dict[str, deque] = {}
        self._backend_ewma_latency: dict[str, float] = {}

        # Pareto Router initialization
        self.router = ParetoRouter(self.backends, self.task_routing)

    def get_default_base_url(self, backend_name: str) -> Optional[str]:
        return self._default_base_urls.get(backend_name)

    def _get_http_client(self) -> Any:
        """Get or lazily instantiate persistent async HTTP connection pool with HTTP/2 multiplexing."""
        import httpx
        if self._http_client is None or getattr(self._http_client, "is_closed", True):
            raw_tls = self.config.get("tls_verify") if "tls_verify" in self.config else (self.config.get("llm", {}).get("tls_verify") if isinstance(self.config.get("llm"), dict) else None)
            if raw_tls is None:
                raw_tls = os.environ.get("MAKIMA_TLS_VERIFY", "false").lower() in ("true", "1", "yes")
            tls_verify = bool(raw_tls)
            limits = httpx.Limits(
                max_keepalive_connections=50,
                max_connections=100,
                keepalive_expiry=300.0,
            )
            timeout = httpx.Timeout(30.0, connect=10.0, read=30.0, write=15.0, pool=15.0)
            try:
                self._http_client = httpx.AsyncClient(
                    verify=tls_verify,
                    limits=limits,
                    timeout=timeout,
                    follow_redirects=True,
                    http2=True,
                )
            except Exception as _h2_err:
                logger.debug("HTTP/2 AsyncClient init failed, falling back to HTTP/1.1: %s", _h2_err)
                self._http_client = httpx.AsyncClient(
                    verify=tls_verify,
                    limits=limits,
                    timeout=timeout,
                    follow_redirects=True,
                    http2=False,
                )
        return self._http_client

    async def close(self) -> None:
        """Gracefully terminate HTTP connection pool on engine shutdown."""
        if self._http_client is not None and not getattr(self._http_client, "is_closed", True):
            try:
                await self._http_client.aclose()
            except Exception as e:
                logger.debug("AIHandler HTTP client close error: %s", e)
            finally:
                self._http_client = None

    aclose = close

    def _record_latency(self, backend_name: str, latency_ms: float) -> None:
        """Record backend latency using EWMA (alpha=0.25) and rolling window."""
        if latency_ms <= 0:
            return
        alpha = 0.25
        prev_ewma = self._backend_ewma_latency.get(backend_name)
        if prev_ewma is None or prev_ewma <= 0:
            self._backend_ewma_latency[backend_name] = float(latency_ms)
        else:
            self._backend_ewma_latency[backend_name] = alpha * float(latency_ms) + (1.0 - alpha) * prev_ewma

        if backend_name in self.backends:
            self.backends[backend_name].ewma_latency_ms = self._backend_ewma_latency[backend_name]

        dq = self._backend_latency.setdefault(backend_name, deque(maxlen=8))
        dq.append(latency_ms)

    @staticmethod
    def _sniff_environment_keys(backend_name: str, explicit_env: str = "") -> list[str]:
        """
        Smart Credential Sniffer:
        Resolves API keys for a backend using:
        1. Explicitly configured api_key_env
        2. Well-known environment variable aliases (including MAKIMA_* and provider-specific names)
        3. Fuzzy environment variable name matching (e.g., MY_GEMINI_KEY, GROQ_KEY, etc.)
        4. Cryptographic / vendor prefix signatures (e.g., AIzaSy... for Gemini, gsk_... for Groq, etc.)
        """
        found_keys: list[str] = []
        name_lower = backend_name.lower()

        # Step 1: Explicit env
        if explicit_env and os.environ.get(explicit_env):
            val = os.environ[explicit_env].strip()
            if val and val not in found_keys:
                found_keys.append(val)

        # Step 2: Standard and well-known aliases per backend family
        alias_map: dict[str, list[str]] = {
            "gemini": [
                "GEMINI_API_KEY", "GOOGLE_API_KEY", "MAKIMA_GEMINI_KEY",
                "GEMINI_KEY", "GOOGLE_KEY", "GEMINI_TOKEN",
            ],
            "groq": [
                "GROQ_API_KEY", "MAKIMA_GROQ_KEY", "GROQ_KEY", "GROQ_TOKEN",
            ],
            "openrouter": [
                "OPENROUTER_API_KEY", "MAKIMA_OPENROUTER_KEY", "OPENROUTER_KEY",
            ],
            "claude": [
                "ANTHROPIC_API_KEY", "MAKIMA_ANTHROPIC_KEY", "CLAUDE_API_KEY",
                "ANTHROPIC_KEY", "CLAUDE_KEY",
            ],
            "openai": [
                "OPENAI_API_KEY", "MAKIMA_OPENAI_KEY", "OPENAI_KEY", "OPENAI_TOKEN",
            ],
            "qwen": [
                "DASHSCOPE_API_KEY", "QWEN_API_KEY", "MAKIMA_QWEN_KEY",
                "DASHSCOPE_KEY", "QWEN_KEY", "ALIYUN_API_KEY",
            ],
            "deepseek": [
                "DEEPSEEK_API_KEY", "MAKIMA_DEEPSEEK_KEY", "DEEPSEEK_KEY",
            ],
            "cerebras": [
                "CEREBRAS_API_KEY", "MAKIMA_CEREBRAS_KEY", "CEREBRAS_KEY",
            ],
            "huggingface": [
                "HF_TOKEN", "HUGGINGFACE_API_KEY", "MAKIMA_HF_KEY", "HF_API_KEY",
            ],
        }

        # Resolve family
        family = name_lower
        if family.startswith("qwen"):
            family = "qwen"
        elif family.startswith("groq"):
            family = "groq"
        elif family.startswith("gemini"):
            family = "gemini"
        elif family.startswith("deepseek"):
            family = "deepseek"
        elif family in ("claude", "anthropic"):
            family = "claude"

        for k_name in alias_map.get(family, []):
            val = os.environ.get(k_name, "").strip()
            if val and is_valid_api_key(val) and val not in found_keys:
                found_keys.append(val)

        # Step 3: Generic patterns e.g. MAKIMA_<NAME>_KEY, <NAME>_API_KEY
        for generic_k in (f"MAKIMA_{backend_name.upper()}_KEY", f"{backend_name.upper()}_API_KEY", f"{backend_name.upper()}_KEY"):
            val = os.environ.get(generic_k, "").strip()
            if val and is_valid_api_key(val) and val not in found_keys:
                found_keys.append(val)

        # Step 4: Fuzzy Environment Variable Name Scan
        fuzzy_keywords = {
            "gemini": ("gemini", "google_ai"),
            "groq": ("groq",),
            "openrouter": ("openrouter",),
            "claude": ("anthropic", "claude"),
            "openai": ("openai",),
            "qwen": ("dashscope", "qwen", "aliyun"),
            "deepseek": ("deepseek",),
            "cerebras": ("cerebras",),
            "huggingface": ("huggingface", "hf_token"),
        }
        kw_list = fuzzy_keywords.get(family, ())
        for env_k, env_v in os.environ.items():
            k_clean = env_k.lower()
            v_clean = env_v.strip()
            if not v_clean or not is_valid_api_key(v_clean) or v_clean in found_keys:
                continue
            if any(kw in k_clean for kw in kw_list) and any(suf in k_clean for suf in ("key", "token", "secret", "auth", "api")):
                found_keys.append(v_clean)

        # Step 5: Vendor Prefix Signature Sniffing (Arbitrary variable name like KUCH_BHI=...)
        prefix_signatures = {
            "gemini": ("AIzaSy",),
            "groq": ("gsk_",),
            "openrouter": ("sk-or-v1-",),
            "claude": ("sk-ant-",),
            "openai": ("sk-proj-", "sk-admin-"),
        }
        sig_list = prefix_signatures.get(family, ())
        if sig_list:
            for env_k, env_v in os.environ.items():
                v_clean = env_v.strip()
                if not v_clean or not is_valid_api_key(v_clean) or v_clean in found_keys:
                    continue
                if any(v_clean.startswith(sig) for sig in sig_list):
                    found_keys.append(v_clean)

        return found_keys

    def _init_backends(self, config: dict[str, Any]) -> None:
        """Initialize dynamic backend profiles from YAML configuration."""
        backends_cfg = config.get("llm", {}).get("backends", {})
        if not backends_cfg:
            import yaml
            cfg_file = Path(__file__).resolve().parents[2] / "configs" / "default.yaml"
            if cfg_file.exists():
                try:
                    with open(cfg_file, encoding="utf-8") as f:
                        default_cfg = yaml.safe_load(f) or {}
                    backends_cfg = default_cfg.get("llm", {}).get("backends", {})
                except Exception as e:
                    logger.warning("Error reading default.yaml config: %s", e)

        explicit_backends = bool(config.get("llm", {}).get("backends"))
        if explicit_backends:
            self.task_routing = {}

        for name, cfg in backends_cfg.items():
            if "api_key" in cfg and not cfg.get("api_key") and not cfg.get("api_key_env"):
                raw_keys = []
            else:
                api_key_env = cfg.get("api_key_env", "")
                raw_keys = self._sniff_environment_keys(name, api_key_env)
                if cfg.get("api_key"):
                    raw_keys.append(cfg.get("api_key"))

            api_keys = []
            for k_str in raw_keys:
                for k in str(k_str).split(","):
                    k = k.strip()
                    if k and k not in api_keys:
                        api_keys.append(k)

            api_key = api_keys[0] if api_keys else ""
            if len(api_keys) <= 1:
                api_keys = []

            # Backend enabled status (respects explicit enabled: false)
            has_credentials = bool(api_key or api_keys or name == "ollama")
            is_enabled = bool(cfg.get("enabled", True))

            # Strict provider filtering: If disabled or non-Alibaba when only_alibaba is active, skip completely
            only_alibaba = bool(
                config.get("llm", {}).get("only_alibaba")
                or config.get("llm", {}).get("only_qwen")
                or os.environ.get("MAKIMA_ONLY_ALIBABA", "").lower() in ("1", "true", "yes")
                or os.environ.get("MAKIMA_ONLY_QWEN", "").lower() in ("1", "true", "yes")
            )
            is_alibaba_backend = bool(
                name.startswith("qwen")
                or name == "deepseek_v32"
                or cfg.get("api_key_env") == "DASHSCOPE_API_KEY"
                or "aliyuncs.com" in str(cfg.get("host", ""))
            )
            if only_alibaba and not is_alibaba_backend:
                logger.debug("[%s] Provider disabled: non-Alibaba backend filtered by only_alibaba policy.", name)
                continue
            if not is_enabled:
                logger.debug("[%s] Provider disabled via config.", name)
                continue

            cb_cfg = cfg.get("circuit_breaker", {})

            # Resolve adapter type
            adapter_type = cfg.get("adapter_type", "openai")
            if name == "gemini":
                adapter_type = "gemini"
            elif name == "ollama":
                adapter_type = "ollama"
            elif name in ("claude", "anthropic"):
                host_str = str(cfg.get("host") or cfg.get("base_url") or "")
                adapter_type = "openai" if "openrouter" in host_str else "anthropic"

            # Resolve latency tier
            latency_tier = cfg.get("latency_tier", "standard")
            if name in ("groq_fast", "cerebras", "groq"):
                latency_tier = "fast"
            elif name in ("gemini", "claude", "openai"):
                latency_tier = "standard"
            elif name in ("ollama", "gpt4o"):
                latency_tier = "slow"

            tasks_list = cfg.get("tasks") or [
                "fast_chat", "general", "intent_classification", "routing",
                "system_control", "media", "automation", "trivial",
                "code", "research", "analysis", "creative", "synthesis",
            ]

            self.backends[name] = BackendProfile(
                name=name,
                enabled=is_enabled,
                adapter_type=adapter_type,
                api_key=api_key,
                api_keys=api_keys,
                model=cfg.get("model", ""),
                context_limit=cfg.get("context_limit", 128000),
                tasks=tasks_list,
                base_url=cfg.get("base_url") or cfg.get("host"),
                supports_tools=cfg.get("supports_tools", True),
                supports_json_mode=cfg.get("supports_json_mode", True),
                latency_tier=latency_tier,
                circuit_breaker=CircuitBreaker(
                    max_failures=cb_cfg.get("max_failures", 3),
                    cooldown_s=cb_cfg.get("cooldown_s", 60),
                ),
                rate_limit_tpm=cfg.get("rate_limit_tpm", 0),
                rate_limit_rpm=cfg.get("rate_limit_rpm", 0),
            )

            if is_enabled:
                for t in tasks_list:
                    if t in self.task_routing:
                        if name not in self.task_routing[t]:
                            self.task_routing[t].append(name)
                    else:
                        self.task_routing[t] = [name]

        # Alias cross-registration so both UI catalog names and YAML backend names resolve
        alias_pairs = [
            ("anthropic", "claude"),
            ("alibaba", "qwen_flash"),
            ("qwen", "qwen_flash"),
            ("openai", "gpt4o"),
        ]
        for a_alias, a_target in alias_pairs:
            if a_target in self.backends and a_alias not in self.backends:
                self.backends[a_alias] = self.backends[a_target]
            elif a_alias in self.backends and a_target not in self.backends:
                self.backends[a_target] = self.backends[a_alias]

        # Load per-agent model mappings from YAML configuration
        self.agent_models: dict[str, str] = {}
        agents_cfg = config.get("agents", {})
        cfg_file = Path(__file__).resolve().parent.parent.parent / "configs" / "default.yaml"
        if not agents_cfg and cfg_file.exists():
            try:
                with open(cfg_file, encoding="utf-8") as f:
                    _dcfg = yaml.safe_load(f) or {}
                agents_cfg = _dcfg.get("agents", {})
            except Exception:
                pass
        if isinstance(agents_cfg, dict):
            models_map = agents_cfg.get("models", {})
            if isinstance(models_map, dict):
                for ag_k, bg_v in models_map.items():
                    self.agent_models[str(ag_k).lower().strip()] = str(bg_v).strip()

        backend_info = {name: len(b.api_keys) if b.api_keys else (1 if b.api_key else 0) for name, b in self.backends.items()}
        logger.info("NEXUS Gateway: Initialized %d LLM backends: %s (agent models: %s)", len(self.backends), backend_info, self.agent_models)

    def _get_backend_order(
        self,
        task: str,
        constraints: Optional[dict[str, Any]] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> list[str]:
        """Get ordered list of backends via Dynamic Pareto Router + Per-Agent Model Routing."""
        resolved_agent = agent_name or kwargs.get("agent_name")
        cons = dict(constraints or {})
        # Multi-user isolation: check per-request client provider context first
        req_ctx: dict[str, Any] = {}
        try:
            from .core.orchestration_engine import client_request_context
            req_ctx = client_request_context.get({}) or {}
        except Exception:
            pass

        req_provider = cons.get("preferred_backend") or kwargs.get("provider") or req_ctx.get("provider")
        req_key = kwargs.get("api_key") or req_ctx.get("api_key")
        req_model = kwargs.get("model") or req_ctx.get("model")
        req_base_url = kwargs.get("base_url") or req_ctx.get("base_url")

        if req_provider:
            pref_name = self._resolve_backend_name(req_provider)
            if pref_name not in self.backends:
                self.ensure_backend(req_provider, api_key=req_key or "", model=req_model or "", base_url=req_base_url or "")
            profile = self.backends.get(pref_name) or self.backends.get(req_provider)
            if profile and profile.is_available(client_api_key=req_key):
                cons["preferred_backend"] = pref_name
                if req_key:
                    cons["client_api_key"] = req_key
        elif self.default_provider and "preferred_backend" not in cons:
            pref_name = self._resolve_backend_name(self.default_provider)
            profile = self.backends.get(pref_name) or self.backends.get(self.default_provider)
            if profile and profile.is_available():
                cons["preferred_backend"] = pref_name

        if constraints is None and "preferred_backend" not in cons:
            candidates = list(self.task_routing.get(task, self.task_routing.get("general", [])))
            if len(candidates) >= 2:
                primary = candidates[0]
                pri_lat = self._backend_latency.get(primary)
                if pri_lat and len(pri_lat) > 0 and pri_lat[-1] > 4000.0:
                    for alt in candidates[1:]:
                        alt_lat = self._backend_latency.get(alt)
                        if alt_lat and len(alt_lat) > 0 and pri_lat[-1] >= 2.0 * alt_lat[-1]:
                            candidates.remove(alt)
                            candidates.insert(0, alt)
                            break
        else:
            candidates = self.router.route(task=task, constraints=cons, ewma_latencies=self._backend_ewma_latency)

        # Prioritize active/default provider if set and healthy
        if cons.get("preferred_backend"):
            pref = cons["preferred_backend"]
            if pref in candidates:
                if candidates and candidates[0] != pref:
                    candidates.remove(pref)
                    candidates.insert(0, pref)
            elif pref in self.backends:
                candidates.insert(0, pref)

        # Prioritize agent's configured model from YAML ONLY if user did not specify preferred_backend or client key
        has_user_override = bool(cons.get("preferred_backend") or req_provider or req_key)
        if resolved_agent and not has_user_override:
            ag_clean = str(resolved_agent).lower().strip()
            pref_backend = (
                self.agent_models.get(ag_clean)
                or self.agent_models.get(f"{ag_clean}_agent")
                or self.agent_models.get(ag_clean.replace("_agent", ""))
            )
            if pref_backend and pref_backend != "auto" and pref_backend in self.backends:
                profile = self.backends.get(pref_backend)
                if profile and profile.is_available():
                    candidates = [pref_backend] + [b for b in candidates if b != pref_backend]

        return candidates

    def extract_thought_content(self, text: str) -> tuple[str, str]:
        """Extract reasoning from <think>, <thinking>, <thought>, or <reasoning> tags, returning (cleaned_text, thought_content)."""
        if not text or not isinstance(text, str):
            return "", ""
        match = re.search(r"<(?:think|thinking|thought|reasoning)>(.*?)</(?:think|thinking|thought|reasoning)>", text, re.DOTALL)
        if match:
            thought_content = match.group(1).strip()
            cleaned_text = re.sub(r"<(?:think|thinking|thought|reasoning)>.*?</(?:think|thinking|thought|reasoning)>", "", text, flags=re.DOTALL).strip()
            return cleaned_text, thought_content
        return text, ""

    @classmethod
    def extract_embedded_tool_calls(cls, text: str) -> tuple[str, list[dict[str, Any]]]:
        """
        Extract tool calls embedded in LLM text output (e.g. <tool_code>, <tool_call>, function calls).
        Returns (cleaned_text, tool_calls_list).
        """
        if not text or not isinstance(text, str):
            return "", []

        tool_calls: list[dict[str, Any]] = []
        cleaned_text = text

        def _parse_fn_args(arg_str: str) -> dict[str, Any]:
            arg_str = arg_str.strip()
            if not arg_str:
                return {}
            # Try JSON first
            if (arg_str.startswith("{") and arg_str.endswith("}")) or (arg_str.startswith("[") and arg_str.endswith("]")):
                try:
                    res = json.loads(arg_str)
                    if isinstance(res, dict):
                        return res
                except Exception:
                    pass
            # Try Python AST kwargs
            try:
                parsed = _ast.parse(f"dummy({arg_str})")
                call_node = parsed.body[0].value
                args_dict = {}
                for kw in getattr(call_node, "keywords", []):
                    if kw.arg:
                        try:
                            args_dict[kw.arg] = _ast.literal_eval(kw.value)
                        except Exception:
                            val_str = arg_str[kw.value.col_offset:kw.value.end_col_offset] if hasattr(kw.value, "col_offset") else str(kw.value)
                            args_dict[kw.arg] = re.sub(r"^['\"]|['\"]$", "", val_str)
                if not args_dict and getattr(call_node, "args", []):
                    try:
                        args_dict["instruction"] = _ast.literal_eval(call_node.args[0])
                    except Exception:
                        pass
                if args_dict:
                    return args_dict
            except Exception:
                pass

            # Regex key=value fallback: key="val" or key='val' or key=val
            kv_matches = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^,\s\)]+))', arg_str)
            if kv_matches:
                res = {}
                for k, v1, v2, v3 in kv_matches:
                    val = v1 if v1 != "" else (v2 if v2 != "" else v3)
                    res[k] = val
                return res

            # XML tag parsing: <key>value</key>
            if "<" in arg_str and ">" in arg_str:
                xml_args = {}
                for tag_m in re.finditer(r"<([a-zA-Z0-9_-]+)>(.*?)</\1>", arg_str, flags=re.DOTALL):
                    k = tag_m.group(1).strip()
                    v = tag_m.group(2).strip()
                    if v.isdigit():
                        xml_args[k] = int(v)
                    elif v.replace(".", "", 1).isdigit():
                        xml_args[k] = float(v)
                    elif v.lower() in ("true", "false"):
                        xml_args[k] = (v.lower() == "true")
                    else:
                        xml_args[k] = v
                if xml_args:
                    return xml_args

            return {"instruction": arg_str}

        # 1. Match <tool_code> ... </tool_code>
        for m in re.finditer(r"<tool_code>(.*?)</tool_code>", cleaned_text, flags=re.DOTALL | re.IGNORECASE):
            raw_block = m.group(1).strip()
            # Support multiple lines or statements inside <tool_code>
            lines = [ln.strip() for ln in raw_block.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            matched_any = False
            for line in lines:
                # Unwrap print(...) if present
                print_m = re.match(r"^print\s*\((.*)\)$", line, flags=re.DOTALL)
                target_expr = print_m.group(1).strip() if print_m else line

                fn_match = re.match(r"^([a-zA-Z0-9_\.]+)\s*\((.*)\)$", target_expr, flags=re.DOTALL)
                if fn_match:
                    tool_name = fn_match.group(1).strip()
                    args = _parse_fn_args(fn_match.group(2).strip())
                    tool_calls.append({
                        "id": f"call_code_{int(time.time() * 1000)}_{len(tool_calls)}",
                        "name": tool_name,
                        "arguments": json.dumps(args),
                    })
                    matched_any = True

            if not matched_any:
                try:
                    js = json.loads(raw_block)
                    if isinstance(js, dict):
                        tool_name = js.get("name") or js.get("tool") or js.get("action") or "call_unknown"
                        args = js.get("arguments") or js.get("parameters") or js
                        tool_calls.append({
                            "id": f"call_code_{int(time.time() * 1000)}_{len(tool_calls)}",
                            "name": str(tool_name),
                            "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                        })
                except Exception:
                    pass

        cleaned_text = re.sub(r"<tool_code>.*?</tool_code>", "", cleaned_text, flags=re.DOTALL | re.IGNORECASE)

        # 2. Match <tool_call> ... </tool_call>
        for m in re.finditer(r"<tool_call>(.*?)</tool_call>", cleaned_text, flags=re.DOTALL | re.IGNORECASE):
            raw_block = m.group(1).strip()
            fn_match = re.match(r"^([a-zA-Z0-9_-]+)\s*\((.*)\)$", raw_block, flags=re.DOTALL)
            if fn_match:
                tool_name = fn_match.group(1).strip()
                args = _parse_fn_args(fn_match.group(2).strip())
                tool_calls.append({
                    "id": f"call_tag_{int(time.time() * 1000)}_{len(tool_calls)}",
                    "name": tool_name,
                    "arguments": json.dumps(args),
                })
            else:
                try:
                    js = json.loads(raw_block)
                    if isinstance(js, dict):
                        tool_name = js.get("name") or js.get("tool") or js.get("action") or "call_unknown"
                        args = js.get("arguments") or js.get("parameters") or js
                        tool_calls.append({
                            "id": f"call_tag_{int(time.time() * 1000)}_{len(tool_calls)}",
                            "name": str(tool_name),
                            "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                        })
                except Exception:
                    pass

        cleaned_text = re.sub(r"<tool_call>.*?</tool_call>", "", cleaned_text, flags=re.DOTALL | re.IGNORECASE)

        # 3. Match Qwen-style XML: <tool_name>(.*?)</tool_name>\s*<tool_args>(.*?)</tool_args>
        for m in re.finditer(r"<tool_name>([a-zA-Z0-9_-]+)</tool_name>\s*<tool_args>(.*?)</tool_args>", cleaned_text, flags=re.DOTALL | re.IGNORECASE):
            tool_name = m.group(1).strip()
            args = _parse_fn_args(m.group(2).strip())
            tool_calls.append({
                "id": f"call_xml_{int(time.time() * 1000)}_{len(tool_calls)}",
                "name": tool_name,
                "arguments": json.dumps(args),
            })
        cleaned_text = re.sub(r"</?(?:tool|invoke|tool_name|tool_args)[^>]*>.*?</?(?:tool|invoke|tool_name|tool_args)>", "", cleaned_text, flags=re.DOTALL | re.IGNORECASE)
        cleaned_text = re.sub(r"</?(?:tool|invoke|tool_name|tool_args)[^>]*>", "", cleaned_text, flags=re.IGNORECASE)

        # 4. Match raw standalone call_<agent>_agent(...) if no tools found yet
        if not tool_calls:
            for m in re.finditer(r"\b(call_[a-zA-Z0-9_]+_agent)\s*\((.*?)\)", cleaned_text, flags=re.DOTALL):
                tool_name = m.group(1).strip()
                args = _parse_fn_args(m.group(2).strip())
                tool_calls.append({
                    "id": f"call_inline_{int(time.time() * 1000)}_{len(tool_calls)}",
                    "name": tool_name,
                    "arguments": json.dumps(args),
                })
                cleaned_text = cleaned_text.replace(m.group(0), "")

        return cleaned_text.strip(), tool_calls

    def _sanitize_response(self, text: str, preserve_thought_marker: bool = False) -> str:
        """
        Strip leaked API keys, truncate hallucinated multi-turn role continuations,
        and clean model-specific stop/control tokens.
        """
        if not text or not isinstance(text, str):
            return ""

        sanitized = text

        # 1. Handle reasoning / thinking tags (<think> or <thinking>)
        cleaned, thought_content = self.extract_thought_content(sanitized)
        if preserve_thought_marker and thought_content:
            marker = f"[Agent previous reasoning: {thought_content[:200]}...]"
            sanitized = re.sub(r"<(?:think|thinking)>.*?</(?:think|thinking)>", marker, sanitized, flags=re.DOTALL)
        else:
            sanitized = re.sub(r"<(?:think|thinking)>.*?</(?:think|thinking)>", "", sanitized, flags=re.DOTALL)
            sanitized = re.sub(r"<(?:think|thinking)>.*$", "", sanitized, flags=re.DOTALL)
            sanitized = re.sub(r"^.*?</(?:think|thinking)>", "", sanitized, flags=re.DOTALL)

        # 2. Exact Active Key Redaction (Dynamic)
        for profile in self.backends.values():
            for k in profile.api_keys:
                if k and len(k) > 6 and k in sanitized:
                    sanitized = sanitized.replace(k, "[API_KEY_REDACTED]")
            if profile.api_key and len(profile.api_key) > 6 and profile.api_key in sanitized:
                sanitized = sanitized.replace(profile.api_key, "[API_KEY_REDACTED]")

        # 3. Regex Patterns Redaction
        for pattern in API_KEY_PATTERNS:
            if pattern.search(sanitized):
                sanitized = pattern.sub("[API_KEY_REDACTED]", sanitized)

        # 4. Truncate at hallucinated conversation turn boundaries
        turn_delimiters = [
            r"<\|\s*(?:im_start|im_end|user|human|system|assistant)\b",
            r"(?:\n|\A)(?:User|Human|Assistant|System):\s*",
            r"<\|\s*$",
        ]
        for pattern in turn_delimiters:
            parts = re.split(pattern, sanitized, flags=re.IGNORECASE)
            if len(parts) > 1:
                sanitized = parts[0]

        # 5. Strip model stop/control tokens
        for token in _STOP_TOKENS:
            if token in sanitized:
                sanitized = sanitized.replace(token, "")

        sanitized = re.sub(r"<\|\s*", "", sanitized)
        sanitized = re.sub(r"\s*\|>", "", sanitized)

        # 6. Collapse runs of blank lines & repetitive phrase loops
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        # Remove ALL internal tool-dispatch markers & XML tags (never user-facing content)
        sanitized = re.sub(r'<tool_code>.*?</tool_code>', '', sanitized, flags=re.DOTALL | re.IGNORECASE)
        sanitized = re.sub(r'<tool_call>.*?</tool_call>', '', sanitized, flags=re.DOTALL | re.IGNORECASE)
        sanitized = re.sub(r'</?(?:tool_call|function|parameter)[^>]*>', '', sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r'\[?[a-zA-Z0-9_-]+: calling tools:?[^\]\n]*\]?\s*', '', sanitized)
        sanitized = re.sub(r'⚙\s*tools dispatched[^\n]*', '', sanitized)
        # Agent step/thinking status artifacts echoed by the model
        # ([browser: thinking], [browser: step 3: ...], [system: ...], [media]: ...)
        sanitized = re.sub(
            r'\[[a-z][a-z0-9_-]*\s*:\s*(?:thinking|step\s[^\]]*)\]\s*',
            '', sanitized, flags=re.IGNORECASE,
        )
        sanitized = re.sub(r'(\b[^\n]{5,40}\b\s*)\1{4,}', r'\1', sanitized)

        return sanitized.strip()

    def sanitize_stream_delta(self, delta: str) -> str:
        """Sanitize an individual streaming delta token WITHOUT stripping whitespace."""
        if not delta or not isinstance(delta, str):
            return ""
        for token in _STOP_TOKENS:
            if token in delta:
                delta = delta.replace(token, "")
        return delta

    sanitize_response = _sanitize_response

    def try_parse_json(self, text: str) -> Optional[dict]:
        """
        Resilient multi-pass JSON parser.
        Extracts valid JSON from markdown fences, single-quotes, and prose wrappers.
        """
        if not text:
            return None

        # Strip reasoning tags before parsing JSON
        text, _ = self.extract_thought_content(text)

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
                    lines = lines[1:]
                    if lines and lines[-1].strip().startswith(fence[:3]):
                        lines = lines[:-1]
                    s = "\n".join(lines).strip()
                    break
            return s

        def _extract_all_balanced(s: str, open_ch: str, close_ch: str) -> list[str]:
            results = []
            idx = 0
            while idx < len(s):
                start = s.find(open_ch, idx)
                if start == -1:
                    break
                depth = 0
                in_string = False
                escape_next = False
                matched = False
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
                            results.append(s[start:i + 1])
                            idx = i + 1
                            matched = True
                            break
                if not matched:
                    idx = start + 1
            return results

        def _coerce(s: str) -> str:
            s = re.sub(r",\s*([}\]])", r"\1", s)
            try:
                return json.dumps(_ast.literal_eval(s))
            except Exception:
                return s

        # 1. Raw parse
        r = _loads(text)
        if r is not None:
            return r

        # 2. Extract content between markdown code fences ```json ... ```
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE):
            candidate = match.group(1).strip()
            r = _loads(candidate) or _loads(_coerce(candidate))
            if r is not None:
                return r

        # 3. Strip outer fences
        stripped = _strip_fences(text)
        r = _loads(stripped)
        if r is not None:
            return r

        # 4. Extract balanced objects and arrays (testing newest/deepest blocks first)
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            candidates = _extract_all_balanced(stripped, open_ch, close_ch)
            for candidate in reversed(candidates):
                r = _loads(candidate)
                if r is not None:
                    return r
                r = _loads(_coerce(candidate))
                if r is not None:
                    return r

        # 5. Coerce full stripped text
        r = _loads(_coerce(stripped))
        if r is not None:
            return r

        # 6. ast.literal_eval fallback
        try:
            r = _ast.literal_eval(stripped)
            if isinstance(r, (dict, list)):
                return r
        except (ValueError, SyntaxError):
            pass

        return None

    def _clean_messages(self, messages: list[dict]) -> list[dict]:
        """Sanitize message roles, content types, and strip control tokens."""
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
                elif isinstance(content, str):
                    clean_text = content
                    for tok in _STOP_TOKENS:
                        if tok in clean_text:
                            clean_text = clean_text.replace(tok, "")
                    cleaned_msg["content"] = clean_text
                elif not isinstance(content, list):
                    cleaned_msg["content"] = str(content)

            if "tool_calls" in cleaned_msg:
                valid_tcs = []
                for tc in cleaned_msg["tool_calls"]:
                    if isinstance(tc, dict):
                        fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                        name = fn.get("name") or tc.get("name")
                        if name and str(name).strip():
                            valid_tcs.append({
                                "id": tc.get("id") or f"call_{int(time.time()*1000)}",
                                "type": "function",
                                "function": {
                                    "name": str(name).strip(),
                                    "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str) else json.dumps(fn.get("arguments") or {}),
                                }
                            })
                if valid_tcs:
                    cleaned_msg["tool_calls"] = valid_tcs
                else:
                    cleaned_msg.pop("tool_calls", None)

            cleaned.append(cleaned_msg)
        return cleaned

    clean_messages = _clean_messages

    async def probe_tool_support(self, timeout_s: float = 3.0) -> dict[str, bool]:
        """Boot-time health contract: verify each OpenAI-compatible backend
        actually accepts a tool schema instead of assuming supports_tools=True.

        Only flips supports_tools=False on a 400-class rejection that explicitly
        references tools/function-calling. Network errors and quota/rate-limit
        responses leave the configured value untouched.
        """
        import httpx

        probe_tools = [{
            "type": "function",
            "function": {
                "name": "makima_health_probe",
                "description": "No-op connectivity probe.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }]
        results: dict[str, bool] = {}

        async def _probe_one(name: str, profile: BackendProfile) -> None:
            if not profile.enabled or profile.adapter_type != "openai" or not profile.supports_tools:
                return
            if not (profile.api_key or profile.api_keys) or not profile.model:
                return
            base_url = profile.base_url or self.get_default_base_url(name)
            if not base_url:
                return
            try:
                client = self._get_http_client()
                resp = await asyncio.wait_for(
                    client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {profile.get_api_key()}", "Content-Type": "application/json"},
                        json={
                            "model": profile.model,
                            "messages": [{"role": "user", "content": "Reply with the word ok."}],
                            "max_tokens": 1,
                            "tools": probe_tools,
                        },
                        timeout=timeout_s,
                    ),
                    timeout=timeout_s,
                )
                text = (resp.text or "").lower()
                tool_rejected = resp.status_code in (400, 404, 422) and ("tool" in text or "function" in text)
                if resp.status_code == 200:
                    logger.info("[%s] Boot probe: tool schema accepted (HTTP 200).", name)
                elif tool_rejected:
                    profile.supports_tools = False
                    logger.error("[%s] Boot probe: backend REJECTED tool schema (HTTP %d). Tools disabled for this backend.", name, resp.status_code)
                elif resp.status_code in (401, 403):
                    profile.circuit_breaker.record_failure()
                    logger.warning("[%s] Boot probe: authentication/permission error (HTTP %d). Check API key.", name, resp.status_code)
                elif resp.status_code in (402, 404):
                    profile.circuit_breaker.record_failure()
                    logger.warning("[%s] Boot probe: model or quota error (HTTP %d): %s", name, resp.status_code, (resp.text or "")[:120])
                else:
                    logger.warning("[%s] Boot probe: unexpected status (HTTP %d).", name, resp.status_code)
            except Exception as e:
                logger.warning("[%s] Boot probe failed (%s); keeping configured supports_tools=True.", name, e)
            finally:
                results[name] = profile.supports_tools

        await asyncio.gather(*(_probe_one(n, p) for n, p in list(self.backends.items())), return_exceptions=True)
        return results

    async def _call_backend(
        self,
        backend_name: str,
        messages: list[dict],
        task: str = "general",
        require_json: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """Execute request against specified backend via its registered adapter."""
        client = self._get_http_client()
        profile = self.backends[backend_name]
        adapter = self.adapters.get(profile.adapter_type) or self.adapters["openai"]

        # Multi-user isolation: Inject per-request client credentials and model overrides ONLY if targeting this provider
        req_ctx = {}
        try:
            from .core.orchestration_engine import client_request_context
            req_ctx = client_request_context.get({}) or {}
        except Exception:
            pass
        merged_kwargs = dict(kwargs)
        req_provider = req_ctx.get("provider") or kwargs.get("provider")
        target_canonical = self._resolve_backend_name(req_provider) if req_provider else None
        is_target = not req_provider or (target_canonical == backend_name or req_provider == backend_name)
        if is_target:
            if "model" not in merged_kwargs and req_ctx.get("model"):
                merged_kwargs["model"] = req_ctx["model"]
            if "api_key" not in merged_kwargs and req_ctx.get("api_key"):
                merged_kwargs["api_key"] = req_ctx["api_key"]
            if "base_url" not in merged_kwargs and req_ctx.get("base_url"):
                merged_kwargs["base_url"] = req_ctx["base_url"]
        else:
            merged_kwargs.pop("model", None)
            merged_kwargs.pop("api_key", None)
            merged_kwargs.pop("base_url", None)

        return await adapter.generate(
            profile, messages, client, self, task=task, require_json=require_json, **merged_kwargs
        )

    async def generate(
        self,
        messages: list[dict],
        task: str = "general",
        require_json: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Generate a completion using Dynamic Pareto Routing across registered Provider Adapters.
        Applies Stanford FrugalGPT Quality Cascading on malformed structured JSON.
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        constraints = {
            "require_json": require_json,
            "require_tools": bool(kwargs.get("tools")),
            "min_context": kwargs.get("min_context", 0),
        }
        backend_order = self._get_backend_order(task, constraints=constraints, **kwargs)
        errors: list[str] = []
        current_messages = list(messages)

        # Adaptive task timeouts: Fast intent/routing (15s), Long-form research/code/synthesis (120s), General (60s)
        if task in ("intent_classification", "routing", "entity_extraction", "query_rewriting"):
            default_per_backend_timeout = 30.0
        elif task in ("research", "analysis", "synthesis", "code", "debugging", "refactoring"):
            default_per_backend_timeout = 120.0
        else:
            default_per_backend_timeout = 60.0
        per_backend_timeout = float(kwargs.get("per_backend_timeout", default_per_backend_timeout))

        req_ctx: dict[str, Any] = {}
        try:
            from .core.orchestration_engine import client_request_context
            req_ctx = client_request_context.get({}) or {}
        except Exception:
            pass
        req_provider = req_ctx.get("provider") or kwargs.get("provider")
        target_canonical = self._resolve_backend_name(req_provider) if req_provider else None

        for i, backend_name in enumerate(backend_order):
            if backend_name not in self.backends:
                continue

            profile = self.backends[backend_name]
            is_target = bool(target_canonical and (backend_name == target_canonical or backend_name == req_provider))
            req_api_key = (req_ctx.get("api_key") or kwargs.get("api_key")) if is_target else None

            if not profile.is_available(client_api_key=req_api_key):
                continue

            # Check rate limiter
            estimated_tokens = min(kwargs.get("max_tokens", 500), 1000)
            if self.rate_limit_manager and not self.rate_limit_manager.can_send(backend_name, estimated_tokens):
                logger.debug("Skipping %s: rate limit would be exceeded", backend_name)
                continue

            try:
                _t0 = time.monotonic()
                response = await asyncio.wait_for(
                    self._call_backend(
                        backend_name, current_messages, task=task, require_json=require_json, **kwargs
                    ),
                    timeout=per_backend_timeout,
                )
                response.latency_ms = (time.monotonic() - _t0) * 1000.0
                self._record_latency(backend_name, response.latency_ms)
                profile.circuit_breaker.record_success()

                if i > 0:
                    response.is_fallback = True
                    logger.info("Used fallback backend: %s (attempt %d)", backend_name, i + 1)

                # Dynamic secret stripping & response sanitization
                response.text = self._sanitize_response(response.text)

                if response.tools_stripped and self.ws_broadcast:
                    try:
                        from . import ws_protocol
                        await self.ws_broadcast(ws_protocol.WSMessage(
                            v=ws_protocol.PROTOCOL_VERSION,
                            type=ws_protocol.ServerMessageType.TOOLS_DEGRADED,
                            payload={
                                "backend": response.backend,
                                "detail": "Tool schemas were stripped after repeated validation errors; replies may lack tool calls until the conversation resets.",
                            },
                        ))
                    except Exception:
                        pass

                # SOTA FrugalGPT Quality Cascading
                if require_json:
                    parsed = self.try_parse_json(response.text)
                    if parsed is None:
                        remaining = [b for b in backend_order[i+1:] if b in self.backends]
                        if remaining:
                            logger.warning("[%s] Malformed JSON generated. Escalating to %s...", backend_name, remaining[0])
                            current_messages = list(messages) + [
                                {"role": "assistant", "content": response.text},
                                {"role": "user", "content": "CRITICAL ERROR: Output was not valid JSON. Return ONLY raw, valid JSON matching the required schema."}
                            ]
                            errors.append(f"{backend_name}: Malformed JSON (cascaded)")
                            continue
                        elif kwargs.get("_retry_attempt", 0) < 1:
                            retry_kw = dict(kwargs)
                            retry_kw["_retry_attempt"] = 1
                            current_messages = list(messages) + [
                                {"role": "assistant", "content": response.text},
                                {"role": "user", "content": "CRITICAL ERROR: Output was not valid JSON. Return ONLY raw, valid JSON matching the required schema."}
                            ]
                            try:
                                retry_resp = await asyncio.wait_for(
                                    self._call_backend(
                                        backend_name, current_messages, task=task, require_json=require_json, **retry_kw
                                    ),
                                    timeout=30.0,
                                )
                                retry_resp.text = self._sanitize_response(retry_resp.text)
                                return retry_resp
                            except Exception:
                                pass

                return response

            except RateLimitError as e:
                profile.circuit_breaker.record_failure()
                errors.append(f"{backend_name}: {e}")
                logger.warning("[%s] Rate limited: %s â€” falling over to next backend", backend_name, e)
                continue
            except Exception as e:
                profile.circuit_breaker.record_failure()
                errors.append(f"{backend_name}: {type(e).__name__}: {e}")
                logger.warning("Backend %s failed during generate: %s: %s, falling back...", backend_name, type(e).__name__, e, exc_info=True)
                continue

        # All backends failed
        error_summary = "; ".join(errors) if errors else "No backends configured with API keys."
        logger.warning("ALL BACKENDS DOWN / UNCONFIGURED: %s", error_summary)

        if self.ws_broadcast:
            try:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.WSMessage(
                    v=ws_protocol.PROTOCOL_VERSION,
                    type=ws_protocol.ServerMessageType.ALL_BACKENDS_DOWN,
                    payload={"retry_in_s": 30},
                ))
            except Exception:
                pass

        return LLMResponse(
            text="âš ï¸ **No AI Model Backend Available**\n\nI need an API key to generate answers. Please go to the **Settings** page in the UI and enter your API key (Gemini, Groq, OpenRouter, or GPT-4o), or start **Ollama** locally.",
            backend="fallback",
            model="none",
            is_fallback=True,
        )

    async def chat_complete(self, messages: list[dict], task: str = "general", **kwargs: Any) -> str:
        """Helper method for simple chat completion returning text string."""
        res = await self.generate(messages, task=task, **kwargs)
        return res.text

    async def generate_stream_events(
        self,
        messages: list[dict],
        task: str = "general",
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream structured completion events (text_delta, tool_call_delta) using Provider Strategy Adapters."""
        # Multi-user isolation: Extract per-request client credentials and model overrides
        req_ctx: dict[str, Any] = {}
        try:
            from .core.orchestration_engine import client_request_context
            req_ctx = client_request_context.get({}) or {}
        except Exception:
            pass

        req_provider = req_ctx.get("provider") or kwargs.get("provider")
        target_canonical = self._resolve_backend_name(req_provider) if req_provider else None

        client = self._get_http_client()

        constraints = {
            "require_tools": bool(kwargs.get("tools")),
            "min_context": kwargs.get("min_context", 0),
        }
        backend_order = self._get_backend_order(task, constraints=constraints, **kwargs)
        last_error = None

        for backend_name in backend_order:
            if backend_name not in self.backends:
                continue
            profile = self.backends[backend_name]
            is_target = bool(target_canonical and (backend_name == target_canonical or backend_name == req_provider))
            req_api_key = (req_ctx.get("api_key") or kwargs.get("api_key")) if is_target else None

            if not profile.is_available(client_api_key=req_api_key):
                continue

            estimated_tokens = kwargs.get("max_tokens", 4096)
            if self.rate_limit_manager and not self.rate_limit_manager.can_send(backend_name, estimated_tokens):
                continue

            adapter = self.adapters.get(profile.adapter_type) or self.adapters["openai"]
            events_yielded = False

            call_kwargs = dict(kwargs)
            if is_target:
                if "model" not in call_kwargs and req_ctx.get("model"):
                    call_kwargs["model"] = req_ctx["model"]
                if "api_key" not in call_kwargs and req_api_key:
                    call_kwargs["api_key"] = req_api_key
                if "base_url" not in call_kwargs and req_ctx.get("base_url"):
                    call_kwargs["base_url"] = req_ctx["base_url"]
            else:
                call_kwargs.pop("model", None)
                call_kwargs.pop("api_key", None)
                call_kwargs.pop("base_url", None)

            try:
                stream_ev_fn = getattr(adapter, "stream_events", None)
                if callable(stream_ev_fn):
                    async for ev in stream_ev_fn(profile, messages, client, self, task=task, **call_kwargs):
                        events_yielded = True
                        yield ev
                else:
                    async for chunk in adapter.stream(profile, messages, client, self, task=task, **call_kwargs):
                        events_yielded = True
                        yield {"type": "text_delta", "text": chunk}
                if events_yielded:
                    return
            except RateLimitError as e:
                last_error = str(e)
                if events_yielded:
                    raise
                continue
            except Exception as e:
                last_error = str(e)
                if events_yielded:
                    logger.warning("Backend %s failed mid-stream-events: %s", backend_name, e)
                    raise
                continue

        # Fallback to non-streaming generate
        logger.warning("generate_stream_events failed on all backends (%s), using non-streaming fallback", last_error)
        response = await self.generate(messages, task=task, **kwargs)
        text = self._sanitize_response(response.text)
        if text:
            chunk_size = 32
            for i in range(0, len(text), chunk_size):
                yield {"type": "text_delta", "text": text[i:i+chunk_size]}
        for idx, tc in enumerate(getattr(response, "tool_calls", []) or []):
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            name = fn.get("name") or tc.get("name") or ""
            args = fn.get("arguments") if isinstance(fn.get("arguments"), str) else json.dumps(fn.get("arguments") or {})
            yield {
                "type": "tool_call_delta",
                "index": idx,
                "id": tc.get("id") or f"call_{idx}",
                "name": name,
                "arguments_delta": args,
            }

    async def generate_stream(
        self,
        messages: list[dict],
        task: str = "general",
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream a completion token-by-token using Provider Strategy Adapters."""
        async for ev in self.generate_stream_events(messages, task=task, **kwargs):
            if ev.get("type") == "text_delta" and ev.get("text"):
                yield ev["text"]

    stream_chat = generate_stream
    stream = generate_stream

    def get_backend_status(self) -> dict[str, dict]:
        """Get status of all backends for health dashboard."""
        status = {}
        for name, backend in self.backends.items():
            status[name] = {
                "enabled": backend.enabled,
                "model": backend.model,
                "adapter_type": backend.adapter_type,
                "latency_tier": backend.latency_tier,
                "ewma_latency_ms": round(backend.ewma_latency_ms, 2),
                "circuit_breaker_state": backend.circuit_breaker.state,
                "fail_count": backend.circuit_breaker.fail_count,
                "has_api_key": bool(backend.api_key) or bool(backend.api_keys),
                "context_limit": backend.context_limit,
            }
        return status


# Alias for SOTA nomenclature
NexusLLMGateway = AIHandler
