"""Small async Ollama control-plane adapter used by the desktop UI."""

from __future__ import annotations

from typing import Any


class OllamaService:
    """Expose model-management operations without coupling them to AIHandler."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = (config or {}).get("llm", {}).get("backends", {}).get("ollama", {})
        self.base_url = str(cfg.get("base_url") or cfg.get("host") or "http://127.0.0.1:11434").rstrip("/")
        self.default_model = str(cfg.get("model") or "")

    async def list_models(self) -> list[dict[str, Any]]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
            models = payload.get("models", [])
            return models if isinstance(models, list) else []
        except Exception:
            return []

    async def pull_model(self, model_name: str) -> str:
        return await self._mutate("pull", model_name)

    async def delete_model(self, model_name: str) -> str:
        return await self._mutate("delete", model_name, method="DELETE")

    async def _mutate(self, operation: str, model_name: str, method: str = "POST") -> str:
        import httpx

        model = str(model_name or "").strip()
        if not model or len(model) > 200 or any(ch in model for ch in "\r\n"):
            return "A valid Ollama model name is required."

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                if method == "DELETE":
                    response = await client.request(method, f"{self.base_url}/api/{operation}", json={"name": model})
                else:
                    response = await client.request(method, f"{self.base_url}/api/{operation}", json={"name": model, "stream": False})
                response.raise_for_status()
            return f"Ollama model {model} {operation} request completed."
        except Exception as exc:
            return f"Ollama {operation} failed: {type(exc).__name__}."
