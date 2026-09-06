"""
Makima OS — User Settings Store
Location: apps/brain/core/user_settings_store.py

Lightweight persistent KV store for user-configurable settings.
Backed by a JSON file in ~/.makima/settings.json.
Thread-safe for single-process use.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("makima.settings_store")

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "privacy_mode": False,
    "backup_path": "",
    "default_llm_backend": "groq",
    "theme": "dark",
}

_DEFAULT_INTEGRATION_FIELDS: Dict[str, Dict[str, str]] = {
    "telegram": {"bot_token": ""},
    "whatsapp": {"phone": ""},
    "github": {"api_key": ""},
    "gdrive": {"client_token": ""},
    "gmail": {"app_password": ""},
    "spotify": {"client_id": "", "client_secret": ""},
    "slack": {"webhook_url": ""},
    "notion": {"api_token": ""},
    "discord": {"bot_token": ""},
    "youtube": {"api_key": ""},
    "figma": {"personal_token": ""},
}


class UserSettingsStore:
    """
    Persistent user settings and integration credential store.

    Settings live in ~/.makima/settings.json.
    Integration credentials live in ~/.makima/integrations.json.
    Both are written atomically on every save.
    """

    def __init__(self, base_dir: str = "~/.makima") -> None:
        self._base = Path(base_dir).expanduser()
        self._base.mkdir(parents=True, exist_ok=True)
        self._settings_path = self._base / "settings.json"
        self._integrations_path = self._base / "integrations.json"
        self._settings: Dict[str, Any] = self._load(self._settings_path, _DEFAULT_SETTINGS.copy())
        self._integrations: Dict[str, Dict[str, str]] = self._load(
            self._integrations_path,
            {k: dict(v) for k, v in _DEFAULT_INTEGRATION_FIELDS.items()},
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _load(path: Path, default: Any) -> Any:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read %s: %s — using defaults", path, e)
        return default

    def _save(self, path: Path, data: Any) -> None:
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.error("Failed to save %s: %s", path, e)

    # ── General settings ─────────────────────────────────────────────────────

    def get_settings(self) -> Dict[str, Any]:
        return dict(self._settings)

    def update_settings(self, updates: Dict[str, Any]) -> None:
        self._settings.update(updates)
        self._save(self._settings_path, self._settings)

    # -- LLM provider settings ---------------------------------------------

    def get_llm_overrides(self) -> Dict[str, Dict[str, Any]]:
        """Return persisted provider overrides without exposing unrelated secrets."""
        overrides: Dict[str, Dict[str, Any]] = {}
        for integration_id, fields in self._integrations.items():
            if not integration_id.startswith("llm:") or not isinstance(fields, dict):
                continue
            provider = integration_id.removeprefix("llm:")
            overrides[provider] = {
                key: fields[key]
                for key in ("api_key", "model", "base_url", "enabled")
                if key in fields
            }
        return overrides

    def save_llm_provider(self, provider: str, fields: Dict[str, Any]) -> None:
        """Persist an LLM provider config separately from general UI settings."""
        safe_fields = {
            key: value
            for key, value in fields.items()
            if key in {"api_key", "model", "base_url", "enabled"}
            and value is not None
            and (not isinstance(value, str) or value.strip())
        }
        self.save_integration(f"llm:{provider}", safe_fields)

    # ── Integration credentials ───────────────────────────────────────────────

    def get_integration_fields(self, integration_id: str) -> Dict[str, str]:
        return dict(self._integrations.get(integration_id, {}))

    def get_integration_raw(self, integration_id: str) -> Dict[str, str]:
        return dict(self._integrations.get(integration_id, {}))

    def save_integration(self, integration_id: str, fields: Dict[str, str]) -> None:
        existing = self._integrations.setdefault(integration_id, {})
        existing.update(fields)
        self._save(self._integrations_path, self._integrations)
        logger.info("Saved integration config for: %s", integration_id)

    def list_integrations(self) -> list[str]:
        return list(self._integrations.keys())
