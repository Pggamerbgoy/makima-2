"""
Makima v7.2 — Elite Security & Vulnerability Agent
Enterprise-grade security auditing, AST analysis, dependency vulnerability checking,
secret detection, and non-destructive port scanning.
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
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.security")

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

# STUB: In-memory vulnerability database for demo/offline use.
# Production should integrate NVD, PyPI Advisory, or GitHub Advisories.
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

SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"(?i)(aws_secret_access_key|aws_secret_key)\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?", "AWS Secret Key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub Personal Access Token"),
    (r"github_pat_[A-Za-z0-9_]{82}", "GitHub Fine-Grained PAT"),
    (r"sk_live_[A-Za-z0-9]{24,}", "Stripe Live Secret Key"),
    (r"sk_test_[A-Za-z0-9]{24,}", "Stripe Test Secret Key"),
    (r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", "Private Key Block"),
    (r"xox[baprs]-[A-Za-z0-9\-]+", "Slack Token"),
]

LOCAL_PREFIXES = ("127.", "192.168.", "10.", "172.16.", "172.17.", "172.18.", 
                  "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", 
                  "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", 
                  "172.31.", "localhost", "::1", "0.0.0.0")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AST Security Auditor
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SecurityASTVisitor(ast.NodeVisitor):
    """Custom AST visitor to detect insecure coding patterns in Python."""

    def __init__(self) -> None:
        self.findings: List[Dict[str, Any]] = []
        self._current_file: str = "unknown"

    def set_file(self, filename: str) -> None:
        self._current_file = filename

    def _add_finding(self, node: ast.AST, severity: str, category: str, message: str) -> None:
        self.findings.append({
            "file": self._current_file,
            "line": getattr(node, "lineno", 0),
            "col": getattr(node, "col_offset", 0),
            "severity": severity,
            "category": category,
            "message": message
        })

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in DANGEROUS_AST_CALLS:
            self._add_finding(node, "HIGH", "Dangerous Function", 
                              f"Use of dangerous built-in function: `{func_name}()`.")
        
        if func_name == "load" and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "yaml":
                self._add_finding(node, "CRITICAL", "Insecure Deserialization",
                                  "Use of `yaml.load()` without SafeLoader. Use `yaml.safe_load()`.")

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split('.')[0] in DANGEROUS_MODULES:
                self._add_finding(node, "MEDIUM", "Dangerous Import", 
                                  f"Importing sensitive module: `{alias.name}`.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.split('.')[0] in DANGEROUS_MODULES:
            self._add_finding(node, "MEDIUM", "Dangerous Import", 
                              f"Importing from sensitive module: `{node.module}`.")
        self.generic_visit(node)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
        content = file_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"^([a-zA-Z0-9_.-]+)\s*(?:==|>=|<=|~=|!=)\s*([0-9a-zA-Z.*-]+)", line)
            if match:
                deps[match.group(1).lower()] = match.group(2)
    except Exception as e:
        logger.warning(f"Failed to parse requirements.txt: {e}")
    return deps

def parse_package_json(file_path: Path) -> Dict[str, str]:
    """Parses a Node.js package.json file into a dict of {package: version}."""
    deps = {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies"):
            for pkg, ver in data.get(section, {}).items():
                clean_ver = re.sub(r"^[^0-9]*", "", str(ver))
                deps[pkg.lower()] = clean_ver
    except Exception as e:
        logger.warning(f"Failed to parse package.json: {e}")
    return deps

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Elite Security Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SecurityAgent(BaseAgent):
    AGENT_NAME = "security"
    DESCRIPTION = "Enterprise security auditing, AST analysis, dependency CVE checks, secret detection, and port scanning."
    CAPABILITIES = ["vulnerability_scanning", "secret_leak_detection", "sql_injection_audit", "dependency_audit", "input_sanitization"]
    AGENT_TOOLS = ["scan_code", "check_dependencies", "audit_secrets"]
    TAGS = ["security", "audit", "secrets", "injection", "vulnerability"]

    SYSTEM_PROMPT = """You are Makima's Elite Security & Vulnerability Agent.
You perform deep, non-destructive security audits, threat modeling, and code analysis.

━━━ Available Tools ━━━
1. scan_ports(target: str, ports: list[int], timeout: float)
   Scans TCP ports on a target IP/hostname. (Requires confirmation for external IPs).
2. audit_dependencies(file_path: str)
   Parses requirements.txt or package.json and checks for known CVEs.
3. detect_secrets(directory: str)
   Scans a directory recursively for hardcoded secrets using regex and entropy analysis.
4. audit_ast(file_path: str)
   Performs Python AST analysis to find dangerous calls, insecure deserialization, and imports.
5. analyze_threat_model(code_snippet: str, language: str)
   Analyzes a code snippet for OWASP Top 10 vulnerabilities (SQLi, XSS, RCE, etc.).

━━━ Rules ━━━
1. NEVER perform destructive actions, exploitation, or DDoS.
2. Always confirm before scanning external/public IPs.
3. Provide actionable, prioritized remediation steps for every finding.
4. Respond with EXACTLY ONE valid JSON object per turn.
"""

    _TOOLS = frozenset({
        "scan_ports", "audit_dependencies", "detect_secrets", 
        "audit_ast", "analyze_threat_model"
    })

    def __init__(
        self,
        ai_handler: Any = None,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Any = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        **kwargs: Any
    ) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails)
        self._port_semaphore = asyncio.Semaphore(50)  # Limit concurrent port scans
        self._file_semaphore = asyncio.Semaphore(20)  # Limit concurrent file reads

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """Main execution loop adhering to the BaseAgent contract."""
        self._reset_state()
        messages = self._build_messages(message, context)
        
        for step in range(6):  # Max 6 reasoning steps for deep analysis
            try:
                raw = await self._llm_call(messages, task="security", require_json=True, temperature=0.1)
                parsed = self.ai_handler.try_parse_json(raw)
                
                if not parsed:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": "[SYSTEM] Invalid JSON format. Respond with valid JSON."})
                    continue

                tool = parsed.get("tool")
                if not tool:
                    return parsed.get("reply", "Security audit complete. No further actions required.")
                
                if tool not in self._TOOLS:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"[ERROR] Unknown tool '{tool}'. Available: {list(self._TOOLS)}"})
                    continue

                params = parsed.get("params", {})
                
                # Enforce confirmation for external port scans
                if tool == "scan_ports" and not self._is_local_target(params.get("target", "")):
                    confirmed = await self._confirm_action(
                        task_id, "security_scan", 
                        f"Scan external target: {params.get('target')}?", "high"
                    )
                    if not confirmed:
                        return json.dumps({"status": "cancelled", "reason": "External port scan denied by user."})

                # Execute the selected tool
                result = await self._route_tool(tool, params)
                
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"[TOOL RESULT: {tool}]\n{json.dumps(result, default=str)}"})

            except Exception as e:
                logger.exception(f"SecurityAgent execution error at step {step}: {e}")
                messages.append({"role": "user", "content": f"[SYSTEM ERROR] {str(e)}. Recover and continue."})

        return json.dumps({"status": "max_steps_reached", "message": "Maximum security audit reasoning steps reached."})

    async def _route_tool(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Routes tool calls to their respective async implementations with zero-crash resilience."""
        try:
            if tool == "scan_ports":
                return await self._scan_ports(params.get("target", ""), params.get("ports", []), params.get("timeout", 2.0))
            elif tool == "audit_dependencies":
                return await self._audit_dependencies(params.get("file_path", ""))
            elif tool == "detect_secrets":
                return await self._detect_secrets(params.get("directory", "."))
            elif tool == "audit_ast":
                return await self._audit_ast(params.get("file_path", ""))
            elif tool == "analyze_threat_model":
                return await self._analyze_threat_model(params.get("code_snippet", ""), params.get("language", "python"))
            return {"error": f"Tool {tool} not implemented."}
        except Exception as e:
            logger.error(f"Tool execution failed for {tool}: {e}")
            return {"error": f"Execution failed: {str(e)}"}

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tool Implementations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _scan_ports(self, target: str, ports: List[int], timeout: float) -> Dict[str, Any]:
        """Asynchronously scans TCP ports on a target."""
        # Re-validation guard: reject non-local targets at the method level.
        if not self._is_local_target(target):
            logger.warning(f"Port scan blocked for non-local target: {target}")
            return {"error": "Port scanning restricted to local/internal targets only."}
        if not target or not ports:
            return {"error": "Target and ports are required."}
        
        open_ports = []
        closed_ports = []

        async def scan_single(port: int) -> None:
            async with self._port_semaphore:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(target, port), 
                        timeout=timeout
                    )
                    writer.close()
                    await writer.wait_closed()
                    open_ports.append(port)
                except (asyncio.TimeoutError, ConnectionRefusedError, socket.gaierror, OSError):
                    closed_ports.append(port)

        tasks = [scan_single(p) for p in ports if isinstance(p, int)]
        await asyncio.gather(*tasks, return_exceptions=True)

        return {
            "target": target,
            "open_ports": sorted(open_ports),
            "closed_ports_count": len(closed_ports),
            "status": "completed"
        }

    async def _audit_dependencies(self, file_path: str) -> Dict[str, Any]:
        """Audits dependency files for known CVEs."""
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        deps = {}
        if path.name == "requirements.txt":
            deps = parse_requirements(path)
        elif path.name == "package.json":
            deps = parse_package_json(path)
        else:
            return {"error": "Unsupported file type. Use requirements.txt or package.json."}

        vulnerabilities = []
        for pkg, ver in deps.items():
            if pkg in VULNERABILITY_DB:
                for vuln_ver, cve in VULNERABILITY_DB[pkg].items():
                    # Simplified version check (assumes exact match or prefix for simulation)
                    clean_vuln = vuln_ver.lstrip("<>=!~")
                    if ver.startswith(clean_vuln) or ver < clean_vuln:
                        vulnerabilities.append({
                            "package": pkg,
                            "installed_version": ver,
                            "vulnerable_condition": vuln_ver,
                            "cve": cve
                        })

        return {
            "file": file_path,
            "total_dependencies": len(deps),
            "vulnerabilities_found": len(vulnerabilities),
            "details": vulnerabilities
        }

    async def _detect_secrets(self, directory: str) -> Dict[str, Any]:
        """Scans a directory for hardcoded secrets using regex and entropy."""
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return {"error": f"Directory not found: {directory}"}

        findings = []
        ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
        ignore_exts = {".png", ".jpg", ".jpeg", ".gif", ".exe", ".dll", ".so", ".pyc", ".woff", ".ttf"}

        async def scan_file(file_path: Path) -> None:
            async with self._file_semaphore:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    lines = content.splitlines()
                    
                    for line_num, line in enumerate(lines, 1):
                        # Regex checks
                        for pattern, name in SECRET_PATTERNS:
                            if re.search(pattern, line):
                                findings.append({
                                    "file": str(file_path),
                                    "line": line_num,
                                    "type": name,
                                    "severity": "CRITICAL"
                                })
                        
                        # Entropy checks for long alphanumeric strings
                        words = re.findall(r"[A-Za-z0-9_\-/+=]{20,}", line)
                        for word in words:
                            if calculate_shannon_entropy(word) > 4.5:
                                findings.append({
                                    "file": str(file_path),
                                    "line": line_num,
                                    "type": "High Entropy String (Potential Secret)",
                                    "severity": "HIGH",
                                    "snippet": word[:10] + "..."
                                })
                except Exception as e:
                    logger.debug(f"Could not read {file_path}: {e}")

        tasks = []
        for f in dir_path.rglob("*"):
            if f.is_file() and f.suffix.lower() not in ignore_exts:
                if not any(part in ignore_dirs for part in f.parts):
                    tasks.append(scan_file(f))
            
            if len(tasks) > 500:  # Cap to prevent memory exhaustion on massive repos
                logger.warning(
                    f"Secret scan truncated in {directory!r}: reached 500-file cap. "
                    "Results are partial and may miss secrets in remaining files."
                )
                break

        await asyncio.gather(*tasks, return_exceptions=True)

        return {
            "directory": directory,
            "secrets_found": len(findings),
            "details": findings[:50]  # Limit output size
        }

    async def _audit_ast(self, file_path: str) -> Dict[str, Any]:
        """Performs Python AST analysis for insecure patterns."""
        path = Path(file_path)
        if not path.exists() or path.suffix != ".py":
            return {"error": "File not found or not a Python file."}

        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            return {"error": f"Syntax error in file: {e}"}
        except Exception as e:
            return {"error": f"Failed to parse AST: {e}"}

        visitor = SecurityASTVisitor()
        visitor.set_file(str(path))
        visitor.visit(tree)

        return {
            "file": file_path,
            "issues_found": len(visitor.findings),
            "details": visitor.findings
        }

    async def _analyze_threat_model(self, code_snippet: str, language: str) -> Dict[str, Any]:
        """Analyzes code snippets for OWASP vulnerabilities using heuristic patterns."""
        if not code_snippet:
            return {"error": "Code snippet is empty."}

        findings = []
        lang = language.lower()

        # SQL Injection patterns
        if re.search(r"(?i)(execute|query|raw)\s*\(\s*f['\"].*\{", code_snippet) or \
           re.search(r"(?i)(execute|query|raw)\s*\(\s*['\"].*\+\s*\w+", code_snippet):
            findings.append({"severity": "CRITICAL", "category": "SQL Injection", 
                             "message": "String concatenation or f-strings used in SQL queries. Use parameterized queries."})

        # Command Injection patterns
        if re.search(r"(?i)(os\.system|subprocess\.(call|run|Popen|check_output))\s*\(.*\+", code_snippet) or \
           re.search(r"(?i)subprocess\..*shell\s*=\s*True", code_snippet):
            findings.append({"severity": "CRITICAL", "category": "Command Injection", 
                             "message": "Dynamic input passed to shell command. Use array arguments and avoid shell=True."})

        # XSS patterns (JS/HTML context)
        if lang in ("js", "javascript", "html", "ts", "typescript"):
            if re.search(r"(?i)innerHTML\s*=", code_snippet) or re.search(r"(?i)document\.write", code_snippet):
                findings.append({"severity": "HIGH", "category": "Cross-Site Scripting (XSS)", 
                                 "message": "Direct DOM manipulation with innerHTML or document.write. Use textContent or sanitization."})

        # Path Traversal
        if re.search(r"(?i)(open|readFile|readFileSync)\s*\(.*\+", code_snippet):
            findings.append({"severity": "HIGH", "category": "Path Traversal", 
                             "message": "Dynamic file path construction. Validate and sanitize paths, use path.join safely."})

        return {
            "language": language,
            "vulnerabilities_found": len(findings),
            "details": findings
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Utility Methods
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _is_local_target(target: str) -> bool:
        """Checks if a target IP/hostname is within local/private ranges."""
        if not target:
            return False
        return any(target.startswith(p) for p in LOCAL_PREFIXES)
