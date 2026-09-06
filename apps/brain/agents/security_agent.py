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
from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.security")


from ..tools.security_tools import (
    _version_tuple,
    _version_matches,
    DANGEROUS_AST_CALLS,
    DANGEROUS_MODULES,
    VULNERABILITY_DB,
    SECRET_PATTERNS,
    LOCAL_PREFIXES,
    calculate_shannon_entropy,
    parse_requirements,
    parse_package_json,
)

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
# Elite Security Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SecurityAgent(BaseAgent):
    AGENT_NAME = "security"
    DESCRIPTION = "Enterprise security auditing, AST analysis, dependency CVE checks, secret detection, and port scanning."
    CAPABILITIES = ["vulnerability_scanning", "secret_leak_detection", "sql_injection_audit", "dependency_audit", "input_sanitization"]
    AGENT_TOOLS = ["scan_ports", "audit_dependencies", "detect_secrets", "audit_ast", "analyze_threat_model"]
    TAGS = ["security", "audit", "secrets", "injection", "vulnerability"]

    SYSTEM_PROMPT = """You are Makima's Elite Security & Vulnerability Agent.
You perform deep, non-destructive security audits, threat modeling, dependency CVE checks, static AST code analysis, and secret leak detection.

AVAILABLE TOOLS:
- Port Scanning: scan_ports(target="127.0.0.1", ports=[...], timeout=2.0) [Non-destructive port inspection]
- Dependency Audit: audit_dependencies(file_path) [checks known CVEs against requirements.txt or package.json]
- Secret Detection: detect_secrets(directory=".") [Shannon entropy & pattern scan for leaked API keys, tokens, private keys]
- Static AST Audit: audit_ast(file_path) [detects dangerous calls (eval, exec), unsafe deserialization, SQL injection]
- Threat Modeling: analyze_threat_model(code_snippet, language="python") [OWASP Top 10 threat modeling & architecture review]

RULES & SAFETY:
1. Non-Destructive: NEVER perform active exploitation, DDoS, brute-forcing, or destructive payloads.
2. Structured Thinking: Always perform step-by-step security reasoning in <thinking>...</thinking> before selecting audit tools or synthesizing reports.
3. Severity Ranking: Deliver actionable, severity-ranked findings (CRITICAL, HIGH, MEDIUM, LOW) with concrete remediation code diffs.""" + "\n" + TOOL_DISCIPLINE_BLOCK

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
        self._TOOL_MAP = {
            "scan_ports": self._tool_scan_ports,
            "audit_dependencies": self._tool_audit_dependencies,
            "detect_secrets": self._tool_detect_secrets,
            "audit_ast": self._tool_audit_ast,
            "analyze_threat_model": self._tool_analyze_threat_model,
        }

    async def _tool_scan_ports(self, target: str = "127.0.0.1", ports: list = None, timeout: float = 2.0, _user_approved_external: bool = False, **kwargs: Any) -> dict[str, Any]:
        return await self._scan_ports(target=target, ports=ports or [], timeout=timeout, _user_approved_external=_user_approved_external)

    async def _tool_audit_dependencies(self, file_path: str = "", **kwargs: Any) -> dict[str, Any]:
        return await self._audit_dependencies(file_path=file_path)

    async def _tool_detect_secrets(self, directory: str = ".", target_path: Optional[str] = None, path: Optional[str] = None, file_path: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        target = target_path or file_path or path or directory
        return await self._detect_secrets(directory=target)

    async def _tool_audit_ast(self, file_path: str = "", **kwargs: Any) -> dict[str, Any]:
        return await self._audit_ast(file_path=file_path)

    async def _tool_analyze_threat_model(self, code_snippet: str = "", language: str = "python", **kwargs: Any) -> dict[str, Any]:
        return await self._analyze_threat_model(code_snippet=code_snippet, language=language)

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """Main execution loop adhering to the BaseAgent contract with unified ReAct execution."""
        self._reset_state()

        # ── P1 Bridge 4B: Consume structured AgentTask parameters directly ────
        agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
        if agent_task and hasattr(agent_task, "operation") and agent_task.operation:
            op = str(agent_task.operation or "").lower().strip()
            params = dict(agent_task.parameters or {})
            
            tool_name = None
            if any(k in op for k in ("scan_port", "port_scan", "scan")):
                tool_name = "scan_ports"
            elif any(k in op for k in ("audit_dep", "dependency", "dependencies")):
                tool_name = "audit_dependencies"
            elif any(k in op for k in ("secret", "leak", "check_secret", "detect_secret")):
                tool_name = "detect_secrets"
            elif any(k in op for k in ("ast", "audit_ast", "static")):
                tool_name = "audit_ast"
            elif any(k in op for k in ("threat", "threat_model")):
                tool_name = "analyze_threat_model"

            if tool_name and tool_name in self._TOOLS:
                logger.info("[security] P1-4B: Direct execution of tool '%s'", tool_name)
                try:
                    res = await self._use_tool(tool_name, context=context, **params)
                    res_str = str(res) if not isinstance(res, (dict, list)) else json.dumps(res, indent=2)
                    self._partial_result = res_str
                    return res_str
                except Exception as ex:
                    logger.warning("[security] Direct tool '%s' failed, falling back to ReAct: %s", tool_name, ex)

        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=6,
                task_type="security",
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.error("[security] SDK error: %s", sdk_exc)
            return f"Task complete nahi hua: {str(sdk_exc)}"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tool Implementations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _scan_ports(self, target: str, ports: List[int], timeout: float,
                          _user_approved_external: bool = False) -> Dict[str, Any]:
        """Asynchronously scans TCP ports on a target."""
        # Re-validation guard: reject non-local targets unless user explicitly approved.
        if not _user_approved_external and not self._is_local_target(target):
            logger.warning(f"Port scan blocked for non-local target: {target}")
            return {"error": "Port scanning restricted to local/internal targets only. User confirmation required for external targets."}
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

        clean_ports: list[int] = []
        for p in (ports or []):
            try:
                p_int = int(p)
                if 1 <= p_int <= 65535:
                    clean_ports.append(p_int)
            except (ValueError, TypeError):
                continue

        tasks = [scan_single(p) for p in clean_ports]
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
                    if _version_matches(ver, vuln_ver):
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
        """Scans a file or directory for hardcoded secrets using regex and entropy."""
        target_p = Path(directory)
        if not target_p.exists():
            return {"error": f"Target not found: {directory}"}

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
        if target_p.is_file():
            tasks.append(scan_file(target_p))
        else:
            for f in target_p.rglob("*"):
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
            "target": str(target_p),
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
