"""
Security Engine module — Fast local PII and secret detection patterns.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


class SecurityEngine:
    """Scans text for leaked secrets, API keys, and sensitive tokens."""

    PII_PATTERNS = {
        "anthropic_key": re.compile(r"sk-ant-[a-zA-Z0-9_\-]{32,}", re.IGNORECASE),
        "openai_key": re.compile(r"sk-[a-zA-Z0-9_\-]{32,}", re.IGNORECASE),
        "generic_api_key": re.compile(r"(?:api_key|token|secret)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", re.IGNORECASE),
        "email_address": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", re.IGNORECASE),
        "private_key_header": re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|PGP)? PRIVATE KEY-----", re.IGNORECASE),
    }

    @classmethod
    def scan_text(cls, text: str) -> Dict[str, Any]:
        findings: List[Dict[str, str]] = []
        for pattern_name, regex in cls.PII_PATTERNS.items():
            matches = regex.findall(text)
            for m in matches:
                findings.append({
                    "type": pattern_name,
                    "preview": m[:10] + "..." if len(m) > 10 else m,
                })
        return {
            "has_pii": len(findings) > 0,
            "findings_count": len(findings),
            "findings": findings,
        }
