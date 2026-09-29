"""
Makima v2 — Google GenAI SDK Adapter.
Architectural Pattern: Official google-genai SDK with streaming, function calling, and free-tier optimization.
Zero-Hardcoding Guarantee: Dynamically reads GEMINI_API_KEY and backend config.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from google import genai
from google.genai import types

from makima.config import BackendConfig
from makima.provider.adapters.base import (
    BaseProvider,
    Message,
    ProviderResponse,
    StreamChunk,
    ToolCall,
)
from makima.provider.circuit_breaker import CircuitBreaker


class GeminiProvider(BaseProvider):
    """
    Adapter for Google Gemini models (e.g. gemini-2.5-flash) using official google-genai SDK.
    Optimized for high-speed free tier quotas on Google AI Studio.
    """

    def __init__(
        self,
        config: BackendConfig,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        super().__init__(config=config, circuit_breaker=circuit_breaker)
        self._client: Optional[genai.Client] = None

    def _get_client(self) -> genai.Client:
        """Lazy initialization of genai.Client with active API key."""
        api_key = self.config.api_key
        if not api_key:
            raise ValueError(
                f"Missing API key for Gemini backend '{self.name}'. "
                f"Ensure environment variable '{self.config.api_key_env or 'GEMINI_API_KEY'}' is set."
            )
        if self._client is None:
            self._client = genai.Client(api_key=api_key)
        return self._client

    def _convert_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[types.Tool]]:
        """Converts standard JSON schema tool definitions into Gemini Tool format."""
        if not tools:
            return None

        declarations = []
        for t in tools:
            fn = t.get("function", t)
            name = fn.get("name")
            description = fn.get("description", "")
            parameters = fn.get("parameters", {})
            if name:
                declarations.append(
                    types.FunctionDeclaration(
                        name=name,
                        description=description,
                        parameters=parameters,
                    )
                )

        if not declarations:
            return None
        return [types.Tool(function_declarations=declarations)]

    def _convert_messages(self, messages: List[Message]) -> List[types.Content]:
        """Translates canonical Message list into Gemini Content objects."""
        contents: List[types.Content] = []

        for msg in messages:
            role = "user" if msg.role in ("user", "system") else "model"
            parts: List[types.Part] = []

            if msg.content:
                parts.append(types.Part.from_text(text=msg.content))

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append(
                        types.Part.from_function_call(
                            name=tc.name,
                            args=tc.arguments,
                        )
                    )

            if msg.role == "tool":
                # Tool execution response
                parts.append(
                    types.Part.from_function_response(
                        name=msg.name or "tool_result",
                        response={"result": msg.content or ""},
                    )
                )

            if parts:
                contents.append(types.Content(role=role, parts=parts))

        return contents

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
            contents = self._convert_messages(messages)
            gemini_tools = self._convert_tools(tools)

            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
                tools=gemini_tools,
            )

            response = await client.aio.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000.0
            await self._on_success()

            # Parse content and function calls
            content_text = ""
            parsed_tool_calls: List[ToolCall] = []

            if response.candidates:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            content_text += part.text
                        if part.function_call:
                            fn_call = part.function_call
                            call_id = f"call_{uuid.uuid4().hex[:8]}"
                            parsed_tool_calls.append(
                                ToolCall(
                                    id=call_id,
                                    name=fn_call.name,
                                    arguments=dict(fn_call.args) if fn_call.args else {},
                                )
                            )

            finish_reason = "stop"
            if parsed_tool_calls:
                finish_reason = "tool_calls"

            usage = None
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = {
                    "prompt_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
                    "completion_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
                    "total_tokens": getattr(response.usage_metadata, "total_token_count", 0),
                }

            return ProviderResponse(
                content=content_text or None,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
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
            contents = self._convert_messages(messages)
            gemini_tools = self._convert_tools(tools)

            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
                tools=gemini_tools,
            )

            stream = await client.aio.models.generate_content_stream(
                model=self.config.model,
                contents=contents,
                config=config,
            )

            async for chunk in stream:
                text_delta = ""
                tool_calls: List[ToolCall] = []
                if chunk.candidates:
                    c = chunk.candidates[0]
                    if c.content and c.content.parts:
                        for p in c.content.parts:
                            if p.text:
                                text_delta += p.text
                            if p.function_call:
                                fn = p.function_call
                                tool_calls.append(
                                    ToolCall(
                                        id=f"call_{uuid.uuid4().hex[:8]}",
                                        name=fn.name,
                                        arguments=dict(fn.args) if fn.args else {},
                                    )
                                )

                yield StreamChunk(
                    delta=text_delta,
                    tool_calls=tool_calls if tool_calls else None,
                )

            await self._on_success()

        except Exception as e:
            await self._on_failure(e)
            raise
