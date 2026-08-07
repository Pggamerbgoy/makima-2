"""
Makima v7.2 — Elite DevOps Agent
Enterprise-grade infrastructure management, Docker/K8s orchestration, CI/CD pipelines, and observability.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import platform
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

# --- Graceful Dependency Imports (Zero-Crash Resilience) ---
try:
    import docker
    DOCKER_SDK_AVAILABLE = True
except ImportError:
    DOCKER_SDK_AVAILABLE = False

try:
    from kubernetes import client as k8s_client, config as k8s_config
    from kubernetes.client.exceptions import ApiException
    K8S_SDK_AVAILABLE = True
except ImportError:
    K8S_SDK_AVAILABLE = False

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from .base_agent import BaseAgent

logger = logging.getLogger("makima.agents.devops")


@dataclass
class ToolResult:
    """Standardized result container for tool executions."""
    success: bool
    data: Any = None
    error: Optional[str] = None


class DevOpsAgent(BaseAgent):
    """
    Elite DevOps, SRE, and Infrastructure Management Engine.
    Handles Docker, Kubernetes, CI/CD pipelines, system metrics, and network diagnostics.
    """
    
    AGENT_NAME = "devops"
    DESCRIPTION = "Elite DevOps, SRE, and Infrastructure Management Engine"
    CAPABILITIES = ["docker_management", "cicd_automation", "deployment_diagnostics", "environment_config"]
    AGENT_TOOLS = ["docker_ps", "docker_logs", "docker_restart", "check_ci_status"]
    TAGS = ["devops", "docker", "deploy", "ci", "logs"]

    SYSTEM_PROMPT = """You are Makima's Elite DevOps & Infrastructure Agent.
You are an enterprise-grade SRE and DevOps engineer managing local/remote infrastructure, Docker containers, 
Kubernetes clusters, CI/CD pipelines, and system observability.

━━━ Available Tools ━━━
1. docker_ps() -> Lists running Docker containers.
2. docker_logs(container_id: str, tail: int = 100) -> Fetches recent logs for a container.
3. docker_inspect(container_id: str) -> Inspects container configuration and state.
4. k8s_get_pods(namespace: str = "default") -> Lists pods in a Kubernetes namespace.
5. k8s_get_logs(pod_name: str, namespace: str = "default", tail: int = 100) -> Fetches pod logs.
6. k8s_describe(resource_type: str, name: str, namespace: str = "default") -> Describes a K8s resource.
7. check_ci_status(owner: str, repo: str, branch: str = "main") -> Checks GitHub Actions pipeline status.
8. system_metrics() -> Retrieves CPU, Memory, and Disk usage metrics.
9. network_check(target: str, method: str = "ping") -> Checks network connectivity (ping/curl).

━━━ Operational Rules ━━━
1. SAFETY FIRST: Never execute destructive commands (rm, kill, delete, drop) without explicit user confirmation.
2. OBSERVABILITY: When diagnosing issues, always gather metrics and logs before hypothesizing.
3. EFFICIENCY: Use the most direct tool for the job. Don't fetch 10,000 lines of logs; use `tail`.
4. RESPONSE FORMAT: You must respond with EXACTLY ONE valid JSON object per turn. No markdown formatting outside the JSON.

━━━ JSON Schema ━━━
{
  "thought": "Your step-by-step reasoning and analysis.",
  "tool": "tool_name" | null,
  "params": { "arg1": "value1" },
  "reply": "Final message to the user (only if tool is null)."
}
"""

    _TOOLS = frozenset({
        "docker_ps", "docker_logs", "docker_inspect",
        "k8s_get_pods", "k8s_get_logs", "k8s_describe",
        "check_ci_status", "system_metrics", "network_check"
    })

    # ──────────────────────────────────────────────────────────────────────────
    # Core Execution Loop
    # ──────────────────────────────────────────────────────────────────────────

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """Main execution loop following the BaseAgent contract."""
        self._reset_state()
        messages = self._build_messages(message, context)
        
        max_iterations = 7
        for iteration in range(max_iterations):
            raw = await self._llm_call(messages, task="devops", require_json=True, temperature=0.1)
            parsed = self.ai_handler.try_parse_json(raw)
            
            if not parsed:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "[SYSTEM] Invalid JSON format. Please provide a valid JSON object."})
                continue

            thought = parsed.get("thought", "")
            tool = parsed.get("tool")
            params = parsed.get("params", {})
            reply = parsed.get("reply")

            if thought:
                logger.info(f"[DevOpsAgent Thought] {thought}")

            if not tool:
                return reply or "DevOps task complete."
                
            if tool not in self._TOOLS:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"[ERROR] Unknown tool '{tool}'. Available: {list(self._TOOLS)}"})
                continue

            logger.info(f"[DevOpsAgent] Executing tool: {tool} | params: {params}")
            res = await self._use_tool(tool, **params)
            
            # Truncate massive outputs to prevent context window overflow
            if len(res) > 8000:
                res = res[:8000] + "\n... [TRUNCATED DUE TO LENGTH] ..."
                
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"[TOOL RESULT: {tool}]\n{res}"})

        return "Max DevOps reasoning steps reached. Please refine the request."

    # ──────────────────────────────────────────────────────────────────────────
    # Tool Dispatcher & Helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _use_tool(self, tool_name: str, **kwargs: Any) -> str:
        """Dispatches tool execution to the appropriate handler with error isolation."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return json.dumps({"error": f"Tool handler for '{tool_name}' not found."})
        
        try:
            sig = inspect.signature(handler)
            valid_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            result = await handler(**valid_kwargs)
            return result if isinstance(result, str) else json.dumps(result, default=str)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return json.dumps({"error": f"Execution failed: {str(e)}"})

    async def _run_shell_command(self, cmd: List[str], timeout: int = 30) -> Tuple[str, str, int]:
        """Executes a shell command asynchronously with timeout and error handling."""
        try:
            use_shell = any(c in cmd for c in ["|", ">", "<", "&&"])
            
            if use_shell:
                shell_cmd = " ".join(cmd)
                proc = await asyncio.create_subprocess_shell(
                    shell_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore'), proc.returncode or 0
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return "", "Command timed out", -1
        except Exception as e:
            return "", f"Failed to execute command: {str(e)}", -1

    # ──────────────────────────────────────────────────────────────────────────
    # Docker Tools
    # ──────────────────────────────────────────────────────────────────────────

    async def _tool_docker_ps(self) -> str:
        if DOCKER_SDK_AVAILABLE:
            try:
                client = docker.from_env()
                containers = client.containers.list()
                data = [{"id": c.short_id, "name": c.name, "image": c.image.tags, "status": c.status} for c in containers]
                return json.dumps(data)
            except Exception as e:
                logger.warning(f"Docker SDK failed, falling back to CLI: {e}")
        
        cmd = ["docker", "ps", "--format", "{{json .}}"]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or "Failed to list containers"})
        containers = [json.loads(line) for line in out.strip().split('\n') if line]
        return json.dumps(containers)

    async def _tool_docker_logs(self, container_id: str, tail: int = 100) -> str:
        if not container_id:
            return json.dumps({"error": "container_id is required"})
        cmd = ["docker", "logs", "--tail", str(tail), container_id]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0 and not out:
            return json.dumps({"error": err or "Failed to fetch logs"})
        return out

    async def _tool_docker_inspect(self, container_id: str) -> str:
        if not container_id:
            return json.dumps({"error": "container_id is required"})
        cmd = ["docker", "inspect", container_id]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or "Failed to inspect container"})
        return out

    # ──────────────────────────────────────────────────────────────────────────
    # Kubernetes Tools
    # ──────────────────────────────────────────────────────────────────────────

    async def _tool_k8s_get_pods(self, namespace: str = "default") -> str:
        if K8S_SDK_AVAILABLE:
            try:
                k8s_config.load_kube_config()
                v1 = k8s_client.CoreV1Api()
                pods = v1.list_namespaced_pod(namespace=namespace)
                data = [{
                    "name": p.metadata.name, "status": p.status.phase,
                    "restarts": sum(c.restart_count for c in (p.status.container_statuses or [])),
                    "age": str(p.metadata.creation_timestamp)
                } for p in pods.items]
                return json.dumps(data)
            except Exception as e:
                logger.warning(f"K8s SDK failed, falling back to kubectl: {e}")
        
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or "Failed to get pods"})
        return out

    async def _tool_k8s_get_logs(self, pod_name: str, namespace: str = "default", tail: int = 100) -> str:
        if not pod_name:
            return json.dumps({"error": "pod_name is required"})
        cmd = ["kubectl", "logs", pod_name, "-n", namespace, "--tail", str(tail)]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0 and not out:
            return json.dumps({"error": err or "Failed to fetch pod logs"})
        return out

    async def _tool_k8s_describe(self, resource_type: str, name: str, namespace: str = "default") -> str:
        if not resource_type or not name:
            return json.dumps({"error": "resource_type and name are required"})
        cmd = ["kubectl", "describe", resource_type, name, "-n", namespace]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or "Failed to describe resource"})
        return out

    # ──────────────────────────────────────────────────────────────────────────
    # CI/CD & Infrastructure Tools
    # ──────────────────────────────────────────────────────────────────────────

    async def _tool_check_ci_status(self, owner: str, repo: str, branch: str = "main") -> str:
        if not AIOHTTP_AVAILABLE:
            return json.dumps({"error": "aiohttp is not installed. Cannot check CI status."})
        if not owner or not repo:
            return json.dumps({"error": "owner and repo are required"})
        
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs?branch={branch}&per_page=5"
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        return json.dumps({"error": f"GitHub API returned status {resp.status}"})
                    data = await resp.json()
                    runs = [{
                        "id": r["id"], "status": r["status"], "conclusion": r["conclusion"],
                        "created_at": r["created_at"], "head_branch": r["head_branch"]
                    } for r in data.get("workflow_runs", [])]
                    return json.dumps(runs)
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch CI status: {str(e)}"})

    async def _tool_system_metrics(self) -> str:
        if PSUTIL_AVAILABLE:
            try:
                cpu = psutil.cpu_percent(interval=1)
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                return json.dumps({
                    "cpu_percent": cpu,
                    "memory_total_mb": round(mem.total / (1024**2), 2),
                    "memory_used_mb": round(mem.used / (1024**2), 2),
                    "memory_percent": mem.percent,
                    "disk_total_gb": round(disk.total / (1024**3), 2),
                    "disk_used_gb": round(disk.used / (1024**3), 2),
                    "disk_percent": disk.percent
                })
            except Exception as e:
                logger.warning(f"psutil failed: {e}")
        
        metrics = {}
        if platform.system() == "Linux":
            out, _, _ = await self._run_shell_command(["top", "-bn1", "|", "grep", "Cpu(s)"])
            metrics["cpu_raw"] = out.strip()
            out, _, _ = await self._run_shell_command(["free", "-m"])
            metrics["memory_raw"] = out.strip()
            out, _, _ = await self._run_shell_command(["df", "-h", "/"])
            metrics["disk_raw"] = out.strip()
        else:
            metrics["note"] = "Advanced metrics require Linux or psutil."
        return json.dumps(metrics)

    async def _tool_network_check(self, target: str, method: str = "ping") -> str:
        if not target:
            return json.dumps({"error": "target is required"})
        
        if not re.match(r'^[a-zA-Z0-9.\-_:\/]+$', target):
            return json.dumps({"error": "Invalid target format. Potential injection detected."})
            
        if method == "ping":
            cmd = ["ping", "-c", "4", target] if platform.system() != "Windows" else ["ping", "-n", "4", target]
        elif method == "curl":
            cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{time_total}s", target]
        else:
            return json.dumps({"error": "Unsupported method. Use 'ping' or 'curl'."})
            
        out, err, code = await self._run_shell_command(cmd)
        return json.dumps({"target": target, "method": method, "exit_code": code, "output": out, "error": err})

    async def _run_shell_command_safe(self, cmd: List[str], timeout: int = 30) -> Tuple[str, str, int]:
        """Executes a shell command asynchronously with timeout and error handling using shlex.split for safety."""
        try:
            safe_cmd = shlex.split(" ".join(cmd))
            proc = await asyncio.create_subprocess_exec(
                *safe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return stdout.decode('utf-8', errors='ignore'), stderr.decode('utf-8', errors='ignore'), proc.returncode or 0
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return "", "Command timed out", -1
        except Exception as e:
            return "", f"Failed to execute command: {str(e)}", -1
