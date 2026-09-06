"""Makima v7.2 — TokenStore

Securely stores OAuth access/refresh tokens in a local SQLite database.
The Fernet encryption key is stored in the native OS Credential Manager
(via `keyring`), ensuring tokens are machine-bound and cannot be decrypted
even if the SQLite file is copied to another machine.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger("makima.auth.token_store")

_KEYRING_SERVICE = "makima_oauth_store"
_KEYRING_USERNAME = "fernet_key"


def _init_fernet():
    """
    Returns a Fernet instance. Key is fetched from OS keyring on every startup;
    generated and stored there on first boot. Falls back to a local key file
    if keyring is unavailable (e.g., headless environments).
    """
    from cryptography.fernet import Fernet
    import keyring as kr

    try:
        key = kr.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if not key:
            logger.info("[auth] First boot — generating machine-specific Fernet key.")
            key = Fernet.generate_key().decode("utf-8")
            kr.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)
            logger.info("[auth] Fernet key stored in OS Credential Manager.")
        return Fernet(key.encode("utf-8"))
    except Exception as e:
        logger.warning("[auth] Keyring unavailable (%s), using local fallback key file.", e)
        # Fallback: a local key file (still better than plaintext tokens)
        home = Path(os.path.expanduser("~/.makima"))
        home.mkdir(parents=True, exist_ok=True)
        key_path = home / ".oauth_key"
        if not key_path.exists():
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            key_path.chmod(0o600)
        else:
            key = key_path.read_bytes()
        return Fernet(key)


class TokenStore:
    """Encrypted SQLite-backed store for OAuth tokens."""

    def __init__(self, db_path: str = "~/.makima/auth_tokens.sqlite") -> None:
        self.db_path = Path(os.path.expanduser(db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = _init_fernet()
        self._init_db()

    # ───────────── DB bootstrap ─────────────

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    provider     TEXT PRIMARY KEY,
                    encrypted    TEXT NOT NULL,
                    updated_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

    # ───────────── Public API ─────────────

    def save(self, provider: str, token_data: dict) -> None:
        """Encrypt and persist token_data for the given provider."""
        try:
            blob = self._fernet.encrypt(json.dumps(token_data).encode()).decode()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO tokens (provider, encrypted, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(provider) DO UPDATE SET
                        encrypted  = excluded.encrypted,
                        updated_at = excluded.updated_at
                """, (provider, blob))
                conn.commit()
            logger.info("[auth] Token saved for provider=%s", provider)
        except Exception as exc:
            logger.error("[auth] save failed for %s: %s", provider, exc)

    def get(self, provider: str) -> Optional[dict]:
        """Decrypt and return token_data for the given provider, or None."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT encrypted FROM tokens WHERE provider = ?", (provider,)
                ).fetchone()
            if not row:
                return None
            return json.loads(self._fernet.decrypt(row[0].encode()).decode())
        except Exception as exc:
            logger.error("[auth] get failed for %s: %s", provider, exc)
            return None

    def delete(self, provider: str) -> None:
        """Remove stored token for the given provider."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM tokens WHERE provider = ?", (provider,))
                conn.commit()
            logger.info("[auth] Token deleted for provider=%s", provider)
        except Exception as exc:
            logger.error("[auth] delete failed for %s: %s", provider, exc)

    def list_providers(self) -> list[str]:
        """Return list of providers with saved tokens."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT provider FROM tokens").fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            logger.error("[auth] list_providers failed: %s", exc)
            return []
