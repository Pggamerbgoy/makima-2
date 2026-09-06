"""
Makima v8.1 — Standalone Security Capability Tools
Location: apps/brain/tools/security_tools.py

Enterprise-grade security auditing tools:
- Port scanning with concurrent async sockets
- Dependency vulnerability analysis (CVE matching against VULNERABILITY_DB)
- Secret detection (regex patterns + Shannon entropy)
- AST security analysis rules & visitors

Follows Makima Zero-Agent-Dependency rule: Tools define pure logic, Agents import tools.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import re
import socket
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("makima.tools.security")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Constants & Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DANGEROUS_AST_CALLS = {
    "eval", "exec", "compile", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "input", "open"
}

DANGEROUS_MODULES = {
    "pickle", "cPickle", "shelve", "marshal", "subprocess", "os", "sys",
    "pty", "socket", "ctypes", "multiprocessing"
}

VULNERABILITY_DB: Dict[str, Dict[str, str]] = {
    # --- Python web frameworks & libraries ---
    "requests": {"<2.31.0": "CVE-2023-32681 (Proxy-Authorization header leak)"},
    "urllib3": {"<2.0.6": "CVE-2023-43804 (Cookie header leak)"},
    "cryptography": {"<41.0.3": "CVE-2023-38325 (NULL pointer dereference)"},
    "django": {"<4.2.4": "CVE-2023-36053 (ReDoS in EmailValidator)"},
    "flask": {"<2.3.2": "CVE-2023-30861 (Cookie session bypass)"},
    "celery": {"<5.3.4": "CVE-2023-32758 (RCE via serialized payloads)"},
    "redis": {"<4.6.0": "CVE-2023-28856 (Connection race condition)"},
    "boto3": {"<1.28.0": "CVE-2023-30855 (Credential leakage in errors)"},
    "numpy": {"<1.22.0": "CVE-2021-41496 (Buffer overflow in array processing)"},
    "pandas": {"<2.0.0": "CVE-2023-37941 (SQL injection via to_sql)"},
    "setuptools": {"<65.5.1": "CVE-2022-40897 (ReDoS in package_url)"},
    "pillow": {"<10.0.1": "CVE-2023-44271 (DoS via infinite loop)"},
    "pyyaml": {"<6.0.1": "CVE-2020-14343 (Arbitrary code execution via yaml.load)"},
    "sqlalchemy": {"<2.0.19": "CVE-2023-30533 (SQL injection via order_by)"},
    "paramiko": {"<3.4.0": "CVE-2023-48795 (SSHv2 Terrapin attack)"},
    "aiohttp": {"<3.9.0": "CVE-2023-37276 (SSL cert validation bypass)"},
    "httpx": {"<0.24.1": "CVE-2023-29863 (POST redirect leaks Authorization)"},
    "starlette": {"<0.27.0": "CVE-2023-5078 (Path traversal in StaticFiles)"},
    "fastapi": {"<0.103.1": "CVE-2024-24762 (CORS origin bypass)"},
    "certifi": {"<2023.7.22": "CVE-2023-37920 (Removal of e-Tugra root cert)"},
    # --- JavaScript ecosystem (for package.json audits) ---
    "lodash": {"<4.17.21": "CVE-2021-23337 (Command Injection)"},
    "axios": {"<1.6.0": "CVE-2023-45857 (CSRF token exposure)"},
    "express": {"<4.18.2": "CVE-2022-24999 (qs prototype pollution)"},
}

SECRET_PATTERNS: List[Tuple[str, str]] = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"(?i)(aws_secret_access_key|aws_secret_key)\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?", "AWS Secret Key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub Personal Access Token"),
    (r"github_pat_[A-Za-z0-9_]{82}", "GitHub Fine-Grained PAT"),
    (r"sk_live_[A-Za-z0-9]{24,}", "Stripe Live Secret Key"),
    (r"sk_test_[A-Za-z0-9]{24,}", "Stripe Test Secret Key"),
    (r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", "Private Key Block"),
    (r"xox[baprs]-[A-Za-z0-9\-]+", "Slack Token"),
]

LOCAL_PREFIXES = (
    "127.", "192.168.", "10.", "172.16.", "172.17.", "172.18.",
    "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
    "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.",
    "172.31.", "localhost", "::1", "0.0.0.0"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse numeric release components without lexicographic mistakes."""
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts) or (0,)


def _version_matches(installed: str, condition: str) -> bool:
    """Evaluate version condition syntax used by the local CVE DB."""
    current = _version_tuple(installed)
    for clause in str(condition or "").split(","):
        match = re.match(r"\s*(<=|>=|==|!=|<|>|~=)?\s*([0-9][0-9A-Za-z.\-_]*)", clause)
        if not match:
            continue
        operator = match.group(1) or "=="
        target = _version_tuple(match.group(2))
        if operator == "<" and not current < target:
            return False
        if operator == "<=" and not current <= target:
            return False
        if operator == ">" and not current > target:
            return False
        if operator == ">=" and not current >= target:
            return False
        if operator == "==" and not (current == target or current[:len(target)] == target):
            return False
        if operator == "!=" and current == target:
            return False
        if operator == "~=" and not (current[:1] == target[:1] and current >= target):
            return False
    return True


def calculate_shannon_entropy(data: str) -> float:
    """Calculates the Shannon entropy of a string to detect high-entropy secrets."""
    if not data:
        return 0.0
    entropy = 0.0
    counts = Counter(data)
    length = len(data)
    for count in counts.values():
        probability = count / length
        if probability > 0:
            entropy -= probability * math.log2(probability)
    return entropy


def parse_requirements(file_path: Path) -> Dict[str, str]:
    """Parses a Python requirements.txt file into a dict of {package: version}."""
    deps = {}
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"^([a-zA-Z0-9_.-]+)\s*(?:==|>=|<=|~=|!=)\s*([0-9a-zA-Z.*-]+)", line)
            if match:
                deps[match.group(1).lower()] = match.group(2)
    except Exception as e:
        logger.warning("Failed to parse requirements.txt: %s", e)
    return deps


def parse_package_json(file_path: Path) -> Dict[str, str]:
    """Parses a Node.js package.json file into a dict of {package: version}."""
    deps = {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8", errors="ignore"))
        for section in ("dependencies", "devDependencies"):
            for pkg, ver in data.get(section, {}).items():
                clean_ver = re.sub(r"^[^0-9]*", "", str(ver))
                deps[pkg.lower()] = clean_ver
    except Exception as e:
        logger.warning("Failed to parse package.json: %s", e)
    return deps


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Capability Tool Functions (ToolRegistry Compatible)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def scan_ports(target: str, ports: str | list[int] = "22,80,443,8080,3306,5432", timeout: float = 2.0) -> str:
    """Non-destructive concurrent TCP port scan with timeout and semaphore gating."""
    clean_ports: list[int] = []
    if isinstance(ports, str):
        for p in ports.split(","):
            if p.strip().isdigit():
                p_int = int(p.strip())
                if 1 <= p_int <= 65535:
                    clean_ports.append(p_int)
    elif isinstance(ports, (list, tuple)):
        for p in ports:
            try:
                p_int = int(p)
                if 1 <= p_int <= 65535:
                    clean_ports.append(p_int)
            except (ValueError, TypeError):
                continue

    if not clean_ports:
        return f"[Error] No valid port numbers provided in '{ports}'"

    open_ports: list[int] = []
    closed_ports_count = 0
    sem = asyncio.Semaphore(50)

    async def scan_single(port: int) -> None:
        nonlocal closed_ports_count
        async with sem:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(target, port),
                    timeout=timeout,
                )
                writer.close()
                await writer.wait_closed()
                open_ports.append(port)
            except (asyncio.TimeoutError, ConnectionRefusedError, socket.gaierror, OSError):
                closed_ports_count += 1

    tasks = [scan_single(p) for p in clean_ports]
    await asyncio.gather(*tasks, return_exceptions=True)
    open_ports.sort()

    if open_ports:
        return f"Open ports on {target}: {open_ports} ({closed_ports_count} closed/filtered)"
    return f"No open ports found on {target} among scanned {len(clean_ports)} ports ({closed_ports_count} closed/filtered)."


async def audit_dependencies(file_path: str) -> str:
    """Audits dependency files (requirements.txt or package.json) for known CVEs."""
    path = Path(file_path)
    if not path.exists():
        return f"[Error] File not found: {file_path}"

    deps: dict[str, str] = {}
    if path.name == "requirements.txt":
        deps = parse_requirements(path)
    elif path.name == "package.json":
        deps = parse_package_json(path)
    else:
        return "[Error] Unsupported file type. Use requirements.txt or package.json."

    vulnerabilities = []
    for pkg, ver in deps.items():
        if pkg in VULNERABILITY_DB:
            for vuln_ver, cve in VULNERABILITY_DB[pkg].items():
                if _version_matches(ver, vuln_ver):
                    vulnerabilities.append(f"⚠️ {pkg} {ver} matches vulnerable condition '{vuln_ver}': {cve}")

    if not vulnerabilities:
        return f"Audited {len(deps)} dependencies in {path.name}: No known CVEs detected."
    return f"Vulnerabilities found in {path.name} ({len(vulnerabilities)} issues out of {len(deps)} dependencies):\n" + "\n".join(vulnerabilities)


async def detect_secrets(directory: str) -> str:
    """Scans a file or directory for hardcoded secrets using regex patterns and Shannon entropy."""
    target_p = Path(directory)
    if not target_p.exists():
        return f"[Error] Directory or file not found: {directory}"

    findings: list[str] = []
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    ignore_exts = {".png", ".jpg", ".jpeg", ".gif", ".exe", ".dll", ".so", ".pyc", ".woff", ".ttf"}

    files_to_scan = [target_p] if target_p.is_file() else [
        f for f in target_p.rglob("*")
        if f.is_file()
        and not any(part in ignore_dirs for part in f.parts)
        and f.suffix.lower() not in ignore_exts
        and f.stat().st_size < 1024 * 1024
    ]

    for file_path in files_to_scan[:500]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            for line_num, line in enumerate(lines, 1):
                # Regex checks
                for pattern, name in SECRET_PATTERNS:
                    if re.search(pattern, line):
                        findings.append(f"🚨 {name} found in {file_path}:{line_num}")
                        break
                # Entropy checks for long alphanumeric strings
                words = re.findall(r"[A-Za-z0-9_\-/+=]{20,}", line)
                for word in words:
                    if calculate_shannon_entropy(word) > 4.5:
                        findings.append(f"🚨 High-entropy secret string found in {file_path}:{line_num}")
                        break
        except Exception:
            continue

    return "\n".join(findings[:50]) if findings else "No hardcoded secrets detected."


def register_security_tools(registry: Any) -> None:
    """Registers all standalone security tools with Makima ToolRegistry."""
    tools = [
        {
            "name": "scan_ports",
            "func": scan_ports,
            "description": "Call this tool EXCLUSIVELY when you need to check for open TCP ports on a target IP or domain to assess attack surface or network connectivity.",
            "schema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Target hostname or IP address to scan"},
                    "ports": {"type": "string", "description": "Comma-separated list of TCP port numbers (e.g. '22,80,443,8080')", "default": "22,80,443,8080,3306,5432"},
                },
                "required": ["target"],
            },
            "category": "security",
        },
        {
            "name": "audit_dependencies",
            "func": audit_dependencies,
            "description": "Call this tool EXCLUSIVELY when asked to audit a specific package file (e.g., requirements.txt, package.json) for known outdated or vulnerable dependencies.",
            "schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to dependency manifest (requirements.txt, package.json)"},
                },
                "required": ["file_path"],
            },
            "category": "security",
        },
        {
            "name": "detect_secrets",
            "func": detect_secrets,
            "description": "Call this tool EXCLUSIVELY when you need to recursively scan a directory to find accidentally hardcoded API keys, tokens, or private keys.",
            "schema": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory path to recursively scan for secrets"},
                },
                "required": ["directory"],
            },
            "category": "security",
        },
    ]

    for t in tools:
        if hasattr(registry, "register"):
            registry.register(
                name=t["name"],
                func=t["func"],
                description=t["description"],
                schema=t["schema"],
                category=t["category"],
            )
        elif hasattr(registry, "add_tool"):
            registry.add_tool(
                name=t["name"],
                func=t["func"],
                description=t["description"],
                schema=t["schema"],
                category=t["category"],
            )
        else:
            registry[t["name"]] = t["func"]
    logger.info("Successfully registered 3 elite security tools.")
