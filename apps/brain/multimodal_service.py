"""Provider-aware preparation of locally stored chat attachments."""

from __future__ import annotations

import base64
import asyncio
import io
from pathlib import Path
from typing import Any

from .media_store import MediaNotFoundError, MediaStore


class MediaCapabilityError(ValueError):
    """Raised when the selected provider cannot accept an attachment kind."""


class MultimodalService:
    """Resolve safe media ids into provider-neutral message context.

    The service never accepts a client filesystem path.  It only reads files
    that have already passed through MediaStore's generated-id and size checks.
    """

    def __init__(self, media_store: MediaStore, ai_handler: Any = None) -> None:
        self.media_store = media_store
        self.ai_handler = ai_handler

    async def process_file(self, file_name: str, mime_type: str, file_data: str) -> str:
        """Compatibility adapter for the legacy base64 message payload."""
        try:
            raw = base64.b64decode(file_data, validate=True)
        except Exception as exc:
            raise ValueError("Legacy attachment is not valid base64") from exc
        item = self.media_store.save_upload_file(io.BytesIO(raw), file_name, mime_type)
        if item["mime_type"] in {"text/plain", "text/markdown", "text/csv", "application/json"}:
            content = self.media_store.file_path(item["id"]).read_text(encoding="utf-8", errors="replace")[:120_000]
            return f"[{item['name']}]\n{content}"
        return f"Attached document: {item['name']} ({item['mime_type']}, {item['size']} bytes)."

    DEFAULT_PROVIDER_CAPABILITIES = {
        "gemini": {"text": True, "image": True, "audio": True, "video": True},
        "openai": {"text": True, "image": True, "audio": False, "video": False},
        "anthropic": {"text": True, "image": True, "audio": False, "video": False},
        "claude": {"text": True, "image": True, "audio": False, "video": False},
        "ollama": {"text": True, "image": True, "audio": False, "video": False},
        "groq": {"text": True, "image": False, "audio": False, "video": False},
        "openrouter": {"text": True, "image": True, "audio": False, "video": False},
        "qwen": {"text": True, "image": True, "audio": True, "video": False},
        "qwen_flash": {"text": True, "image": True, "audio": True, "video": False},
        "deepseek": {"text": True, "image": False, "audio": False, "video": False},
        "deepseek_v32": {"text": True, "image": False, "audio": False, "video": False},
        "cerebras": {"text": True, "image": False, "audio": False, "video": False},
        "huggingface": {"text": True, "image": False, "audio": False, "video": False},
    }

    def _provider_capabilities(self, provider_id: str | None) -> dict[str, bool]:
        raw_provider = (provider_id or getattr(self.ai_handler, "default_provider", "") or "").lower()
        provider = raw_provider
        if hasattr(self.ai_handler, "_resolve_backend_name"):
            provider = self.ai_handler._resolve_backend_name(raw_provider)
        backend = getattr(self.ai_handler, "backends", {}).get(provider) or getattr(self.ai_handler, "backends", {}).get(raw_provider)
        backend_caps = dict(getattr(backend, "capabilities", {}) or {})
        if backend_caps:
            return backend_caps
        return self.DEFAULT_PROVIDER_CAPABILITIES.get(
            provider,
            self.DEFAULT_PROVIDER_CAPABILITIES.get(raw_provider, {"text": True, "image": False, "video": False, "audio": False})
        )

    def resolve_attachments(
        self,
        attachments: list[dict[str, Any]],
        provider_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Validate media ids and return safe metadata plus model content."""
        resolved: list[dict[str, Any]] = []
        capabilities = self._provider_capabilities(provider_id)
        for attachment in attachments:
            media_id = str(attachment.get("id") or "")
            if not media_id:
                raise MediaNotFoundError("Attachment is missing a media id")
            item = self.media_store.get(media_id)
            kind = str(item.get("kind") or "document")
            capability_key = "text" if kind == "document" else kind
            if not capabilities.get(capability_key, False):
                raise MediaCapabilityError(
                    f"The selected provider does not support {kind} analysis. "
                    "Select a provider with this capability in Settings."
                )

            entry = {
                "id": item["id"],
                "kind": kind,
                "mime_type": item["mime_type"],
                "name": item["name"],
                "size": item["size"],
                "status": item.get("status", "ready"),
                "url": f"/media/{item['id']}/content",
            }
            path = self.media_store.file_path(media_id)
            if kind == "image":
                raw = path.read_bytes()
                encoded = base64.b64encode(raw).decode("ascii")
                entry["content"] = {
                    "type": "image_url",
                    "image_url": {"url": f"data:{item['mime_type']};base64,{encoded}"},
                }
            elif kind == "document" and item["mime_type"] in {"text/plain", "text/markdown", "text/csv", "application/json"}:
                # Text documents are useful without a separate extraction
                # dependency. Keep the prompt bounded for predictable costs.
                text = path.read_text(encoding="utf-8", errors="replace")[:120_000]
                entry["content"] = {"type": "text", "text": f"\n[{item['name']}]\n{text}"}
            else:
                entry["content"] = {
                    "type": "text",
                    "text": f"Attached {kind} file: {item['name']} ({item['mime_type']}, {item['size']} bytes).",
                }
            resolved.append(entry)
        return resolved

    async def prepare_attachments(
        self,
        attachments: list[dict[str, Any]],
        prompt: str,
        provider_id: str | None = None,
        emit: Any = None,
    ) -> list[dict[str, Any]]:
        """Resolve attachments and perform Gemini audio/video analysis.

        Gemini's interaction API is used only for media that cannot travel in
        the existing chat-completions content contract.  The resulting
        timestamp-aware analysis is then passed to the selected chat model as
        text, while the original file remains available for playback in UI.
        """
        resolved = self.resolve_attachments(attachments, provider_id)
        provider = provider_id or getattr(self.ai_handler, "default_provider", "")
        backend = getattr(self.ai_handler, "backends", {}).get(provider)
        for entry in resolved:
            if entry["kind"] not in {"video", "audio"}:
                continue
            if provider != "gemini" or not backend:
                raise MediaCapabilityError(
                    f"The selected provider does not support {entry['kind']} analysis. "
                    "Select Gemini in Settings."
                )
            if emit:
                await emit("media_processing", entry["id"], {"stage": "analyzing", "kind": entry["kind"]})
            analysis = await self._analyze_gemini_media(
                entry,
                prompt or f"Analyze the attached {entry['kind']} and describe the important details with timestamps.",
                backend,
            )
            entry["content"] = {
                "type": "text",
                "text": f"\n[{entry['name']} Gemini media analysis]\n{analysis}",
            }
            entry["analysis"] = analysis
        return resolved

    async def _analyze_gemini_media(self, entry: dict[str, Any], prompt: str, backend: Any) -> str:
        import httpx

        path = self.media_store.file_path(entry["id"])
        raw = path.read_bytes()
        key = backend.get_api_key()
        model = backend.model or "gemini-2.5-flash"
        input_item: dict[str, Any]
        # Keep inline requests comfortably below the documented small-file
        # boundary. Larger local uploads use the resumable Files API.
        if len(raw) < 20 * 1024 * 1024:
            input_item = {
                "type": entry["kind"],
                "data": base64.b64encode(raw).decode("ascii"),
                "mime_type": entry["mime_type"],
            }
        else:
            async with httpx.AsyncClient(timeout=120.0, verify=self.ai_handler.config.get("tls_verify", True)) as client:
                start = await client.post(
                    "https://generativelanguage.googleapis.com/upload/v1beta/files",
                    headers={
                        "x-goog-api-key": key,
                        "X-Goog-Upload-Protocol": "resumable",
                        "X-Goog-Upload-Command": "start",
                        "X-Goog-Upload-Header-Content-Length": str(len(raw)),
                        "X-Goog-Upload-Header-Content-Type": entry["mime_type"],
                        "Content-Type": "application/json",
                    },
                    json={"file": {"display_name": entry["name"]}},
                )
                start.raise_for_status()
                upload_url = start.headers.get("x-goog-upload-url")
                if not upload_url:
                    raise RuntimeError("Gemini did not return a resumable upload URL")
                uploaded = await client.post(
                    upload_url,
                    headers={
                        "Content-Length": str(len(raw)),
                        "X-Goog-Upload-Offset": "0",
                        "X-Goog-Upload-Command": "upload, finalize",
                    },
                    content=raw,
                )
                uploaded.raise_for_status()
                file_data = uploaded.json().get("file", {})
                file_uri = file_data.get("uri")
                file_name = file_data.get("name")
                if not file_uri or not file_name:
                    raise RuntimeError("Gemini file upload returned no file reference")
                for _ in range(60):
                    status = await client.get(
                        f"https://generativelanguage.googleapis.com/v1beta/{file_name}",
                        headers={"x-goog-api-key": key},
                    )
                    status.raise_for_status()
                    state = str(status.json().get("state", ""))
                    if state == "ACTIVE":
                        break
                    if state == "FAILED":
                        raise RuntimeError("Gemini failed to process the uploaded media")
                    await asyncio.sleep(2)
                else:
                    raise RuntimeError("Gemini media processing timed out")
                input_item = {"type": entry["kind"], "uri": file_uri, "mime_type": entry["mime_type"]}

        async with httpx.AsyncClient(timeout=120.0, verify=self.ai_handler.config.get("tls_verify", True)) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"model": model, "input": [input_item, {"type": "text", "text": prompt}]},
            )
            response.raise_for_status()
            body = response.json()
        text = body.get("output_text")
        if text:
            return str(text).strip()
        # Be tolerant of the nested interaction response shape.
        parts = []
        for step in body.get("steps", []):
            for content in step.get("content", []):
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))
        return "\n".join(parts).strip() or "Gemini returned no media analysis."
