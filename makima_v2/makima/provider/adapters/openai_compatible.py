"""
Makima v2 — OpenAI-Compatible Provider Adapter.
Drives Groq (Free tier Llama 3.3 70B), OpenRouter (Free models), and local Ollama (100% offline).
Zero-Cost Guarantee: Zero mandatory paid keys. Operates natively with free tiers and local daemon.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from makima.config import BackendConfig
from makima.provider.adapters.base import (
    BaseProvider,
    Message,
    ProviderResponse,
    StreamChunk,
    ToolCall,
)
from makima.provider.circuit_breaker import CircuitBreaker


class OpenAICompatibleProvider(BaseProvider):
    """
    Adapter for standard OpenAI Chat Completions API format.
    Fully compatible with Groq, OpenRouter, Cerebras, and Ollama localhost.
    """

    def __init__(
        self,
        config: BackendConfig,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        super().__init__(config=config, circuit_breaker=circuit_breaker)
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        """Initializes AsyncOpenAI client with target base_url and API key."""
        if self._client is None:
            # Ollama does not require real API key; default to dummy string
            api_key = self.config.api_key or ("ollama" if self.config.adapter == "ollama" else None)
            if not api_key:
                raise ValueError(
                    f"Missing API key for provider '{self.name}'. "
                    f"Please configure '{self.config.api_key_env}' in environment or .env."
                )

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.config.base_url,
                timeout=60.0,
            )
        return self._client

    def _format_messages(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Formats canonical messages into OpenAI chat payload format."""
        formatted: List[Dict[str, Any]] = []

        if system_prompt:
            formatted.append({"role": "system", "content": system_prompt})

        for msg in messages:
            entry: Dict[str, Any] = {
                "role": msg.role,
                "content": msg.content or "",
            }

            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id

            if msg.name:
                entry["name"] = msg.name

            formatted.append(entry)

        return formatted

    def _format_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """Ensures tools comply with standard OpenAI function schema structure."""
        if not tools:
            return None

        formatted_tools = []
        for t in tools:
            if "type" in t and "function" in t:
                formatted_tools.append(t)
            elif "name" in t:
                formatted_tools.append({
                    "type": "function",
                    "function": t,
                })
            else:
                formatted_tools.append(t)
        return formatted_tools

    async def generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ProviderResponse:
        await self._before_call()
        start_time = time.monotonic()

        try:
            client = self._get_client()
            formatted_messages = self._format_messages(messages, system_prompt=system_prompt)
            formatted_tools = self._format_tools(tools)

            call_kwargs: Dict[str, Any] = {
                "model": self.config.model,
                "messages": formatted_messages,
                "temperature": temperature,
            }
            if max_tokens:
                call_kwargs["max_tokens"] = max_tokens
            if formatted_tools:
                call_kwargs["tools"] = formatted_tools

            response = await client.chat.completions.create(**call_kwargs)
            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            await self._on_success()

            choice = response.choices[0]
            content = choice.message.content
            tool_calls: List[ToolCall] = []

            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    args: Dict[str, Any] = {}
                    if tc.function.arguments:
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {"raw_arguments": tc.function.arguments}
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        )
                    )

            usage = None
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            return ProviderResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason or "stop",
                usage=usage,
                model=self.config.model,
                latency_ms=elapsed_ms,
                raw=response,
            )

        except Exception as e:
            await self._on_failure(e)
            raise

    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        await self._before_call()
        try:
            client = self._get_client()
            formatted_messages = self._format_messages(messages, system_prompt=system_prompt)
            formatted_tools = self._format_tools(tools)

            call_kwargs: Dict[str, Any] = {
                "model": self.config.model,
                "messages": formatted_messages,
                "temperature": temperature,
                "stream": True,
            }
            if max_tokens:
                call_kwargs["max_tokens"] = max_tokens
            if formatted_tools:
                call_kwargs["tools"] = formatted_tools

            stream_resp = await client.chat.completions.create(**call_kwargs)

            async for chunk in stream_resp:
                if not chunk.choices:
                    continue
                c = chunk.choices[0]
                delta_text = c.delta.content or ""
                finish_reason = c.finish_reason

                yield StreamChunk(
                    delta=delta_text,
                    finish_reason=finish_reason,
                )

            await self._on_success()

        except Exception as e:
            await self._on_failure(e)
            raise
