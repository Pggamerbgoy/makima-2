"""
Makima v7.2 — Elite DevOps Tools
Docker management, log parsing, CI/CD status.
"""
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
    """Check CI/CD pipeline status using GitHub CLI (gh) or git repository inspection."""
    import asyncio
    import shutil

    gh_bin = shutil.which("gh")
    if gh_bin:
        try:
            cmd = [gh_bin, "run", "list", "--limit", "5", "--json", "status,conclusion,name,headBranch,createdAt"]
            if repo_url and "/" in repo_url:
                repo_name = repo_url.rstrip("/").split("/")[-2] + "/" + repo_url.rstrip("/").split("/")[-1].replace(".git", "")
                cmd.extend(["--repo", repo_name])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and stdout:
                runs = json.loads(stdout.decode("utf-8", errors="ignore"))
                if not runs:
                    return "No recent CI/CD workflow runs found on GitHub."
                summary_lines = ["Latest CI/CD Pipeline Runs:"]
                for r in runs:
                    st = r.get("conclusion") or r.get("status") or "unknown"
                    summary_lines.append(f"- {r.get('name')}: {st} on branch {r.get('headBranch')}")
                return "\n".join(summary_lines)
            elif stderr:
                err_text = stderr.decode("utf-8", errors="ignore").strip()
                if "not logged in" in err_text.lower():
                    return "[CI Status] GitHub CLI installed but not authenticated (`gh auth login` required)."
                return f"[CI Status] GitHub CLI check failed: {err_text}"
        except Exception as e:
            return f"[CI Status] Error probing GitHub CLI: {e}"

    # Fallback when gh CLI is not available
    return "[CI Status] GitHub CLI (`gh`) not installed or not in PATH. Please install gh or configure GITHUB_TOKEN for live CI polling."

def register_devops_tools(registry: Any) -> None:
    tools = [
        {
            "name": "docker_ps",
            "func": docker_ps,
            "description": "Call this tool EXCLUSIVELY when you need to check the status, image tags, or IDs of running Docker containers on the local machine.",
            "schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
            "category": "devops",
        },
        {
            "name": "docker_logs",
            "func": docker_logs,
            "description": "Call this tool EXCLUSIVELY when a specific Docker container is failing or you need to inspect its recent stdout/stderr logs. Requires the container ID.",
            "schema": {
                "type": "object",
                "properties": {
                    "container_id": {"type": "string", "description": "Docker container ID or container name"},
                    "tail": {"type": "integer", "description": "Number of recent log lines to retrieve", "default": 50},
                },
                "required": ["container_id"],
            },
            "category": "devops",
        },
        {
            "name": "check_ci_status",
            "func": check_ci_status,
            "description": "Call this tool EXCLUSIVELY to verify if the latest GitHub Actions or GitLab CI/CD pipeline has passed or failed for a given repository.",
            "schema": {
                "type": "object",
                "properties": {
                    "repo_url": {"type": "string", "description": "Repository URL or name to check CI/CD pipeline status", "default": ""},
                },
                "required": [],
            },
            "category": "devops",
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
    logger.info("Successfully registered 3 elite devops tools.")
