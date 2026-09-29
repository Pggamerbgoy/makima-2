"""
Makima v2 — Abstract Base Provider Contract.
Defines canonical Message, ToolCall, and ProviderResponse structures
bridging all LLM wire protocols (Google GenAI, OpenAI, Groq, Ollama) into a unified contract.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

from pydantic import BaseModel, Field

from makima.config import BackendConfig
from makima.provider.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: str  # "system", "user", "assistant", "tool"
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class StreamChunk(BaseModel):
    delta: str = ""
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None


class ProviderResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: Optional[Dict[str, int]] = None
    model: str = ""
    latency_ms: float = 0.0
    raw: Any = None


class BaseProvider(ABC):
    """
    Unified abstract provider adapter interface.
    All underlying LLM implementations (Gemini, Groq, Ollama) must conform to this contract.
    """

    def __init__(
        self,
        config: BackendConfig,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.config = config
        self.name = config.model
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name=config.model)

    @property
    def is_available(self) -> bool:
        """Evaluates whether the backend has credentials and circuit breaker is healthy."""
        return self.config.is_available and self.circuit_breaker.is_available

    async def _before_call(self) -> None:
        """Pre-call guard checking circuit breaker."""
        if not await self.circuit_breaker.can_execute():
            retry_after = self.circuit_breaker.get_retry_after()
            raise CircuitBreakerOpenError(self.name, retry_after)

    async def _on_success(self) -> None:
        """Post-call hook registering success with circuit breaker."""
        await self.circuit_breaker.record_success()

    async def _on_failure(self, error: Exception) -> None:
        """Post-call hook registering failure with circuit breaker."""
        await self.circuit_breaker.record_failure(error)

    @abstractmethod
    async def generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Executes a non-streaming completion call against the provider.
        """
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """
        Executes a real-time streaming completion call against the provider.
        """
        pass
