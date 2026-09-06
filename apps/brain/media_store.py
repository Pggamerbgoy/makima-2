"""Safe local media storage for the Makima chat UI.

The store deliberately keeps binary files outside the WebSocket payload.  The
browser uploads a file once, receives a generated media id, and sends only that
id with the chat message.  Metadata is persisted atomically for the local
library and all filesystem paths are derived from generated ids.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, BinaryIO


class MediaValidationError(ValueError):
    """Raised when an upload is unsupported or exceeds its safe limit."""


class MediaNotFoundError(FileNotFoundError):
    """Raised when a media id is not present in the local library."""


class MediaStore:
    """Atomic, local-only media library with generated-id path safety."""

    MAX_BYTES = {
        "image": 20 * 1024 * 1024,
        "document": 20 * 1024 * 1024,
        "audio": 20 * 1024 * 1024,
        "video": 100 * 1024 * 1024,
    }

    MIME_KINDS = {
        "image/jpeg": "image",
        "image/png": "image",
        "image/gif": "image",
        "image/webp": "image",
        "image/bmp": "image",
        "video/mp4": "video",
        "video/webm": "video",
        "video/mpeg": "video",
        "video/quicktime": "video",
        "video/x-msvideo": "video",
        "audio/mpeg": "audio",
        "audio/wav": "audio",
        "audio/x-wav": "audio",
        "audio/ogg": "audio",
        "audio/webm": "audio",
        "audio/mp4": "audio",
        "audio/aac": "audio",
        "application/pdf": "document",
        "application/json": "document",
        "application/rtf": "document",
        "text/plain": "document",
        "text/markdown": "document",
        "text/csv": "document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "document",
    }
    _ID_RE = re.compile(r"^media_[a-f0-9]{32}$")

    def __init__(self, base_dir: str = "~/.makima/media") -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.base_dir / "manifest.json"
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {str(k): v for k, v in payload.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass
        return {}

    def _save_manifest(self) -> None:
        temp_path = self.manifest_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self._items, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self.manifest_path)

    @classmethod
    def kind_for(cls, file_name: str, mime_type: str | None) -> str:
        normalized = str(mime_type or "").split(";", 1)[0].strip().lower()
        if normalized in cls.MIME_KINDS:
            return cls.MIME_KINDS[normalized]
        guessed, _ = mimetypes.guess_type(file_name)
        if guessed in cls.MIME_KINDS:
            return cls.MIME_KINDS[guessed]
        raise MediaValidationError("This file type is not supported by Makima.")

    @classmethod
    def validate(cls, file_name: str, mime_type: str | None, size: int) -> tuple[str, str]:
        safe_name = Path(str(file_name or "")).name.strip()
        if not safe_name or safe_name in {".", ".."}:
            raise MediaValidationError("A valid file name is required.")
        kind = cls.kind_for(safe_name, mime_type)
        if size < 1:
            raise MediaValidationError("The uploaded file is empty.")
        if size > cls.MAX_BYTES[kind]:
            limit_mb = cls.MAX_BYTES[kind] // (1024 * 1024)
            raise MediaValidationError(f"{kind.title()} files are limited to {limit_mb} MB.")
        normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
        guessed_mime, _ = mimetypes.guess_type(safe_name)
        if normalized_mime in cls.MIME_KINDS and guessed_mime in cls.MIME_KINDS:
            if cls.MIME_KINDS[normalized_mime] != cls.MIME_KINDS[guessed_mime]:
                raise MediaValidationError("The file extension and MIME type do not match.")
        if normalized_mime not in cls.MIME_KINDS:
            normalized_mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        return safe_name, normalized_mime

    def _path_for(self, media_id: str) -> Path:
        if not self._ID_RE.fullmatch(str(media_id)):
            raise MediaNotFoundError("Invalid media id")
        item = self._items.get(media_id)
        if not item:
            raise MediaNotFoundError("Media not found")
        path = (self.base_dir / str(item["stored_name"])).resolve()
        if path.parent != self.base_dir or not path.is_file():
            raise MediaNotFoundError("Invalid media path")
        return path

    def save_upload_file(self, file_obj: BinaryIO, file_name: str, mime_type: str | None) -> dict[str, Any]:
        """Copy an UploadFile stream to disk while enforcing limits incrementally."""
        safe_name = Path(str(file_name or "")).name.strip()
        # Validate the name/type before touching the filesystem.  The size is
        # checked again after streaming because UploadFile does not expose a
        # trusted content length.
        if not safe_name or safe_name in {".", ".."}:
            raise MediaValidationError("A valid file name is required.")
        kind = self.kind_for(safe_name, mime_type)
        maximum = self.MAX_BYTES[kind]
        media_id = f"media_{uuid.uuid4().hex}"
        stored_name = f"{media_id}{Path(safe_name).suffix.lower()[:12]}"
        destination = self.base_dir / stored_name
        temp_path: Path | None = None
        total = 0
        digest = hashlib.sha256()
        try:
            with tempfile.NamedTemporaryFile(dir=self.base_dir, prefix="upload_", delete=False) as temp:
                temp_path = Path(temp.name)
                while True:
                    chunk = file_obj.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum:
                        raise MediaValidationError(f"{kind.title()} files are too large.")
                    digest.update(chunk)
                    temp.write(chunk)
            safe_name, normalized_mime = self.validate(safe_name, mime_type, total)
            os.replace(temp_path, destination)
            temp_path = None
            item = {
                "id": media_id,
                "kind": kind,
                "mime_type": normalized_mime,
                "name": safe_name,
                "size": total,
                "sha256": digest.hexdigest(),
                "stored_name": stored_name,
                "status": "ready",
            }
            with self._lock:
                self._items[media_id] = item
                self._save_manifest()
            return dict(item)
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, media_id: str) -> dict[str, Any]:
        item = self._items.get(str(media_id))
        if not item:
            raise MediaNotFoundError("Media not found")
        self._path_for(str(media_id))
        return dict(item)

    def list(self, kind: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        normalized_kind = str(kind or "").lower().strip()
        normalized_query = str(query or "").lower().strip()
        items = []
        for item in list(self._items.values()):
            if normalized_kind and item.get("kind") != normalized_kind:
                continue
            if normalized_query and normalized_query not in str(item.get("name", "")).lower():
                continue
            items.append(dict(item))
        return sorted(items, key=lambda item: item.get("id", ""), reverse=True)

    def file_path(self, media_id: str) -> Path:
        return self._path_for(str(media_id))

    def delete(self, media_id: str) -> None:
        media_id = str(media_id)
        path = self._path_for(media_id)
        path.unlink(missing_ok=True)
        with self._lock:
            self._items.pop(media_id, None)
            self._save_manifest()
