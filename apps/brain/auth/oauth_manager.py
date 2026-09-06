"""Makima v7.2 — OAuthManager

Handles the full OAuth 2.0 Authorization Code Flow with PKCE for local desktop
app use-cases. Since Makima is a "public client" it cannot safely hold a static
client_secret; PKCE replaces that security property by generating a fresh
cryptographic challenge for every login attempt.

Supported providers (Wave 1):
  - github      GitHub App (repo read/write, PRs, issues)
  - google      Google Workspace (Gmail read, Calendar read/write)

Adding a new provider: just add an entry to PROVIDERS below.

PKCE flow overview:
  1. /auth/login/{provider}
       → generate code_verifier (random 128-char hex)
       → derive code_challenge = BASE64URL(SHA256(code_verifier))
       → store code_verifier in state dict (keyed by state param)
       → redirect browser to provider auth URL
  2. /auth/callback?code=...&state=...
       → look up code_verifier from state
       → POST to token endpoint with code + code_verifier
       → store access_token + refresh_token via TokenStore
       → serve auto-close HTML page
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from .token_store import TokenStore

logger = logging.getLogger("makima.auth.oauth_manager")

# ─────────────────────────────────────────────────────────────────────────────
# Provider registry
# Each entry:
#   client_id_env     : env var holding the client/app ID (user must set)
#   client_secret_env : env var for client secret (required by GitHub & Google Web apps)
#   auth_url          : authorization endpoint
#   token_url         : token exchange endpoint
#   scopes            : space-separated scope list
#   pkce              : True = S256 PKCE challenge added to auth URL
# ─────────────────────────────────────────────────────────────────────────────
PROVIDERS: dict[str, dict] = {
    "github": {
        "client_id_env": "MAKIMA_GITHUB_CLIENT_ID",
        "client_secret_env": "MAKIMA_GITHUB_CLIENT_SECRET",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "scopes": "repo user read:org",
        "pkce": False,  # GitHub OAuth Apps do not support PKCE; uses state CSRF + client_secret
        "grant_type": "authorization_code",
        "token_type": "github",
    },
    "google": {
        "client_id_env": "MAKIMA_GOOGLE_CLIENT_ID",
        "client_secret_env": "MAKIMA_GOOGLE_CLIENT_SECRET",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": (
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/calendar"
        ),
        "pkce": True,   # Google Web apps support PKCE but still require client_secret
        "grant_type": "authorization_code",
        "token_type": "google",
    },
}

# Redirect URI — Makima's local server
_REDIRECT_URI = "http://127.0.0.1:8080/auth/callback"

# Auto-close HTML page served after a successful OAuth redirect
_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Makima — Connected!</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f0f1a; color: #a78bfa;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ text-align: center; padding: 40px 60px; border: 1px solid #4c1d95;
             border-radius: 16px; background: #1a1a2e; }}
    h1 {{ font-size: 2rem; margin-bottom: 8px; }}
    p {{ color: #9ca3af; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>✓ Connected to {provider_name}!</h1>
    <p>Makima is now linked. You can close this tab.</p>
    <script>setTimeout(() => window.close(), 1500);</script>
  </div>
</body>
</html>"""

_ERROR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Makima — Connection Failed</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f0f1a; color: #f87171;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ text-align: center; padding: 40px 60px; border: 1px solid #7f1d1d;
             border-radius: 16px; background: #1a0a0a; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>✗ Connection Failed</h1>
    <p>{error_message}</p>
    <script>setTimeout(() => window.close(), 4000);</script>
  </div>
</body>
</html>"""


class OAuthManager:
    """
    Orchestrates OAuth 2.0 + PKCE flows for all supported external providers.
    Instances are singleton — typically created once at Brain startup.
    """

    def __init__(self, token_store: Optional[TokenStore] = None) -> None:
        self._store = token_store or TokenStore()
        # Pending states: {state_param -> {"verifier": str, "provider": str}}
        self._pending: dict[str, dict] = {}

    # ─────────────────────────── Login (step 1) ────────────────────────────

    def get_auth_url(self, provider: str) -> str:
        """
        Generate the provider's authorization URL.
        Includes PKCE challenge for PKCE-capable providers,
        or a plain state token for GitHub-style flows.
        Returns the URL to redirect the user's browser to.
        """
        cfg = PROVIDERS.get(provider)
        if not cfg:
            raise ValueError(f"Unknown OAuth provider: {provider!r}")

        client_id = os.environ.get(cfg["client_id_env"], "")
        if not client_id:
            raise EnvironmentError(
                f"Missing env var {cfg['client_id_env']!r}. "
                f"Register a {provider} OAuth App and set that variable."
            )

        # Prune expired pending states (> 600s)
        now = time.time()
        expired_states = [
            s for s, e in self._pending.items()
            if now - e.get("created_at", now) > 600.0
        ]
        for s in expired_states:
            self._pending.pop(s, None)

        state = secrets.token_urlsafe(32)
        entry: dict = {"provider": provider, "created_at": now}

        params: dict = {
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "state": state,
            "scope": cfg["scopes"],
        }

        if cfg["pkce"]:
            verifier = secrets.token_hex(64)          # 128-char hex → 512-bit entropy
            challenge = _s256(verifier)
            entry["verifier"] = verifier
            params.update({
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "response_type": "code",
                "access_type": "offline",              # Google: request refresh_token
                "prompt": "consent",                   # Google: always show consent screen
            })
        else:
            params["response_type"] = "code"

        self._pending[state] = entry
        return cfg["auth_url"] + "?" + urlencode(params)

    # ─────────────────────────── Callback (step 2) ─────────────────────────

    async def handle_callback(self, code: str, state: str) -> str:
        """
        Exchange the authorization code for tokens, store them, and return
        HTML that closes the browser tab.
        """
        entry = self._pending.pop(state, None)
        if not entry:
            logger.warning("[auth] Unknown or expired state param: %s", state)
            return _ERROR_HTML.format(error_message="Invalid or expired login session.")

        provider = entry["provider"]
        cfg = PROVIDERS[provider]
        client_id = os.environ.get(cfg["client_id_env"], "")
        client_secret = os.environ.get(cfg.get("client_secret_env", ""), "")

        payload: dict = {
            "client_id": client_id,
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "grant_type": cfg.get("grant_type", "authorization_code"),
        }
        # Include client_secret if configured (required by GitHub and Google Web apps)
        if client_secret:
            payload["client_secret"] = client_secret
        # Include PKCE verifier if this was a PKCE flow
        if cfg["pkce"] and "verifier" in entry:
            payload["code_verifier"] = entry["verifier"]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    cfg["token_url"],
                    data=payload,
                    headers={"Accept": "application/json"},
                    timeout=15.0,
                )
                resp.raise_for_status()
                token_data = resp.json()

            # Normalize + add metadata
            token_data["provider"] = provider
            token_data["obtained_at"] = time.time()

            if "error" in token_data:
                logger.error("[auth] Token exchange error for %s: %s", provider, token_data)
                return _ERROR_HTML.format(error_message=token_data.get("error_description", token_data["error"]))

            self._store.save(provider, token_data)
            logger.info("[auth] Successfully connected to %s", provider)
            return _SUCCESS_HTML.format(provider_name=provider.capitalize())

        except Exception as exc:
            logger.error("[auth] Callback failed for %s: %s", provider, exc)
            return _ERROR_HTML.format(error_message=str(exc))

    # ─────────────────────────── Token access ──────────────────────────────

    async def get_access_token(self, provider: str) -> Optional[str]:
        """
        Returns a valid access token for the given provider.
        Automatically refreshes if the token is expired.
        Returns None if not connected.
        """
        token_data = self._store.get(provider)
        if not token_data:
            return None

        # Check expiry (Google tokens expire in 3600s, GitHub tokens don't)
        if _is_expired(token_data):
            token_data = await self._refresh_token(provider, token_data)
            if not token_data:
                return None

        return token_data.get("access_token")

    async def _refresh_token(self, provider: str, token_data: dict) -> Optional[dict]:
        """Attempt to refresh an expired access token using the refresh_token."""
        cfg = PROVIDERS.get(provider)
        if not cfg:
            return None

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            logger.warning("[auth] No refresh_token available for %s — user must re-authenticate.", provider)
            return None

        client_id = os.environ.get(cfg["client_id_env"], "")
        client_secret = os.environ.get(cfg.get("client_secret_env", ""), "")
        payload = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if client_secret:
            payload["client_secret"] = client_secret

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(cfg["token_url"], data=payload, timeout=15.0)
                resp.raise_for_status()
                new_data = resp.json()

            if "error" in new_data:
                logger.error("[auth] Refresh failed for %s: %s", provider, new_data)
                return None

            # Preserve refresh_token if not returned in response (Google may omit it)
            if "refresh_token" not in new_data:
                new_data["refresh_token"] = refresh_token
            new_data["provider"] = provider
            new_data["obtained_at"] = time.time()

            self._store.save(provider, new_data)
            logger.info("[auth] Token refreshed for %s", provider)
            return new_data
        except Exception as exc:
            logger.error("[auth] Refresh exception for %s: %s", provider, exc)
            return None

    # ─────────────────────────── Status ────────────────────────────────────

    def status(self) -> dict[str, bool]:
        """Returns {provider: is_connected} for all known providers."""
        connected = self._store.list_providers()
        return {p: p in connected for p in PROVIDERS}

    def disconnect(self, provider: str) -> None:
        """Revoke stored token for a provider."""
        self._store.delete(provider)
        logger.info("[auth] Disconnected from %s", provider)


# ─────────────────────────── PKCE helpers ───────────────────────────────────

def _s256(verifier: str) -> str:
    """Compute S256 PKCE code_challenge from a code_verifier string."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _is_expired(token_data: dict, buffer_s: int = 60) -> bool:
    """Returns True if access_token appears to be expired (with 60s buffer)."""
    obtained_at = float(token_data.get("obtained_at", 0.0) or 0.0)
    raw_expires = token_data.get("expires_in", 0)
    if not raw_expires:
        return False   # GitHub tokens don't expire; treat as valid
    try:
        expires_in = float(raw_expires)
    except (ValueError, TypeError):
        return False
    return time.time() >= (obtained_at + expires_in - buffer_s)
