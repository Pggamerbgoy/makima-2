"""
Makima v7.2 — Elite Security Tools
Port scanning, dependency auditing, secret detection.
"""
from __future__ import annotations
import asyncio
import socket
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("makima.tools.security")

async def scan_ports(target: str, ports: str = "22,80,443,8080,3306,5432") -> str:
    port_list = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
    open_ports = []
    
    loop = asyncio.get_event_loop()
    for port in port_list:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = await loop.run_in_executor(None, sock.connect_ex, (target, port))
            if result == 0:
                open_ports.append(port)
            sock.close()
        except Exception:
            pass
            
    return f"Open ports on {target}: {open_ports}" if open_ports else f"No open ports found on {target} in range {ports}."

async def audit_dependencies(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists(): return f"[Error] File not found: {file_path}"
    
    try:
        content = path.read_text()
        # Simple heuristic for outdated/vulnerable patterns (placeholder for real CVE DB)
        warnings = []
        if "requests==" in content and "requests==2.25" in content:
            warnings.append("⚠️ requests 2.25 has known vulnerabilities. Upgrade to >=2.31.0")
        if "lodash" in content and "4.17.15" in content:
            warnings.append("⚠️ lodash 4.17.15 has prototype pollution vulnerability.")
            
        return "\n".join(warnings) if warnings else "No obvious vulnerable dependency versions detected."
    except Exception as e:
        return f"[Error] Audit failed: {e}"

async def detect_secrets(directory: str) -> str:
    path = Path(directory)
    if not path.is_dir(): return f"[Error] Directory not found: {directory}"
    
    patterns = [
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
        (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
        (r"sk-[a-zA-Z0-9]{48}", "OpenAI API Key"),
        (r"-----BEGIN PRIVATE KEY-----", "Private Key"),
    ]
    
    findings = []
    for file_path in path.rglob("*"):
        if file_path.is_file() and file_path.stat().st_size < 1024 * 1024: # < 1MB
            try:
                content = file_path.read_text(errors="ignore")
                for pattern, name in patterns:
                    if re.search(pattern, content):
                        findings.append(f"🚨 {name} found in {file_path}")
            except Exception:
                pass
                
    return "\n".join(findings) if findings else "No hardcoded secrets detected."

def register_security_tools(registry: Any):
    registry.register("scan_ports", scan_ports, "Scans TCP ports on a target")
    registry.register("audit_dependencies", audit_dependencies, "Audits package files for known CVEs")
    registry.register("detect_secrets", detect_secrets, "Scans directory for hardcoded secrets")
