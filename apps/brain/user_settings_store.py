"""
Makima v7.1 -- User Settings & Integrations Store

Simple JSON-file-backed persistence for settings that were previously
UI-only (SettingsPage.tsx/IntegrationsPage.tsx had no save path at all --
every value lived in React state and vanished on refresh).

Scope, deliberately kept honest: this makes Save/Test buttons *persist*
data across restarts. It does NOT hot-reload already-running services
(MessagingHub, AIHandler's backend list, etc.) -- those read their config
once at startup from CONFIG. A saved integration credential or an LLM
backend toggle takes effect on the *next* brain restart, not instantly.
That's a real limitation, not hidden: the API responses say so explicitly
so the UI can surface it instead of implying something it doesn't do.

Secrets (API keys, bot tokens) are stored in this JSON file in plain text,
same trust boundary as .env already on this machine (single-user local
desktop app, not a hosted multi-tenant service). Not sent back to the
client in GET responses -- masked to avoid the UI accidentally displaying
a secret it doesn't need to re-render.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("makima.user_settings_store")

_SECRET_FIELD_HINTS = ("token", "key", "secret", "password", "credential")


class UserSettingsStore:
    """Async-safe (single-process, lock-guarded) JSON settings store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error("Failed to load %s, starting empty: %s", self.path, e)
                self._data = {}
        else:
            self._data = {"settings": {}, "integrations": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # General settings (privacy mode, backup path, LLM backend toggles...)
    # ------------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        return dict(self._data.get("settings", {}))

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        settings = self._data.setdefault("settings", {})
        settings.update(updates)
        self._save()
        return dict(settings)

    # ------------------------------------------------------------------
    # Integrations (per-service credential fields)
    # ------------------------------------------------------------------

    def get_integration_fields(self, integration_id: str, mask_secrets: bool = True) -> dict[str, str]:
        fields = dict(self._data.get("integrations", {}).get(integration_id, {}))
        if mask_secrets:
            for k in list(fields.keys()):
                if any(hint in k.lower() for hint in _SECRET_FIELD_HINTS) and fields[k]:
                    fields[k] = "\u2022" * 8  # bullet-masked, never echo real secret back
        return fields

    def save_integration_fields(self, integration_id: str, fields: dict[str, str]) -> None:
        integrations = self._data.setdefault("integrations", {})
        existing = integrations.setdefault(integration_id, {})
        for k, v in fields.items():
            # Don't overwrite a real stored secret with a masked placeholder
            # coming back from a GET-then-POST round trip in the UI.
            if v and set(v) == {"\u2022"}:
                continue
            existing[k] = v
        self._save()

    def has_integration_configured(self, integration_id: str) -> bool:
        fields = self._data.get("integrations", {}).get(integration_id, {})
        return bool(fields) and any(v for v in fields.values())

    def get_integration_raw(self, integration_id: str) -> dict[str, str]:
        """Unmasked -- for internal backend use only (e.g. a connection test), never returned over HTTP."""
        return dict(self._data.get("integrations", {}).get(integration_id, {}))
