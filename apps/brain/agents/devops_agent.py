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
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# --- Graceful Dependency Imports (Zero-Crash Resilience) ---
try:
    import docker
    DOCKER_SDK_AVAILABLE = True
except ImportError:
    DOCKER_SDK_AVAILABLE = False

try:
    from kubernetes import client as k8s_client, config as k8s_config
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

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

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
    AGENT_TOOLS = [
        "docker_ps", "docker_logs", "docker_inspect", "docker_start", "docker_stop", "docker_restart",
        "git_status", "git_diff", "check_ci_status", "system_metrics", "network_check",
        "k8s_get_pods", "k8s_get_logs", "k8s_describe",
    ]
    TAGS = ["devops", "docker", "deploy", "ci", "logs"]

    SYSTEM_PROMPT = """You are Makima's Elite DevOps, SRE, and Infrastructure Management Engine.
You manage infrastructure, Docker containers, Kubernetes clusters, CI/CD pipelines, Git repositories, and system observability.

AVAILABLE TOOLS:
- Docker: docker_ps(), docker_logs(container_id, tail=100), docker_inspect(container_id), docker_start(container_id), docker_stop(container_id), docker_restart(container_id)
- Kubernetes: k8s_get_pods(namespace="default"), k8s_get_logs(pod_name, namespace="default", tail=100), k8s_describe(resource_type, name, namespace="default")
- CI/CD & Git: check_ci_status(owner, repo, branch="main"), git_status(repo_path="."), git_diff(repo_path=".")
- Observability: system_metrics(), network_check(target, method="ping")

OPERATIONAL RULES:
1. Safety First: Never perform destructive infrastructure operations without explicit user confirmation.
2. Observability & Precision: Inspect real container logs, system metrics, and pod states before drawing diagnostic conclusions.
3. Structured Thinking: Always perform concise diagnostic reasoning in <thinking>...</thinking> before selecting tools or synthesizing status.
4. Response Format: Provide actionable, structured status updates with exact error codes, logs, and remediation recommendations.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    _TOOLS = frozenset({
        "docker_ps", "docker_logs", "docker_inspect", "docker_start", "docker_stop", "docker_restart",
        "git_status", "git_diff",
        "k8s_get_pods", "k8s_get_logs", "k8s_describe",
        "check_ci_status", "system_metrics", "network_check"
    })

    # ──────────────────────────────────────────────────────────────────────────
    # Core Execution Loop
    # ──────────────────────────────────────────────────────────────────────────

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """Main execution loop following the BaseAgent contract with unified ReAct execution."""
        self._reset_state()

        # ── P1 Bridge 4B: Consume structured AgentTask parameters directly ────
        agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
        if agent_task and hasattr(agent_task, "operation") and agent_task.operation:
            op = str(agent_task.operation or "").lower().strip()
            params = dict(agent_task.parameters or {})
            
            tool_name = None
            if any(k in op for k in ("docker_ps", "list_containers", "docker_list")):
                tool_name = "docker_ps"
            elif any(k in op for k in ("docker_logs", "container_logs")):
                tool_name = "docker_logs"
            elif any(k in op for k in ("docker_start", "start_container")):
                tool_name = "docker_start"
            elif any(k in op for k in ("docker_stop", "stop_container")):
                tool_name = "docker_stop"
            elif any(k in op for k in ("git_status", "repo_status")):
                tool_name = "git_status"
            elif any(k in op for k in ("git_diff", "diff")):
                tool_name = "git_diff"
            elif any(k in op for k in ("ci", "check_ci", "workflow")):
                tool_name = "check_ci_status"

            if tool_name and tool_name in self._TOOLS:
                logger.info("[devops] P1-4B: Direct execution of tool '%s'", tool_name)
                try:
                    res = await self._use_tool(tool_name, **params)
                    res_str = str(res) if not isinstance(res, (dict, list)) else json.dumps(res, indent=2)
                    self._partial_result = res_str
                    return res_str
                except Exception as ex:
                    logger.warning("[devops] Direct tool '%s' failed, falling back to ReAct: %s", tool_name, ex)

        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=7,
                task_type="devops",
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.error("[devops] SDK error: %s", sdk_exc)
            return f"Task complete nahi hua: {str(sdk_exc)}"

    # ──────────────────────────────────────────────────────────────────────────
    # Tool Dispatcher & Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def __init__(self, ai_handler: Any = None, memory: Any = None, tool_registry: Any = None, ws_broadcast: Any = None, orchestrator: Any = None, guardrails: Any = None, **kwargs: Any) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        def _safe_wrapper(fn):
            async def _wrapped(**kw):
                return await self._safe_call_tool(fn, **kw)
            return _wrapped

        self._TOOL_MAP = {
            tool: _safe_wrapper(getattr(self, f"_tool_{tool}"))
            for tool in self._TOOLS
        }

    async def _safe_call_tool(self, handler: Any, **kwargs: Any) -> str:
        sig = inspect.signature(handler)
        valid_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        result = await handler(**valid_kwargs)
        return result if isinstance(result, str) else json.dumps(result, default=str)

    async def _run_shell_command(self, cmd: List[str], timeout: int = 30) -> Tuple[str, str, int]:
        """Executes a shell command asynchronously with timeout and error handling."""
        try:
            # Prevent shell injection by never using shell=True
            clean_cmd = [str(c) for c in cmd if str(c) not in ("|", ">", "<", "&&")]
            proc = await asyncio.create_subprocess_exec(
                *clean_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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
        return out.strip() or json.dumps({})

    async def _tool_docker_start(self, container_id: str) -> str:
        if not container_id:
            return json.dumps({"error": "container_id is required"})
        cmd = ["docker", "start", container_id]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or f"Failed to start container {container_id}"})
        return json.dumps({"status": "started", "container_id": container_id, "output": out.strip()})

    async def _tool_docker_stop(self, container_id: str) -> str:
        if not container_id:
            return json.dumps({"error": "container_id is required"})
        cmd = ["docker", "stop", container_id]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or f"Failed to stop container {container_id}"})
        return json.dumps({"status": "stopped", "container_id": container_id, "output": out.strip()})

    async def _tool_docker_restart(self, container_id: str) -> str:
        if not container_id:
            return json.dumps({"error": "container_id is required"})
        cmd = ["docker", "restart", container_id]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or f"Failed to restart container {container_id}"})
        return json.dumps({"status": "restarted", "container_id": container_id, "output": out.strip()})

    async def _tool_git_status(self, repo_path: str = ".") -> str:
        cmd = ["git", "-C", repo_path, "status", "--short"]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or "Failed to get git status"})
        return out.strip() or "Working tree clean"

    async def _tool_git_diff(self, repo_path: str = ".") -> str:
        cmd = ["git", "-C", repo_path, "diff"]
        out, err, code = await self._run_shell_command(cmd)
        if code != 0:
            return json.dumps({"error": err or "Failed to get git diff"})
        return out.strip() or "No changes detected"

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
            timeout_obj = aiohttp.ClientTimeout(total=10.0)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                async with session.get(url, headers=headers) as resp:
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
                cpu = await asyncio.to_thread(psutil.cpu_percent, interval=None)
                mem = psutil.virtual_memory()
                root_path = "C:\\" if platform.system() == "Windows" else "/"
                disk = psutil.disk_usage(root_path)
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
            out, _, _ = await self._run_shell_command(["top", "-bn1"])
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
