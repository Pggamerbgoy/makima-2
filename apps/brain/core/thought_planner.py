"""
Makima OS — ThoughtPlanner (Tree of Thoughts / ToT Engine - Module 5)
Location: apps/brain/core/thought_planner.py

Based on:
  - Yao et al. 2023 arXiv:2305.10601 (Tree of Thoughts)
  - Hao et al. 2023 arXiv:2305.14992 (Reasoning via Planning + MCTS)

Enables deliberate multi-path reasoning, heuristic evaluation, and backtracking
for complex tasks (>2 steps), while preserving the fast linear path for simple tasks.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger("makima.core.thought_planner")

from enum import Enum

_SIMPLE_TASK_PATTERNS = re.compile(
    r"^(hi|hello|hey|kaisa hai|who are you|what time|time kya|status|ping|pong|help|version|shukriya|thanks|"
    r"screenshot|volume\s+\d+|aawaz\s+\d+|sound\s+\d+|set\s+volume|"
    r"chrome\s+kholo|open\s+chrome|launch\s+chrome|"
    r"notepad\s+kholo|open\s+notepad|launch\s+notepad|"
    r"calculator\s+kholo|open\s+calculator)\b",
    re.IGNORECASE,
)

class IntentCompleteness(str, Enum):
    """Semantic uncertainty state of a user instruction (domain-agnostic)."""
    COMPLETE = "complete"                          # All critical parameters and target domain are clear
    AMBIGUOUS_RESOLVABLE = "ambiguous_resolvable"  # Underspecified, but inferrable from active screen/clipboard/memory
    AMBIGUOUS_NEEDS_USER = "ambiguous_needs_user"  # Underspecified with zero ground truth; requires clarification


@dataclass
class CompletenessAssessment:
    """Model-evaluated assessment of semantic completeness and ambiguity (SAGE-Agent arXiv:2511.08798)."""
    state: IntentCompleteness = IntentCompleteness.COMPLETE
    complexity: str = "simple"
    reason: str = ""
    missing_slots: List[str] = field(default_factory=list)
    clarifying_question: Optional[str] = None
    suggested_action: Optional[str] = None
    candidate_tool: Optional[str] = None                 # Primary candidate tool c
    candidate_viability: float = 1.0                     # pi_c(t) = prod p(theta_j | obs_t)
    missing_critical_params: List[str] = field(default_factory=list)
    partial_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThoughtNode:
    """Represents a single node in the reasoning tree."""
    node_id: str
    parent_id: Optional[str]
    thought: str           # Reasoning step / strategy description
    action: Optional[str] = None  # Tool call or primary action
    result: Optional[str] = None  # Expected or actual tool result
    score: float = 0.0     # Value estimate in range [0.0, 1.0]
    depth: int = 0         # Depth in reasoning tree (0 = root)
    children: List[str] = field(default_factory=list)  # Child node IDs
    status: str = "active" # "active" | "pruned" | "selected"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "thought": self.thought,
            "action": self.action,
            "result": self.result,
            "score": self.score,
            "depth": self.depth,
            "children": self.children,
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionPlan:
    """Pre-computed deliberative execution plan to inject into agent prompt."""
    task: str
    selected_path: List[ThoughtNode]
    confidence: float
    fallback_path: Optional[List[ThoughtNode]] = None
    completeness: Optional[CompletenessAssessment] = None
    is_clarification_required: bool = False
    clarifying_question: Optional[str] = None
    candidate_tool: Optional[str] = None
    candidate_viability: float = 1.0
    missing_critical_params: List[str] = field(default_factory=list)
    partial_parameters: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_prompt_string(self) -> str:
        """Format the plan as a prioritized instruction for the agent."""
        if self.is_clarification_required:
            q = self.clarifying_question or (self.completeness.clarifying_question if self.completeness else None) or "Please clarify your exact requirements before proceeding."
            missing = ", ".join(self.completeness.missing_slots) if (self.completeness and self.completeness.missing_slots) else "target domain/parameters"
            return (
                "### INTENT CLARIFICATION MANDATE (Tree-of-Thoughts Deliberation)\n"
                f"Goal: {self.task}\n"
                f"Status: AMBIGUOUS_NEEDS_USER (Missing: {missing})\n"
                "CRITICAL DIRECTIVE: DO NOT EXECUTE ANY TOOLS OR CREATE FILES/DOCUMENTS.\n"
                "You must immediately respond to the user with the following clarifying question:\n"
                f'"{q}"'
            )

        lines = [
            "### PRE-COMPUTED DELIBERATIVE EXECUTION PLAN (Tree-of-Thoughts)",
            f"Goal: {self.task}",
        ]

        if self.completeness and self.completeness.state == IntentCompleteness.AMBIGUOUS_RESOLVABLE:
            lines.extend([
                "🔍 CONTEXTUAL GROUNDING REQUIRED: Underspecified goal.",
                "Action: Inspect Active Foreground Window, Screen, or Clipboard before generating artifacts.",
            ])

        lines.extend([
            f"Primary Strategy (Confidence: {int(self.confidence * 100)}%):",
            "Follow these verified sequential steps (Approach):",
        ])
        for idx, node in enumerate(self.selected_path, 1):
            act_desc = f" [Tool/Action: {node.action}]" if node.action else ""
            lines.append(f"{idx}. {node.thought}{act_desc}")

        if self.fallback_path:
            lines.append("\nFallback Contingency (if primary path encounters errors):")
            for idx, f_node in enumerate(self.fallback_path, 1):
                f_act = f" [Tool/Action: {f_node.action}]" if f_node.action else ""
                lines.append(f"  F{idx}. {f_node.thought}{f_act}")

        lines.append("\nExecute systematically using the above planned route.")
        return "\n".join(lines)


class SelectionResult(tuple):
    """Allows both `best, fallback = select_best(...)` and `best = select_best(...); best.score`."""
    def __new__(cls, best_node: ThoughtNode, fallback_node: Optional[ThoughtNode]):
        return super().__new__(cls, (best_node, fallback_node))

    def __init__(self, best_node: ThoughtNode, fallback_node: Optional[ThoughtNode]):
        self.best_node = best_node
        self.fallback_node = fallback_node

    def __getattr__(self, name: str) -> Any:
        return getattr(self.best_node, name)


class ThoughtPlanner:
    """
    Tree-of-Thoughts (ToT) Deliberative Planner.
    Explores multiple reasoning trajectories, scores candidates, and selects the optimal path.
    """

    def __init__(self, ai_handler: Any, tool_registry: Optional[Any] = None) -> None:
        self.ai_handler = ai_handler
        self.tool_registry = tool_registry
        self._completeness_cache: dict[str, CompletenessAssessment] = {}

    async def assess_intent_completeness(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> CompletenessAssessment:
        """
        Model-driven cognitive assessment of semantic completeness, ambiguity, and operational complexity.
        Evaluates whether the user's intent has sufficient grounding or if critical domain parameters are missing.
        Completely domain-agnostic — reasons through goals across code, system, devops, documents, messaging, etc.
        Executes in a single call and caches results to prevent duplicate LLM calls.
        """
        cleaned = (task or "").strip()
        if not cleaned:
            return CompletenessAssessment(
                state=IntentCompleteness.AMBIGUOUS_NEEDS_USER,
                complexity="complex",
                reason="Empty instruction",
            )

        if len(cleaned) < 30 and _SIMPLE_TASK_PATTERNS.search(cleaned):
            return CompletenessAssessment(
                state=IntentCompleteness.COMPLETE,
                complexity="simple",
                reason="Trivial/greeting query",
            )

        # 1. Check in-memory cache and context cache first to eliminate duplicate LLM calls
        if context is not None and isinstance(context, dict) and "_completeness_assessment" in context:
            return context["_completeness_assessment"]
        if cleaned in self._completeness_cache:
            return self._completeness_cache[cleaned]

        ctx = dict(context or {})
        os_info = []
        if ctx.get("foreground_window"):
            os_info.append(f"Active Foreground Window: {ctx['foreground_window']}")
        if ctx.get("clipboard"):
            os_info.append(f"Clipboard: {str(ctx['clipboard'])[:120]}")
        if ctx.get("memory_results"):
            os_info.append(f"Recalled Memory: {str(ctx['memory_results'])[:160]}")
        ctx_str = "\n".join(os_info) if os_info else "None (environment is neutral/unrelated)"

        # Available tools with critical parameters hint (SAGE-Agent POMDP)
        known_tools_hint = ""
        if self.tool_registry and hasattr(self.tool_registry, "_tools"):
            crit_tools = [
                f"- {name}: critical params {meta.critical_parameters}"
                for name, meta in self.tool_registry._tools.items()
                if getattr(meta, "critical_parameters", ())
            ][:30]
            if crit_tools:
                known_tools_hint = "Tool Parameter Requirements (SAGE-Agent):\n" + "\n".join(crit_tools) + "\n\n"

        prompt = (
            "You are an expert cognitive intent analyst implementing SAGE-Agent (ACL Findings 2026: Structured Uncertainty for Agents).\n"
            f'User Instruction: "{cleaned}"\n'
            f"Active Environment / Screen / Memory Context:\n{ctx_str}\n\n"
            f"{known_tools_hint}"
            "Evaluate structured parameter certainty, candidate tool viability, and semantic completeness:\n"
            "1. 'candidate_tool': The primary tool intended (e.g. 'set_volume', 'launch_app', 'create_excel', 'create_word', 'create_ppt', 'delete_file', 'kill_process', 'send_whatsapp_message', 'send_email', 'play_media', 'write_file', or null if general conversation).\n"
            "2. 'critical_parameters_status': Are the essential operational parameters (e.g. table columns/rows for spreadsheets, level for volume, app name to launch, content blocks for documents, file path to delete, recipient for messages) known?\n"
            "3. 'candidate_viability': Certainty score between 0.0 and 1.0 (pi_c = product of parameter probabilities). If critical parameters are missing and not in context, score must be < 0.6. If fully specified, score is 1.0.\n"
            "4. 'state':\n"
            "   - 'complete': Viability >= 0.75. All critical parameters and target domain are clear. Direct commands (e.g. 'Volume 40 percent kar do' -> level 40, 'Chrome kholo' -> app Chrome) are 100% COMPLETE.\n"
            "   - 'ambiguous_resolvable': Viability < 0.75, but active context (screen/clipboard/memory) provides the missing parameters.\n"
            "   - 'ambiguous_needs_user': Viability < 0.75, and context does NOT provide them. Proceeding would require hallucinating or creating dummy/fake content.\n"
            "5. 'complexity':\n"
            "   - 'simple': Straightforward 1-step unambiguous action with complete parameters (e.g. volume adjustment, launching an app, taking a screenshot).\n"
            "   - 'complex': Underspecified instruction (any ambiguous state), or multi-step/creative workflow.\n\n"
            "Respond with ONLY a JSON object:\n"
            "{\n"
            '  "candidate_tool": "tool_name or null",\n'
            '  "candidate_viability": 0.0 to 1.0,\n'
            '  "complexity": "simple" | "complex",\n'
            '  "state": "complete" | "ambiguous_resolvable" | "ambiguous_needs_user",\n'
            '  "reason": "Short explanation of what is known vs unknown",\n'
            '  "missing_slots": ["list of missing critical parameters"],\n'
            '  "partial_parameters": {"known_param": "value"},\n'
            '  "clarifying_question": "A single crisp, natural question to ask the user if state is ambiguous_needs_user (null otherwise)"\n'
            "}"
        )

        default_complexity = "complex" if len(cleaned) > 80 else "simple"
        assessment = CompletenessAssessment(
            state=IntentCompleteness.COMPLETE,
            complexity=default_complexity,
            reason="Default fallback",
        )
        try:
            resp = await self._call_llm(prompt, temperature=0.1)
            parsed = self._extract_json(resp)
            if isinstance(parsed, dict):
                raw_state = str(parsed.get("state", "")).lower().strip()
                raw_complexity = str(parsed.get("complexity", "")).lower().strip()
                state = IntentCompleteness.COMPLETE
                if raw_state in ("complete", "ambiguous_resolvable", "ambiguous_needs_user"):
                    state = IntentCompleteness(raw_state)

                if state in (IntentCompleteness.AMBIGUOUS_NEEDS_USER, IntentCompleteness.AMBIGUOUS_RESOLVABLE):
                    complexity = "complex"
                elif raw_complexity in ("simple", "complex"):
                    complexity = raw_complexity
                else:
                    complexity = default_complexity

                cand_tool = parsed.get("candidate_tool")
                if cand_tool and str(cand_tool).lower() in ("null", "none"):
                    cand_tool = None
                try:
                    cand_viability = float(parsed.get("candidate_viability", 1.0))
                except (ValueError, TypeError):
                    cand_viability = 1.0

                missing = parsed.get("missing_slots") if isinstance(parsed.get("missing_slots"), list) else []
                partial = parsed.get("partial_parameters") if isinstance(parsed.get("partial_parameters"), dict) else {}

                assessment = CompletenessAssessment(
                    state=state,
                    complexity=complexity,
                    reason=parsed.get("reason", ""),
                    missing_slots=missing,
                    clarifying_question=parsed.get("clarifying_question"),
                    candidate_tool=cand_tool,
                    candidate_viability=cand_viability,
                    missing_critical_params=missing,
                    partial_parameters=partial,
                )
        except Exception as e:
            logger.debug("[ThoughtPlanner] assess_intent_completeness error: %s", e)

        # Cache bounded to 100 entries
        if len(self._completeness_cache) > 100:
            self._completeness_cache.clear()
        self._completeness_cache[cleaned] = assessment

        if context is not None and isinstance(context, dict):
            context["_completeness_assessment"] = assessment
        return assessment

    async def classify_complexity(self, task: str, context: Optional[dict[str, Any]] = None) -> str:
        """
        Classify whether a task requires fast linear execution or deliberative planning.
        A task is 'complex' if it involves multi-step workflows OR semantic ambiguity needing deliberation.
        Executes via unified single-call cognitive evaluation without duplicate LLM queries.
        """
        cleaned = (task or "").strip()
        if len(cleaned) < 30 and _SIMPLE_TASK_PATTERNS.search(cleaned):
            return "simple"

        # Explicit multi-step keywords
        multi_step_keywords = (
            "and then", "after that", "first", "step by step",
            "workflow", "pipeline", "scrape and", "search and create",
            "analyze and write", "download and", "find all and summarize",
            "and write", "literature review", "compare methodologies",
        )
        if any(kw in cleaned.lower() for kw in multi_step_keywords):
            return "complex"

        if not self.ai_handler:
            return "complex" if len(cleaned) > 80 else "simple"

        # Unified single-call cognitive assessment (evaluates both ambiguity & complexity)
        assessment = await self.assess_intent_completeness(cleaned, context=context)
        return assessment.complexity

    async def generate_thoughts(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
        n: int = 3,
        completeness: Optional[CompletenessAssessment] = None,
    ) -> List[ThoughtNode]:
        """
        Generate n candidate reasoning approaches for the task.
        Incorporate cognitive ambiguity analysis to prevent blind guesswork.
        """
        comp_hint = ""
        if completeness and completeness.state == IntentCompleteness.AMBIGUOUS_NEEDS_USER:
            comp_hint = (
                f"\nNOTE: This goal appears underspecified (Missing: {', '.join(completeness.missing_slots) if completeness.missing_slots else 'target domain/entity'}). "
                "Candidate strategies MUST include either inspecting available context (screen/clipboard/memory) or asking the user for clarification rather than blindly fabricating arbitrary data.\n"
            )
        elif completeness and completeness.state == IntentCompleteness.AMBIGUOUS_RESOLVABLE:
            comp_hint = "\nNOTE: Underspecified goal, but active context (screen/clipboard) may contain ground truth. A strategy should prioritize inspecting that context first.\n"

        prompt = (
            f"Goal: {task}\n"
            f"{comp_hint}\n"
            f"Generate exactly {n} distinct, realistic strategies to solve this goal safely and intelligently.\n"
            "Principles to reason about:\n"
            "- What information is given vs what assumptions are being made.\n"
            "- If critical details are missing, strategies should ground themselves in environment context or clarify with the user rather than fabricating arbitrary data.\n\n"
            "Respond ONLY with a JSON array of objects with the following schema:\n"
            "[\n"
            "  {\n"
            '    "strategy_name": "Short descriptive name",\n'
            '    "thought": "Reasoning step detailing the approach and how it addresses context/ambiguity",\n'
            '    "first_action": "Suggested primary tool or action (e.g. search_web, read_file, clarify_with_user)",\n'
            '    "why_effective": "Why this approach succeeds",\n'
            '    "main_risk": "Primary failure mode or risk of false assumptions"\n'
            "  }\n"
            "]"
        )

        try:
            resp = await self._call_llm(prompt, temperature=0.6)
            data = self._extract_json(resp)
            if isinstance(data, list) and len(data) > 0:
                nodes: List[ThoughtNode] = []
                for item in data[:n]:
                    if isinstance(item, dict):
                        nid = f"node_{uuid.uuid4().hex[:8]}"
                        node = ThoughtNode(
                            node_id=nid,
                            parent_id=None,
                            thought=f"{item.get('strategy_name', 'Strategy')}: {item.get('thought', '')}",
                            action=item.get("first_action"),
                            depth=1,
                            metadata={
                                "why_effective": item.get("why_effective", ""),
                                "main_risk": item.get("main_risk", ""),
                            },
                        )
                        nodes.append(node)
                if nodes:
                    return nodes
        except Exception as e:
            logger.warning("[ThoughtPlanner] generate_thoughts LLM error: %s", e)

        # Fallback deterministic candidate thoughts
        return [
            ThoughtNode(
                node_id=f"node_{uuid.uuid4().hex[:8]}",
                parent_id=None,
                thought=f"Grounded Execution: Verify requirements against context and execute actions for '{task}'",
                action="grounded_execution",
                depth=1,
            ),
            ThoughtNode(
                node_id=f"node_{uuid.uuid4().hex[:8]}",
                parent_id=None,
                thought=f"Context Discovery: Inspect environment and memory before committing changes for '{task}'",
                action="context_discovery",
                depth=1,
            ),
            ThoughtNode(
                node_id=f"node_{uuid.uuid4().hex[:8]}",
                parent_id=None,
                thought=f"Clarification & Validation: Confirm ambiguous parameters prior to execution for '{task}'",
                action="clarify_and_validate",
                depth=1,
            ),
        ]

    async def evaluate_thoughts(
        self,
        nodes: List[ThoughtNode],
    ) -> List[float]:
        """
        Evaluate candidate thoughts on Feasibility, Efficiency, and Safety.
        Returns a list of float scores in range [0.0, 1.0].
        """
        if not nodes:
            return []

        descriptions = []
        for i, node in enumerate(nodes, 1):
            descriptions.append(
                f"Approach {i}:\n"
                f"Thought: {node.thought}\n"
                f"Action: {node.action}\n"
                f"Risk: {node.metadata.get('main_risk', 'None stated')}"
            )
        eval_body = "\n\n".join(descriptions)

        prompt = (
            "Evaluate each of the following candidate strategies on a scale of 0 to 10 based on:\n"
            "1. Feasibility (likelihood of completing the goal)\n"
            "2. Efficiency (minimal redundant operations)\n"
            "3. Safety (no unintended side-effects)\n"
            "4. Groundedness & Integrity (reward strategies that rely on verified context or ask when critical intent is missing; heavily penalize strategies that blindly fabricate/hallucinate arbitrary domain data)\n\n"
            f"{eval_body}\n\n"
            "Respond ONLY with a JSON array of numbers representing the score (0-10) for each approach in order.\n"
            "Example: [8.5, 6.0, 7.5]"
        )

        try:
            resp = await self._call_llm(prompt, temperature=0.1)
            scores_raw = self._extract_json(resp)
            if isinstance(scores_raw, list):
                scores: List[float] = []
                for s in scores_raw:
                    try:
                        val = float(s)
                        # Normalize 0-10 to 0.0-1.0
                        norm = max(0.0, min(1.0, val / 10.0 if val > 1.0 else val))
                        scores.append(norm)
                    except (ValueError, TypeError):
                        scores.append(0.5)
                while len(scores) < len(nodes):
                    scores.append(0.5)
                return scores[:len(nodes)]
        except Exception as e:
            logger.warning("[ThoughtPlanner] evaluate_thoughts error: %s", e)

        # Fallback heuristic scores
        return [0.8, 0.7, 0.6][:len(nodes)]

    def select_best(
        self,
        nodes: List[ThoughtNode],
        scores: List[float],
    ) -> tuple[ThoughtNode, Optional[ThoughtNode]]:
        """
        Select the highest scoring node.
        If scores are close (< 0.1 diff), uses the first.
        Returns (best_node, fallback_node).
        """
        if not nodes:
            raise ValueError("Cannot select from empty nodes list")

        for node, score in zip(nodes, scores):
            node.score = score

        sorted_pairs = sorted(zip(nodes, scores), key=lambda x: x[1], reverse=True)
        best_node = sorted_pairs[0][0]
        best_node.status = "selected"

        fallback_node: Optional[ThoughtNode] = None
        if len(sorted_pairs) > 1:
            fallback_node = sorted_pairs[1][0]
            fallback_node.status = "active"

        # Prune remaining
        for node, _ in sorted_pairs[2:]:
            node.status = "pruned"

        return SelectionResult(best_node, fallback_node)

    async def expand_node(
        self,
        node: ThoughtNode,
        context: Optional[dict[str, Any]] = None,
        max_depth: int = 4,
    ) -> List[ThoughtNode]:
        """
        Expand subsequent action steps from the selected root node.
        """
        prompt = (
            f"Selected Strategy: {node.thought}\n"
            f"Initial Action: {node.action}\n\n"
            f"Outline the next 2-3 concrete steps to complete this strategy up to depth {max_depth}.\n"
            "Respond ONLY with a JSON array of step descriptions:\n"
            '["Step 2 description", "Step 3 description"]'
        )

        expanded_nodes: List[ThoughtNode] = [node]
        current_parent_id = node.node_id
        current_depth = node.depth

        try:
            resp = await self._call_llm(prompt, temperature=0.3)
            steps = self._extract_json(resp)
            if isinstance(steps, list):
                for s in steps:
                    if current_depth >= max_depth:
                        break
                    current_depth += 1
                    step_text = str(s).strip()
                    if not step_text:
                        continue
                    child_id = f"node_{uuid.uuid4().hex[:8]}"
                    node.children.append(child_id)
                    child_node = ThoughtNode(
                        node_id=child_id,
                        parent_id=current_parent_id,
                        thought=step_text,
                        depth=current_depth,
                        score=node.score,
                        status="selected",
                    )
                    expanded_nodes.append(child_node)
                    current_parent_id = child_id
                return expanded_nodes
        except Exception as e:
            logger.debug("[ThoughtPlanner] expand_node error: %s", e)

        # Fallback single expansion
        fallback_step = ThoughtNode(
            node_id=f"node_{uuid.uuid4().hex[:8]}",
            parent_id=node.node_id,
            thought="Execute subsequent actions and verify result output against goals.",
            depth=node.depth + 1,
            score=node.score,
            status="selected",
        )
        expanded_nodes.append(fallback_step)
        return expanded_nodes

    async def search(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
        max_depth: int = 4,
    ) -> ExecutionPlan:
        """
        Execute full Tree-of-Thoughts deliberative search:
        1. Generate candidates (n=3)
        2. Evaluate candidates
        3. Select best and runner-up fallback
        4. Expand best node trajectory
        5. Return ExecutionPlan
        """
        logger.info("[ThoughtPlanner] Initiating Tree-of-Thoughts search for task: '%s'", task[:60])
        start_time = time.time()

        cleaned = (task or "").strip()

        # Step 0: Retrieve cached intent completeness assessment or evaluate
        completeness: Optional[CompletenessAssessment] = None
        if context is not None and isinstance(context, dict) and "_completeness_assessment" in context:
            completeness = context["_completeness_assessment"]
        elif cleaned in self._completeness_cache:
            completeness = self._completeness_cache[cleaned]
        elif self.ai_handler:
            completeness = await self.assess_intent_completeness(cleaned, context=context)

        # Step 0.5: If ambiguous and needs user clarification, STOP and return clarification outcome immediately!
        if completeness and completeness.state == IntentCompleteness.AMBIGUOUS_NEEDS_USER:
            q = completeness.clarifying_question or "Please clarify what specific domain or requirements you need."
            elapsed = time.time() - start_time
            logger.info(
                "[ThoughtPlanner] SAGE Ambiguity detected (tool=%s, viability=%.2f, missing=%s) — halting in %.2fs",
                completeness.candidate_tool,
                completeness.candidate_viability,
                completeness.missing_slots,
                elapsed,
            )
            clarification_node = ThoughtNode(
                node_id=f"node_{uuid.uuid4().hex[:8]}",
                parent_id=None,
                thought=f"Clarification Required: Ask user '{q}' before executing tools or generating data.",
                action="clarify_with_user",
                result=q,
                score=1.0,
                status="selected",
            )
            return ExecutionPlan(
                task=task,
                selected_path=[clarification_node],
                confidence=completeness.candidate_viability,
                fallback_path=None,
                completeness=completeness,
                is_clarification_required=True,
                clarifying_question=q,
                candidate_tool=completeness.candidate_tool,
                candidate_viability=completeness.candidate_viability,
                missing_critical_params=completeness.missing_slots,
                partial_parameters=completeness.partial_parameters,
            )

        # Step 1: Generate candidates with completeness hint
        candidates = await self.generate_thoughts(task, context, n=3, completeness=completeness)

        # Step 2: Evaluate candidates
        scores = await self.evaluate_thoughts(candidates)

        # Step 3: Select best and runner-up
        best_node, fallback_node = self.select_best(candidates, scores)

        # Step 4: Expand selected path
        selected_path = await self.expand_node(best_node, context, max_depth=max_depth)

        # Build fallback trajectory
        fallback_path: Optional[List[ThoughtNode]] = None
        if fallback_node:
            fallback_path = [fallback_node]

        confidence = best_node.score or 0.8
        elapsed = time.time() - start_time
        logger.info(
            "[ThoughtPlanner] Plan constructed in %.2fs (steps=%d, confidence=%.2f)",
            elapsed, len(selected_path), confidence,
        )

        return ExecutionPlan(
            task=task,
            selected_path=selected_path,
            confidence=confidence,
            fallback_path=fallback_path,
            completeness=completeness,
            is_clarification_required=False,
            candidate_tool=completeness.candidate_tool if completeness else None,
            candidate_viability=completeness.candidate_viability if completeness else 1.0,
            missing_critical_params=completeness.missing_critical_params if completeness else [],
            partial_parameters=completeness.partial_parameters if completeness else {},
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Helper Methods
    # ─────────────────────────────────────────────────────────────────────────

    async def _call_llm(self, prompt: str, temperature: float = 0.3) -> str:
        """Helper to invoke AI handler cleanly."""
        if not self.ai_handler:
            return ""

        if hasattr(self.ai_handler, "generate"):
            try:
                resp = await self.ai_handler.generate(
                    [{"role": "user", "content": prompt}],
                    task="routing",
                    temperature=temperature,
                )
                return getattr(resp, "text", "") or ""
            except Exception as e:
                logger.warning("[ThoughtPlanner] ai_handler.generate error: %s", e)
                return ""
        elif hasattr(self.ai_handler, "chat"):
            resp = await self.ai_handler.chat(
                [{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return getattr(resp, "content", str(resp))
        return ""

    def _extract_json(self, text: str) -> Any:
        """Extract and parse JSON safely from LLM output."""
        if not text:
            return {}

        if self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
            parsed = self.ai_handler.try_parse_json(text)
            if isinstance(parsed, (dict, list)):
                return parsed

        # 1. Direct parse
        try:
            return json.loads(text.strip())
        except Exception:
            pass

        # 2. Markdown fenced block
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass

        # 3. Bracket boundary extraction
        for open_c, close_c in (("{", "}"), ("[", "]")):
            start = text.find(open_c)
            end = text.rfind(close_c)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except Exception:
                    pass

        return {}
