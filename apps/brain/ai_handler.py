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

    def is_available(self) -> bool:
        """Check if backend is healthy, unblocked by circuit breaker, and credentialed."""
        if not self.enabled:
            return False
        if not self.circuit_breaker.can_attempt():
            return False
        if self.adapter_type == "ollama":
            return True
        return bool(self.api_key) or bool(self.api_keys)


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
        if profile.name == "claude" and "model" not in kwargs:
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
            key = profile.get_api_key()
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
        if profile.name == "claude" and "model" not in kwargs:
            model = _resolve_openrouter_model(kwargs.get("task", "general"))

        base_url = kwargs.get("base_url") or profile.base_url or gateway.get_default_base_url(profile.name)
        headers = {
            "Authorization": f"Bearer {profile.get_api_key()}",
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
        model = kwargs.get("model") or profile.model or "gemini-3-flash-preview"
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
            "x-goog-api-key": profile.get_api_key(),
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

        model = kwargs.get("model") or profile.model or "gemini-3-flash-preview"
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
            "x-goog-api-key": profile.get_api_key(),
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
        host = profile.base_url or "http://127.0.0.1:11434"
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
        host = profile.base_url or "http://127.0.0.1:11434"
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

        # 1. Capability & Availability filtering
        valid_candidates: list[BackendProfile] = []
        for name in task_candidates:
            profile = self.profiles.get(name)
            if not profile or not profile.is_available():
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
            if profile in valid_candidates or not profile.is_available():
                continue
            if cons.get("require_tools") and not profile.supports_tools:
                continue
            if cons.get("require_json") and not profile.supports_json_mode:
                continue
            if cons.get("min_context", 0) > profile.context_limit:
                continue
            valid_candidates.append(profile)

        if not valid_candidates:
            return list(task_candidates)

        # 2. Dynamic Pareto Frontier Sorting: Task Routing Priority -> Latency Tier -> EWMA Latency -> Fail Count
        def _pareto_key(p: BackendProfile) -> tuple[int, int, float, int]:
            idx = task_candidates.index(p.name) if p.name in task_candidates else 999
            tier = self.TIER_SCORES.get(p.latency_tier, 1)
            ewma = ewma_map.get(p.name, p.ewma_latency_ms)
            lat_val = ewma if ewma > 0 else (100.0 if tier == 0 else (500.0 if tier == 1 else 2000.0))
            return (idx, tier, lat_val, p.circuit_breaker.fail_count)

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
        "claude": "https://openrouter.ai/api/v1",
        "gpt4o": "https://api.openai.com/v1",
        "cerebras": "https://api.cerebras.ai/v1",
        "nemotron_free": "https://openrouter.ai/api/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen36_flash": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen35_flash": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen_plus": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    }

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        rate_limit_manager: Any = None,
        ws_broadcast: Any = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self.backends: dict[str, BackendProfile] = {}
        self.rate_limit_manager = rate_limit_manager
        self.ws_broadcast = ws_broadcast
        self._http_client: Optional[Any] = None

        # Provider Strategy Adapters Registry
        self.adapters: dict[str, BaseProviderAdapter] = {
            "openai": OpenAICompatibleAdapter(),
            "gemini": GeminiAdapter(),
            "ollama": OllamaAdapter(),
        }

        # Canonical task routing map (Multi-provider priority cascade)
        # ORDER MATTERS: qwen_flash + deepseek_v32 are confirmed working (HTTP 200 on boot probe).
        # openrouter (402 no credits) and nemotron_free (429 rate limit) are moved to end as
        # last-resort fallbacks. This eliminates ~6s wasted on dead backends.
        self.task_routing: dict[str, list[str]] = {
            "fast_chat":             ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "intent_classification": ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "entity_extraction":     ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "routing":               ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "query_rewriting":       ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "persona_extraction":    ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "research_decomp":       ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "general":               ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "research":              ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "analysis":              ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "automation":            ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "code":                  ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "debugging":             ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "refactoring":           ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "data_analysis":         ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "browser":               ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "system_control":        ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "media":                 ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "vision":                ["gemini", "qwen_flash"],
            "creative":              ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "daily_briefing":        ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "decompose":             ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "synthesize":            ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "consolidation":         ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "devops":                ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
            "offline":               ["gemini", "qwen_flash"],
            "privacy_mode":          ["gemini", "qwen_flash"],
            "mock":                  ["mock_primary", "mock_secondary"],
            "reflexion":             ["gemini", "qwen_flash", "qwen35_plus_0420", "groq"],
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

        for name, cfg in backends_cfg.items():
            api_key_env = cfg.get("api_key_env", "")
            raw_keys = []
            env_keys = [
                (os.environ.get(api_key_env, "") if api_key_env else ""),
                os.environ.get(f"MAKIMA_{name.upper()}_KEY", ""),
                os.environ.get(f"{name.upper()}_API_KEY", ""),
            ]
            if name.startswith("groq"):
                env_keys.extend([os.environ.get("GROQ_API_KEY", ""), os.environ.get("MAKIMA_GROQ_KEY", "")])
            elif name in ("openrouter", "claude"):
                env_keys.extend([
                    os.environ.get("OPENROUTER_API_KEY", ""),
                    os.environ.get("MAKIMA_OPENROUTER_KEY", ""),
                    os.environ.get("OPENROUTER_KEY", ""),
                    os.environ.get("CLAUDE_API_KEY", ""),
                    os.environ.get("ANTHROPIC_API_KEY", ""),
                ])
            elif name.startswith("gemini"):
                env_keys.extend([
                    os.environ.get("GEMINI_API_KEY", ""),
                    os.environ.get("MAKIMA_GEMINI_KEY", ""),
                    os.environ.get("GOOGLE_API_KEY", ""),
                ])
            elif name.startswith("qwen") or name.startswith("deepseek"):
                env_keys.extend([
                    os.environ.get("DASHSCOPE_API_KEY", ""),
                    os.environ.get("QWEN_API_KEY", ""),
                    os.environ.get("MAKIMA_QWEN_KEY", ""),
                    os.environ.get("DEEPSEEK_API_KEY", ""),
                    os.environ.get("ALIYUN_API_KEY", ""),
                ])
            elif name.startswith("cerebras"):
                env_keys.extend([os.environ.get("CEREBRAS_API_KEY", ""), os.environ.get("MAKIMA_CEREBRAS_KEY", "")])
            elif name.startswith("huggingface"):
                env_keys.extend([os.environ.get("HF_TOKEN", ""), os.environ.get("HUGGINGFACE_API_KEY", ""), os.environ.get("HF_API_KEY", "")])

            for k_str in env_keys:
                if k_str:
                    raw_keys.append(k_str)
            if cfg.get("api_key"):
                raw_keys.append(cfg.get("api_key"))

            api_keys = []
            for k_str in raw_keys:
                for k in k_str.split(","):
                    k = k.strip()
                    if k and k not in api_keys:
                        api_keys.append(k)

            api_key = api_keys[0] if api_keys else ""
            if len(api_keys) <= 1:
                api_keys = []

            # Strict provider filtering: If disabled or non-Alibaba when only_alibaba is active, skip completely
            is_enabled = cfg.get("enabled", True)
            only_alibaba = bool(
                config.get("llm", {}).get("only_alibaba")
                or config.get("llm", {}).get("only_qwen")
                or os.environ.get("MAKIMA_ONLY_ALIBABA", "").lower() in ("1", "true", "yes")
                or os.environ.get("MAKIMA_ONLY_QWEN", "").lower() in ("1", "true", "yes")
            )
            is_alibaba_backend = bool(
                name.startswith("qwen")
                or name.startswith("deepseek")
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

            # Resolve latency tier
            latency_tier = cfg.get("latency_tier", "standard")
            if name in ("groq_fast", "cerebras", "groq"):
                latency_tier = "fast"
            elif name in ("gemini", "claude"):
                latency_tier = "standard"
            elif name in ("ollama", "gpt4o"):
                latency_tier = "slow"

            self.backends[name] = BackendProfile(
                name=name,
                enabled=cfg.get("enabled", True),
                adapter_type=adapter_type,
                api_key=api_key,
                api_keys=api_keys,
                model=cfg.get("model", ""),
                context_limit=cfg.get("context_limit", 128000),
                tasks=cfg.get("tasks", []),
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

            for t in cfg.get("tasks", []):
                if t in self.task_routing:
                    if name not in self.task_routing[t]:
                        self.task_routing[t].append(name)
                else:
                    self.task_routing[t] = [name]

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
    ) -> list[str]:
        """Get ordered list of backends via Dynamic Pareto Router + Per-Agent Model Routing."""
        if constraints is None:
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
            candidates = self.router.route(task=task, constraints=constraints, ewma_latencies=self._backend_ewma_latency)

        # Prioritize agent's configured model from YAML if available and healthy
        if agent_name:
            ag_clean = str(agent_name).lower().strip()
            pref_backend = (
                self.agent_models.get(ag_clean)
                or self.agent_models.get(f"{ag_clean}_agent")
                or self.agent_models.get(ag_clean.replace("_agent", ""))
            )
            if pref_backend and pref_backend in self.backends:
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
        return await adapter.generate(
            profile, messages, client, self, task=task, require_json=require_json, **kwargs
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
        backend_order = self._get_backend_order(task, constraints=constraints, agent_name=kwargs.get("agent_name"))
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

        for i, backend_name in enumerate(backend_order):
            if backend_name not in self.backends:
                continue

            profile = self.backends[backend_name]
            if not profile.is_available():
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
        client = self._get_http_client()
        constraints = {
            "require_tools": bool(kwargs.get("tools")),
            "min_context": kwargs.get("min_context", 0),
        }
        backend_order = self._get_backend_order(task, constraints=constraints, agent_name=kwargs.get("agent_name"))
        last_error = None

        for backend_name in backend_order:
            if backend_name not in self.backends:
                continue
            profile = self.backends[backend_name]
            if not profile.is_available():
                continue

            estimated_tokens = kwargs.get("max_tokens", 4096)
            if self.rate_limit_manager and not self.rate_limit_manager.can_send(backend_name, estimated_tokens):
                continue

            adapter = self.adapters.get(profile.adapter_type) or self.adapters["openai"]
            events_yielded = False

            try:
                stream_ev_fn = getattr(adapter, "stream_events", None)
                if callable(stream_ev_fn):
                    async for ev in stream_ev_fn(profile, messages, client, self, task=task, **kwargs):
                        events_yielded = True
                        yield ev
                else:
                    async for chunk in adapter.stream(profile, messages, client, self, task=task, **kwargs):
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
