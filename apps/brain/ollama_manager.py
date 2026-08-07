"""
Makima v7.1 — Ollama Manager

Manages the local Ollama instance.
ensure_running(): checks if Ollama is up, starts it if not.
list_models(): returns available local models.
pull_model(name): pulls a model with progress streaming.
delete_model(name): removes a model from local storage.
Used by AIHandler to route tasks to local models when privacy_mode=True
or when all cloud backends are down (N10 fallback).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Any, Optional

logger = logging.getLogger("makima.ollama_manager")

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_STARTUP_TIMEOUT_S = 15.0


class OllamaManager:
    """
    Manages the local Ollama process and model lifecycle.
    """

    def __init__(self, config: dict, ws_broadcast=None):
        cfg = config.get("ollama", {})
        self.base_url = cfg.get("base_url", OLLAMA_BASE_URL)
        self.auto_start = cfg.get("auto_start", True)
        self.default_model = cfg.get("default_model", "llama3.2")
        self.ws_broadcast = ws_broadcast
        self._process: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_running(self) -> bool:
        """
        Check if Ollama API is reachable. If not, start Ollama process.
        Returns True if Ollama is up after this call.
        """
        if await self._ping():
            return True

        if not self.auto_start:
            logger.warning("Ollama not running and auto_start=False.")
            return False

        logger.info("Starting Ollama...")
        try:
            self._process = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("Ollama binary not found. Install from https://ollama.com")
            return False

        # Wait for startup
        deadline = time.time() + DEFAULT_STARTUP_TIMEOUT_S
        while time.time() < deadline:
            await asyncio.sleep(1.0)
            if await self._ping():
                logger.info("Ollama started successfully.")
                return True

        logger.error(f"Ollama did not start within {DEFAULT_STARTUP_TIMEOUT_S}s")
        return False

    async def stop(self) -> None:
        """Stop the Ollama process if we started it."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            logger.info("Ollama process stopped.")

    # ------------------------------------------------------------------
    # Model Management
    # ------------------------------------------------------------------

    async def list_models(self) -> list[dict]:
        """Return list of locally available models."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 2)}
                            for m in models]
        except Exception as e:
            logger.error(f"list_models failed: {e}")
        return []

    async def pull_model(self, model_name: str) -> str:
        """
        Pull a model from Ollama registry with progress events.
        Streams pull status to WS if ws_broadcast is available.
        """
        try:
            import httpx

            logger.info(f"Pulling model: {model_name}")
            if self.ws_broadcast:
                from . import ws_protocol
                await self.ws_broadcast(ws_protocol.build_ai_chunk(
                    "ollama_pull", f"Starting download of model '{model_name}'..."
                ))

            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/pull",
                    json={"name": model_name},
                ) as resp:
                    last_status = ""
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        import json
                        try:
                            data = json.loads(line)
                            status = data.get("status", "")
                            if status and status != last_status:
                                last_status = status
                                if self.ws_broadcast:
                                    from . import ws_protocol
                                    await self.ws_broadcast(ws_protocol.build_ai_chunk(
                                        "ollama_pull", f"[{model_name}] {status}"
                                    ))
                        except json.JSONDecodeError:
                            pass

            return f"✅ Model '{model_name}' pulled successfully."
        except Exception as e:
            return f"Pull failed for '{model_name}': {e}"

    async def delete_model(self, model_name: str) -> str:
        """Delete a local model."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{self.base_url}/api/delete",
                    json={"name": model_name},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return f"✅ Model '{model_name}' deleted."
                return f"Delete failed ({resp.status_code}): {resp.text}"
        except Exception as e:
            return f"Delete failed: {e}"

    async def generate(self, prompt: str, model: str = "", stream: bool = False) -> str:
        """
        Direct generation via Ollama (used as final fallback by AIHandler).
        """
        model = model or self.default_model
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "")
                return f"Ollama error ({resp.status_code})"
        except Exception as e:
            return f"Ollama generate failed: {e}"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ping(self) -> bool:
        """Check if Ollama API is reachable."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.base_url, timeout=2.0)
                return resp.status_code < 500
        except Exception:
            return False
