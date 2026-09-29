"""
Ruflo Swarm Workflows Engine — Official 7 Workflow Templates & Multi-Agent DAG Handoffs.
Self-contained inside .agents/ruflo_swarm/ for Antigravity Zero-API-Key Swarm Orchestration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorkflowStage:
    name: str
    agents: List[str]
    description: str
    tools: List[str] = field(default_factory=list)
    input_artifacts: List[str] = field(default_factory=list)
    output_artifacts: List[str] = field(default_factory=list)
    handoff_to: Optional[str] = None


@dataclass
class WorkflowDefinition:
    id: str
    name: str
    description: str
    topology: str
    estimated_duration: str
    stages: List[WorkflowStage]


# The 7 Official Ruflo Workflow Templates with exact agent and handoff bindings
OFFICIAL_WORKFLOWS: Dict[str, WorkflowDefinition] = {
    "development": WorkflowDefinition(
        id="development",
        name="Standard Development Workflow",
        description="Standard 5-stage feature engineering flow from planning to integration.",
        topology="hierarchical",
        estimated_duration="15-30 min",
        stages=[
            WorkflowStage(
                name="Planning",
                agents=["planner", "task-orchestrator"],
                description="Deconstruct task into atomic milestones, identify dependencies, map blast radius.",
                tools=["ruflo analyze boundaries (MinCut)", "ruflo analyze complexity"],
                input_artifacts=["user_task", "target_files"],
                output_artifacts=["milestones_plan", "blast_radius_graph"],
                handoff_to="Implementation",
            ),
            WorkflowStage(
                name="Implementation",
                agents=["coder", "sparc-coder"],
                description="Write clean, modular code conforming to architectural boundaries.",
                tools=["ruflo hooks pre-edit", "ruflo analyze ast"],
                input_artifacts=["milestones_plan"],
                output_artifacts=["code_patch", "modified_files"],
                handoff_to="Testing",
            ),
            WorkflowStage(
                name="Testing",
                agents=["tester", "tdd-london-swarm"],
                description="Execute outside-in unit and integration test assertions.",
                tools=["pytest / test runner", "ruflo analyze diff --risk"],
                input_artifacts=["code_patch"],
                output_artifacts=["test_results", "diff_risk_score"],
                handoff_to="Review",
            ),
            WorkflowStage(
                name="Review",
                agents=["reviewer", "ruflo-code-auditor"],
                description="5-point adversarial inspection (callee + caller cross-inspection, edge cases).",
                tools=["ruflo security channel-scan", "ruflo analyze code"],
                input_artifacts=["code_patch", "test_results"],
                output_artifacts=["review_findings", "review_signoff"],
                handoff_to="Integration",
            ),
            WorkflowStage(
                name="Integration",
                agents=["pr-manager", "raft-manager"],
                description="Consensus quorum verification and committing durable memory.",
                tools=["hive-mind_consensus", "memory_store"],
                input_artifacts=["review_signoff"],
                output_artifacts=["consensus_receipt", "memory_key"],
                handoff_to=None,
            ),
        ],
    ),
    "sparc": WorkflowDefinition(
        id="sparc",
        name="SPARC Methodology Workflow",
        description="Formal SPARC (Specification -> Pseudocode -> Architecture -> Refinement -> Completion) cycle.",
        topology="hierarchical",
        estimated_duration="25-45 min",
        stages=[
            WorkflowStage(
                name="Specification",
                agents=["specification"],
                description="Elicit formal requirements, functional invariants, and edge case boundaries.",
                tools=["memory_search", "ruflo policy evaluate"],
                input_artifacts=["user_task"],
                output_artifacts=["formal_specification"],
                handoff_to="Pseudocode",
            ),
            WorkflowStage(
                name="Pseudocode",
                agents=["pseudocode"],
                description="Design algorithm logic flow, data structures, and state transitions.",
                tools=["ruflo analyze ast"],
                input_artifacts=["formal_specification"],
                output_artifacts=["pseudocode_algorithm"],
                handoff_to="Architecture",
            ),
            WorkflowStage(
                name="Architecture",
                agents=["architecture", "repo-architect"],
                description="Define interface schemas, type annotations, and module contracts.",
                tools=["ruflo analyze boundaries"],
                input_artifacts=["pseudocode_algorithm"],
                output_artifacts=["architecture_contracts"],
                handoff_to="Refinement",
            ),
            WorkflowStage(
                name="Refinement",
                agents=["refinement", "sparc-coder"],
                description="TDD implementation, edge-case hardening, and test assertion verification.",
                tools=["pytest", "ruflo hooks post-edit"],
                input_artifacts=["architecture_contracts"],
                output_artifacts=["implementation_diff", "test_suite"],
                handoff_to="Completion",
            ),
            WorkflowStage(
                name="Completion",
                agents=["production-validator", "raft-manager"],
                description="End-to-end regression validation and Raft consensus sign-off.",
                tools=["hive-mind_consensus", "memory_store"],
                input_artifacts=["implementation_diff", "test_suite"],
                output_artifacts=["production_verification_receipt"],
                handoff_to=None,
            ),
        ],
    ),
    "security-audit": WorkflowDefinition(
        id="security-audit",
        name="Security & Threat Audit Workflow",
        description="Comprehensive security scanning, CVE checks, PII protection, and threat modeling.",
        topology="mesh",
        estimated_duration="20-40 min",
        stages=[
            WorkflowStage(
                name="Threat Model",
                agents=["security-manager"],
                description="Construct threat model identifying attack vectors, trust boundaries, and injection surfaces.",
                tools=["ruflo security threats", "metaharness_threat_model"],
                input_artifacts=["target_files"],
                output_artifacts=["threat_model_report"],
                handoff_to="Static Analysis",
            ),
            WorkflowStage(
                name="Static Analysis",
                agents=["ruflo-security-auditor"],
                description="Audit source code for leaked secrets, hardcoded credentials, and CVE patterns.",
                tools=["ruflo security secrets", "ruflo security scan", "aidefence_has_pii"],
                input_artifacts=["threat_model_report"],
                output_artifacts=["vulnerability_findings"],
                handoff_to="Dynamic Analysis",
            ),
            WorkflowStage(
                name="Dynamic Analysis",
                agents=["byzantine-coordinator", "security-manager"],
                description="Inter-agent channel scan and prompt injection validation.",
                tools=["ruflo security channel-scan", "ruflo security composition-scan"],
                input_artifacts=["vulnerability_findings"],
                output_artifacts=["channel_injection_report"],
                handoff_to="Report",
            ),
            WorkflowStage(
                name="Report",
                agents=["ruflo-orchestrator-lead"],
                description="Synthesize executive audit report and commit security findings to vector memory.",
                tools=["memory_store"],
                input_artifacts=["channel_injection_report"],
                output_artifacts=["audit_report_final"],
                handoff_to=None,
            ),
        ],
    ),
    "code-review": WorkflowDefinition(
        id="code-review",
        name="Multi-Agent Code Review Workflow",
        description="Parallel peer review covering quality, style, security, and runtime regressions.",
        topology="mesh",
        estimated_duration="10-25 min",
        stages=[
            WorkflowStage(
                name="Initial Review",
                agents=["reviewer"],
                description="High-level logic inspection, convention compliance, and intent check.",
                tools=["ruflo analyze diff"],
                input_artifacts=["diff"],
                output_artifacts=["initial_review_comments"],
                handoff_to="Security Check",
            ),
            WorkflowStage(
                name="Security Check",
                agents=["ruflo-security-auditor"],
                description="Verify input sanitization, SQL/shell safety, and authorization boundaries.",
                tools=["ruflo security scan"],
                input_artifacts=["diff"],
                output_artifacts=["security_review_comments"],
                handoff_to="Quality Analysis",
            ),
            WorkflowStage(
                name="Quality Analysis",
                agents=["ruflo-code-auditor", "perf-analyzer"],
                description="Adversarial caller/callee cross-inspection and algorithmic complexity check.",
                tools=["ruflo analyze complexity", "ruflo analyze circular"],
                input_artifacts=["diff"],
                output_artifacts=["quality_metrics"],
                handoff_to="Feedback",
            ),
            WorkflowStage(
                name="Feedback",
                agents=["code-review-swarm"],
                description="Consolidate all reviewers into a unified review verdict.",
                tools=["memory_store"],
                input_artifacts=["initial_review_comments", "security_review_comments", "quality_metrics"],
                output_artifacts=["review_verdict"],
                handoff_to=None,
            ),
        ],
    ),
    "refactoring": WorkflowDefinition(
        id="refactoring",
        name="Architecture Refactoring Workflow",
        description="Safe, non-breaking refactoring pipeline backed by MinCut boundaries and test gates.",
        topology="hierarchical",
        estimated_duration="15-35 min",
        stages=[
            WorkflowStage(
                name="Analysis",
                agents=["architecture", "ruflo-arch-auditor"],
                description="Analyze current modular boundaries, circular dependencies, and caller footprints.",
                tools=["ruflo analyze boundaries", "ruflo analyze circular"],
                input_artifacts=["target_files"],
                output_artifacts=["architecture_baseline"],
                handoff_to="Planning",
            ),
            WorkflowStage(
                name="Planning",
                agents=["planner"],
                description="Define atomic refactoring steps ensuring zero breaking changes to consumers.",
                tools=["ruflo policy evaluate"],
                input_artifacts=["architecture_baseline"],
                output_artifacts=["refactor_plan"],
                handoff_to="Refactor",
            ),
            WorkflowStage(
                name="Refactor",
                agents=["coder", "sparc-coder"],
                description="Execute code transformations, extract helper modules, and preserve public APIs.",
                tools=["ruflo hooks pre-edit", "ruflo hooks post-edit"],
                input_artifacts=["refactor_plan"],
                output_artifacts=["refactored_diff"],
                handoff_to="Validation",
            ),
            WorkflowStage(
                name="Validation",
                agents=["tester", "production-validator"],
                description="Validate full regression suite and compare complexity metrics.",
                tools=["pytest", "ruflo analyze complexity"],
                input_artifacts=["refactored_diff"],
                output_artifacts=["validation_report"],
                handoff_to=None,
            ),
        ],
    ),
    "testing": WorkflowDefinition(
        id="testing",
        name="Comprehensive Testing Workflow",
        description="Multi-tier testing pipeline covering unit, integration, e2e, and performance profiling.",
        topology="hierarchical",
        estimated_duration="5-15 min",
        stages=[
            WorkflowStage(
                name="Unit Tests",
                agents=["tdd-london-swarm", "tester"],
                description="Isolated unit testing with mock contracts.",
                tools=["pytest"],
                input_artifacts=["target_files"],
                output_artifacts=["unit_test_results"],
                handoff_to="Integration Tests",
            ),
            WorkflowStage(
                name="Integration Tests",
                agents=["tester"],
                description="Test cross-module boundaries and subsystem wireups.",
                tools=["pytest"],
                input_artifacts=["unit_test_results"],
                output_artifacts=["integration_test_results"],
                handoff_to="E2E Tests",
            ),
            WorkflowStage(
                name="E2E Tests",
                agents=["production-validator"],
                description="End-to-end user scenario validation.",
                tools=["pytest"],
                input_artifacts=["integration_test_results"],
                output_artifacts=["e2e_results"],
                handoff_to="Performance Tests",
            ),
            WorkflowStage(
                name="Performance Tests",
                agents=["ruflo-perf-auditor", "perf-analyzer"],
                description="Latency benchmarking, event-loop blocking audit, and memory growth.",
                tools=["ruflo performance benchmark"],
                input_artifacts=["e2e_results"],
                output_artifacts=["performance_profile"],
                handoff_to=None,
            ),
        ],
    ),
    "research": WorkflowDefinition(
        id="research",
        name="Deep Research & Synthesis Workflow",
        description="Systematic investigation, codebase pattern discovery, and technical documentation.",
        topology="mesh",
        estimated_duration="10-20 min",
        stages=[
            WorkflowStage(
                name="Discovery",
                agents=["researcher"],
                description="Search repository and documentation for existing implementations and patterns.",
                tools=["memory_search", "ruflo analyze symbols"],
                input_artifacts=["research_query"],
                output_artifacts=["raw_findings"],
                handoff_to="Analysis",
            ),
            WorkflowStage(
                name="Analysis",
                agents=["code-analyzer", "ruflo-arch-auditor"],
                description="Compare competing technical approaches and trade-offs.",
                tools=["ruflo analyze deps"],
                input_artifacts=["raw_findings"],
                output_artifacts=["tradeoff_matrix"],
                handoff_to="Synthesis",
            ),
            WorkflowStage(
                name="Synthesis",
                agents=["ruflo-orchestrator-lead"],
                description="Formulate final recommendation and architecture proposal.",
                tools=["ruflo policy evaluate"],
                input_artifacts=["tradeoff_matrix"],
                output_artifacts=["recommendation_summary"],
                handoff_to="Documentation",
            ),
            WorkflowStage(
                name="Documentation",
                agents=["api-docs", "migration-planner"],
                description="Author documentation specs and persist to vector memory.",
                tools=["memory_store"],
                input_artifacts=["recommendation_summary"],
                output_artifacts=["technical_spec_doc"],
                handoff_to=None,
            ),
        ],
    ),
}


class WorkflowEngine:
    """
    Self-contained workflow DAG manager for Antigravity Swarm Orchestration.
    """

    def __init__(self, workflows: Optional[Dict[str, WorkflowDefinition]] = None):
        self.workflows = workflows or OFFICIAL_WORKFLOWS

    def list_workflows(self) -> List[Dict[str, Any]]:
        """Return list of all 7 available official workflow templates."""
        return [
            {
                "id": wf.id,
                "name": wf.name,
                "description": wf.description,
                "topology": wf.topology,
                "stages_count": len(wf.stages),
                "estimated_duration": wf.estimated_duration,
                "stages": [s.name for s in wf.stages],
            }
            for wf in self.workflows.values()
        ]

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Look up a workflow definition by id."""
        clean = workflow_id.strip().lower()
        return self.workflows.get(clean)

    def render_workflow_ascii(self, workflow_id: str) -> str:
        """Render a clean visual ASCII diagram of the workflow DAG and handoffs."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            return f"Workflow '{workflow_id}' not found."

        lines = [
            f"🐝 Ruflo Official Workflow: {wf.name} ({wf.id})",
            f"Topology: {wf.topology.upper()} | Est. Duration: {wf.estimated_duration}",
            f"Description: {wf.description}",
            "",
            "Execution Sequence & Handoff DAG:",
            "=" * 60,
        ]

        for idx, stage in enumerate(wf.stages, 1):
            lines.append(f"[{idx}] Stage: {stage.name}")
            lines.append(f"    • Active Agents : {', '.join(stage.agents)}")
            lines.append(f"    • Tools/Features: {', '.join(stage.tools) if stage.tools else 'None'}")
            lines.append(f"    • Inputs        : {', '.join(stage.input_artifacts)}")
            lines.append(f"    • Outputs       : {', '.join(stage.output_artifacts)}")
            if stage.handoff_to:
                lines.append(f"    ▼ [HANDOFF] ──► Passes ({', '.join(stage.output_artifacts)}) to Stage: '{stage.handoff_to}'")
                lines.append("")
            else:
                lines.append("    ■ [TERMINAL] ──► Raft Consensus / Final Memory Store Committed.")

        lines.append("=" * 60)
        return "\n".join(lines)
