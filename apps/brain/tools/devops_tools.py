"""
Makima v7.2 — Elite DevOps Tools
Docker management, log parsing, CI/CD status.
"""
from __future__ import annotations
import asyncio
import logging
import json
from typing import Any

logger = logging.getLogger("makima.tools.devops")

try:
    import docker
    _HAS_DOCKER = True
except ImportError:
    _HAS_DOCKER = False

async def docker_ps() -> str:
    if not _HAS_DOCKER: return "[Error] Docker SDK not installed."
    try:
        client = docker.from_env()
        containers = client.containers.list()
        if not containers: return "No running containers."
        
        data = [{"id": c.short_id, "name": c.name, "image": c.image.tags[0] if c.image.tags else "unknown", "status": c.status} for c in containers]
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"[Error] Docker connection failed: {e}"

async def docker_logs(container_id: str, tail: int = 50) -> str:
    if not _HAS_DOCKER: return "[Error] Docker SDK not installed."
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        logs = container.logs(tail=tail).decode("utf-8", errors="ignore")
        return logs
    except Exception as e:
        return f"[Error] Failed to fetch logs: {e}"

async def check_ci_status(repo_url: str = "") -> str:
    # Placeholder for GitHub API integration
    return f"CI/CD Status for {repo_url or 'default repo'}: All pipelines passing (Simulated)."

def register_devops_tools(registry: Any):
    registry.register("docker_ps", docker_ps, "Lists running Docker containers")
    registry.register("docker_logs", docker_logs, "Fetches recent logs for a Docker container")
    registry.register("check_ci_status", check_ci_status, "Checks CI/CD pipeline status")
