"""
Ruflo Swarm Bridge — Master Zero-API-Key Native Orchestration Helper & CLI Toolkit
Coordinates Ruflo MCP local engine (Swarm, Raft Consensus, Vector Memory, Static Analysis, AI Defence)
with Antigravity Gemini subagents.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.analytics import CodeAnalytics
from modules.security import SecurityEngine
from modules.memory_graph import MemoryGraphHelper
from modules.workflows import WorkflowEngine

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("ruflo_bridge")

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
AGENTS_DIR = BASE_DIR / "agents"
CATALOG_DIR = BASE_DIR / "catalog"
REGISTRY_DIR = BASE_DIR / "registry"
MCP_DIR = BASE_DIR / "mcp"
SKILLS_DIR = BASE_DIR / "skills"
AGENT_ALIASES: Dict[str, str] = {
    "security-architect": "security-manager",
    "security-auditor": "ruflo-security-auditor",
    "architect": "architecture",
    "analyst": "code-analyzer",
    "docs-api-openapi": "api-docs",
}


class RufloBridge:
    """
    Master bridge connecting Ruflo local toolchain and Antigravity execution workers.
    """

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self.analytics = CodeAnalytics(self.workspace_root)
        self.security = SecurityEngine()
        self.memory_graph = MemoryGraphHelper()
        self.workflows = WorkflowEngine()

    def analyze_boundaries(self, target_path: str) -> Dict[str, Any]:
        """Run MinCut graph boundary analysis on target file or directory."""
        return self.run_ruflo_command(["analyze", "boundaries", target_path])

    def get_catalog(self) -> Dict[str, Any]:
        """Load agents catalog JSON registry."""
        cat_file = REGISTRY_DIR / "agents_catalog.json"
        if cat_file.exists():
            try:
                return json.loads(cat_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Failed to read catalog: {e}")
        return {"total_agents": 0, "agents": {}}

    def get_agent_info(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Look up metadata and dependencies for an agent by identifier."""
        catalog = self.get_catalog().get("agents", {})
        clean = agent_id.strip().lower()
        clean = AGENT_ALIASES.get(clean, clean)
        for aid, info in catalog.items():
            if aid.lower() == clean or aid.lower().replace("-", "_") == clean.replace("-", "_"):
                return info
        return None

    def get_agent_prompt(self, agent_name: str) -> str:
        """Load markdown system prompt for a specific swarm or catalog agent (fail-closed)."""
        if not isinstance(agent_name, str):
            raise TypeError(f"agent_name must be str, got {type(agent_name).__name__}")
        raw = agent_name.strip()
        if not raw or raw.lower() in (".md", "ruflo-", "ruflo"):
            available = [f.stem for f in AGENTS_DIR.glob("*.md")]
            raise FileNotFoundError(f"Agent prompt '{agent_name}' not found. Available: {available}")
        
        # Apply alias resolution if needed
        clean_raw = raw.lower().strip()
        clean_raw = AGENT_ALIASES.get(clean_raw, clean_raw)

        clean_name = (
            clean_raw
            .replace(".md", "")
            .replace("ruflo-", "")
            .replace("-", "_")
            .strip("._")
        )
        # Block path traversal / separators after normalization.
        if not clean_name or any(sep in clean_name for sep in ("/", "\\", "..")):
            available = [f.stem for f in AGENTS_DIR.glob("*.md")]
            raise FileNotFoundError(f"Agent prompt '{agent_name}' not found. Available: {available}")

        # 1. Check in custom AGENTS_DIR
        prompt_file = (AGENTS_DIR / f"{clean_name}.md").resolve()
        if prompt_file.is_file():
            try:
                prompt_file.relative_to(AGENTS_DIR.resolve())
                return prompt_file.read_text(encoding="utf-8")
            except ValueError:
                pass

        # 2. Check catalog registry for path
        info = self.get_agent_info(raw)
        if info and "relative_path" in info:
            rel = info["relative_path"]
            cat_prompt = (CATALOG_DIR / rel).resolve()
            if cat_prompt.is_file():
                return cat_prompt.read_text(encoding="utf-8")
            alt_prompt = (REGISTRY_DIR / rel).resolve()
            if alt_prompt.is_file():
                return alt_prompt.read_text(encoding="utf-8")

        # 3. Direct match in CATALOG_DIR
        matches = list(CATALOG_DIR.rglob(f"{clean_name.replace('_', '-')}.md")) or list(CATALOG_DIR.rglob(f"{clean_name}.md"))
        if matches and matches[0].is_file():
            return matches[0].read_text(encoding="utf-8")

        # 4. Fail-closed fuzzy fallback: escaped glob
        import glob as _glob
        pattern = str(AGENTS_DIR / f"*{_glob.escape(clean_name)}*.md")
        local_matches = sorted(Path(p) for p in _glob.glob(pattern))
        local_matches = [m for m in local_matches if m.is_file()]
        if local_matches:
            return local_matches[0].read_text(encoding="utf-8")

        available = [f.stem for f in AGENTS_DIR.glob("*.md")]
        raise FileNotFoundError(f"Agent prompt '{agent_name}' not found. Available local: {available}")

    def run_ruflo_command(self, args: List[str], timeout: int = 45) -> Dict[str, Any]:
        """Execute a local ruflo CLI command without requiring an LLM API key."""
        cmd = ["ruflo.cmd"] + args if sys.platform == "win32" else ["ruflo"] + args
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    def analyze_complexity(self, target_path: str, threshold: int = 10) -> Dict[str, Any]:
        return self.analytics.complexity(target_path, threshold)

    def analyze_symbols(self, target_path: str) -> Dict[str, Any]:
        return self.analytics.symbols(target_path)

    def get_swarm_status(self) -> Dict[str, Any]:
        return self.run_ruflo_command(["status"])

    def scan_file_for_pii(self, file_path: str) -> Dict[str, Any]:
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        content = p.read_text(encoding="utf-8", errors="ignore")
        return self.security.scan_text(content)

    def get_swarm_agents(self) -> List[Dict[str, str]]:
        """List all active agent roles in this zero-key swarm."""
        return [
            {
                "id": "ruflo-orchestrator-lead",
                "role": "orchestrator",
                "file": "agents/orchestrator_lead.md",
                "description": "Macro-orchestration and Raft consensus synthesis",
            },
            {
                "id": "ruflo-arch-auditor",
                "role": "architect",
                "file": "agents/arch_auditor.md",
                "description": "Concurrency, GC, locks, and lifecycle audit",
            },
            {
                "id": "ruflo-code-auditor",
                "role": "code-reviewer",
                "file": "agents/code_auditor.md",
                "description": "5-point adversarial line-by-line inspection",
            },
            {
                "id": "ruflo-security-auditor",
                "role": "security-reviewer",
                "file": "agents/security_auditor.md",
                "description": "PII scrubbing, secret detection & threat modeling",
            },
            {
                "id": "ruflo-perf-auditor",
                "role": "performance-engineer",
                "file": "agents/perf_auditor.md",
                "description": "AST complexity, bottleneck analysis & latency profiling",
            },
        ]

    def verify_bundle_integrity(self) -> Dict[str, Any]:
        """Verify that all files in this ruflo_swarm bundle exist and are valid."""
        results: Dict[str, Any] = {
            "agents": {},
            "mcp_schemas": 0,
            "mcp_schemas_invalid": [],
            "skills": [],
            "modules": [],
            "valid": True,
        }

        # Check agents
        for ag in self.get_swarm_agents():
            try:
                content = self.get_agent_prompt(ag["id"])
                results["agents"][ag["id"]] = f"OK ({len(content.splitlines())} lines)"
            except Exception as exc:
                results["agents"][ag["id"]] = f"FAILED: {exc}"
                results["valid"] = False

        # Check MCP schemas — count AND json-parse every file (corrupt JSON fails).
        schema_dir = MCP_DIR / "schemas"
        if schema_dir.is_dir():
            schemas = sorted(schema_dir.glob("*.json"))
            results["mcp_schemas"] = len(schemas)
            for schema_file in schemas:
                try:
                    json.loads(schema_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    results["mcp_schemas_invalid"].append(f"{schema_file.name}: {exc}")
                    results["valid"] = False
            if len(schemas) < 20:
                results["valid"] = False
        else:
            results["mcp_schemas"] = 0
            results["valid"] = False

        # Check bundled skills
        if SKILLS_DIR.exists():
            results["skills"] = [d.name for d in SKILLS_DIR.iterdir() if d.is_dir()]

        # Check modules
        modules_dir = BASE_DIR / "modules"
        if modules_dir.exists():
            results["modules"] = [f.stem for f in modules_dir.glob("*.py") if f.name != "__init__.py"]

        return results


def _emit_cmd_result(res: Dict[str, Any]) -> int:
    """Print a ruflo CLI result, propagate exit code (stderr on failure)."""
    exit_code = int(res.get("exit_code", 0) or 0)
    stdout = (res.get("stdout") or "").strip()
    stderr = (res.get("stderr") or "").strip()
    if exit_code == 0 and stdout:
        print(stdout)
        return 0
    # Failure (or empty success): surface stderr, never fake success.
    message = stderr or stdout or f"ruflo command failed (exit {exit_code})"
    print(message, file=sys.stderr)
    if stdout and stderr and stdout != stderr:
        print(stdout)
    return exit_code if exit_code != 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Ruflo Master Zero-API-Key Swarm Bridge")
    parser.add_argument("--status", action="store_true", help="Check Ruflo daemon/swarm status")
    parser.add_argument("--complexity", type=str, help="Run complexity analysis on file")
    parser.add_argument("--symbols", type=str, help="Extract symbols from file")
    parser.add_argument("--scan-pii", type=str, help="Scan file for leaked PII/API keys")
    parser.add_argument("--prompt", type=str, help="Print system prompt for an agent")
    parser.add_argument("--list-agents", action="store_true", help="List core swarm agents")
    parser.add_argument("--list-all", action="store_true", help="List all 75+ cataloged agents with dependencies")
    parser.add_argument("--category", type=str, help="Filter agents by category")
    parser.add_argument("--deps", type=str, help="Show dependency details for an agent")
    parser.add_argument("--info", type=str, help="Show full metadata info for an agent")
    parser.add_argument("--list-workflows", action="store_true", help="List all 7 official Ruflo workflow templates")
    parser.add_argument("--workflow", type=str, help="Show DAG and handoffs for a workflow (development, sparc, security-audit, etc.)")
    parser.add_argument("--boundaries", type=str, help="Run MinCut boundary analysis on file/directory")
    parser.add_argument("--verify", action="store_true", help="Verify bundle integrity (agents, mcp, skills, modules)")

    args = parser.parse_args()
    bridge = RufloBridge()

    if args.list_agents:
        print("🐝 Active Core Swarm Agents in ruflo_swarm:")
        for ag in bridge.get_swarm_agents():
            print(f"  • {ag['id']} [{ag['role']}]: {ag['description']}")
        return 0

    if args.list_all or args.category:
        catalog = bridge.get_catalog().get("agents", {})
        filter_cat = args.category.strip().lower() if args.category else None
        by_cat: Dict[str, List[Dict[str, Any]]] = {}
        for aid, info in catalog.items():
            cat = info.get("category", "general")
            if filter_cat and cat.lower() != filter_cat:
                continue
            by_cat.setdefault(cat, []).append(info)

        if not by_cat:
            print(f"No agents found matching category '{args.category}'.")
            return 1

        print(f"🐝 Ruflo Catalog Agents ({sum(len(v) for v in by_cat.values())} agents):")
        for cat, items in sorted(by_cat.items()):
            print(f"\n📂 [{cat.upper()}] ({len(items)} agents)")
            for it in items:
                sys_deps = ",".join(it.get("dependencies", {}).get("system_tools", [])) or "none"
                mcp_deps = ",".join(it.get("dependencies", {}).get("mcp_tools", [])) or "none"
                desc = it.get("description", "")[:60]
                if desc:
                    desc = f" - {desc}"
                print(f"  • {it['id']} [{it.get('role', 'specialist')}]{desc}")
                print(f"    └─ Tools: [System: {sys_deps} | MCP: {mcp_deps}]")
        return 0

    if args.deps:
        info = bridge.get_agent_info(args.deps)
        if not info:
            print(f"Agent '{args.deps}' not found in catalog.", file=sys.stderr)
            return 1
        print(f"📦 Dependencies for '{info['id']}' ({info.get('category')} / {info.get('role')}):")
        deps = info.get("dependencies", {})
        print("  • System Tools :", ", ".join(deps.get("system_tools", [])) or "None required")
        print("  • MCP Tools    :", ", ".join(deps.get("mcp_tools", [])) or "None required")
        print("  • Capabilities :", ", ".join(info.get("capabilities", [])) or "General")
        print("  • Spec File    :", info.get("relative_path", "N/A"))
        return 0

    if args.info:
        info = bridge.get_agent_info(args.info)
        if not info:
            print(f"Agent '{args.info}' not found in catalog.", file=sys.stderr)
            return 1
        print(json.dumps(info, indent=2))
        return 0

    if args.list_workflows:
        print("🐝 Official Ruflo Workflow Templates (7 Built-in):")
        for wf in bridge.workflows.list_workflows():
            print(f"\n  • {wf['name']} [{wf['id']}] (Est: {wf['estimated_duration']} | Topology: {wf['topology'].upper()})")
            print(f"    Description : {wf['description']}")
            print(f"    Stages ({wf['stages_count']}) : {' ➔ '.join(wf['stages'])}")
        return 0

    if args.workflow:
        print(bridge.workflows.render_workflow_ascii(args.workflow))
        return 0

    if args.boundaries:
        return _emit_cmd_result(bridge.analyze_boundaries(args.boundaries))

    if args.prompt:
        try:
            print(bridge.get_agent_prompt(args.prompt))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.status:
        return _emit_cmd_result(bridge.get_swarm_status())

    if args.scan_pii:
        res = bridge.scan_file_for_pii(args.scan_pii)
        if "error" in res:
            print(json.dumps(res, indent=2), file=sys.stderr)
            return 1
        print(json.dumps(res, indent=2))
        return 0

    if args.symbols:
        return _emit_cmd_result(bridge.analyze_symbols(args.symbols))

    if args.complexity:
        return _emit_cmd_result(bridge.analyze_complexity(args.complexity))

    if args.verify:
        print("🔍 Verifying ruflo_swarm bundle integrity...")
        integrity = bridge.verify_bundle_integrity()
        print(f"Bundle Valid: {integrity['valid']}")
        print(f"Agents Checked: {json.dumps(integrity['agents'], indent=2)}")
        print(f"MCP Schemas Found: {integrity['mcp_schemas']}")
        if integrity.get("mcp_schemas_invalid"):
            print(f"Invalid Schemas: {json.dumps(integrity['mcp_schemas_invalid'], indent=2)}", file=sys.stderr)
        print(f"Bundled Skills: {integrity['skills']}")
        print(f"Bundled Modules: {integrity['modules']}")
        return 0 if integrity["valid"] else 1

    # Default action: summary & verify
    print("🐝 Ruflo Master Zero-API-Key Swarm Bridge")
    print(f"Workspace: {bridge.workspace_root}\n")
    print("Use --help to see all available CLI operations.")
    print("Running quick verification check...")
    integrity = bridge.verify_bundle_integrity()
    print(f"Status: {'✅ All Systems Operational' if integrity['valid'] else '⚠️ Issues Detected'}")
    print(f"Agents Active: {len(integrity['agents'])} | MCP Schemas: {integrity['mcp_schemas']} | Skills: {len(integrity['skills'])}")
    return 0 if integrity["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
