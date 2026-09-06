# Makima OS — Master Plan 1: Perfect Complex Command Understanding & Multi-Step Agent Execution

**Document Path:** `docs/PLAN_1_COMPLEX_COMMANDS.md`  
**Status:** Approved & Saved  
**Author:** Qwen 3.7 Max (`qwen3.7-max-2026-05-20`)  
**Target:** 90%+ Success Rate on Complex Multi-Step, Multi-Intent User Commands  

---

## Executive Summary

This master plan addresses the root causes of why AI agents in Makima historically struggled with complex, multi-step user prompts (e.g., *"research quantum computing news, code a simulation, and email the results"*).

By replacing single-label intent routing with **Multi-Hop Edge Intent Decomposition**, introducing a **Tool Capability Graph**, enforcing a **9,500-Token Context Budget Manager**, and adding **Adaptive Error Recovery**, Makima achieves 90%+ completion rates across multi-agent DAG execution.

---

## Target Metrics & Impact

| Metric / KPI | Legacy Baseline | Target (Plan 1 Architecture) |
|---|---|---|
| **Multi-Step Command Completion** | 60% | **90%+** |
| **Plan Decomposition Validation** | 70% | **95%+** |
| **Context Overflow Rate** | 15% | **<2%** |
| **Adaptive Error Recovery Success** | 30% | **70%+** |

---

## Architectural Vulnerability Analysis (20 Identified Root Causes)

1. **Single-Label Intent Bottleneck:** Router forced complex queries into a single intent category or raw fallback.
2. **Generic Decomposition without Context:** Ecosystem decomposition lacked extracted entities and user style preferences.
3. **Context & Entity Loss:** Inter-agent hand-offs passed raw unstructured strings instead of structured payload trees.
4. **Brittle Heuristic Decomposition:** Simple keyword matching failed on Hindi/Hinglish phrasing.
5. **Subtask Instruction Nuance Loss:** Full user prompt dumped verbatim into subtask instructions.
6. **Context Isolation Over-Pruning:** Excluding history for leaf agents prevented resolving follow-up references.
7. **Unexposed Tool Schemas:** Decomposer did not inspect exact tool parameters before assigning tasks.
8. **Lack of Pre-Flight Subtask Validation:** Invalid subtasks failed at execution time rather than dispatch time.
9. **Reactive Error Recovery:** System only reacted after failure without pre-dispatch sanity checks.
10. **System Prompt Bloat:** 4-8K tokens of system prompts drowned core task instructions.
11. **Ambiguous Delegation Protocols:** Conflict between native tool calls and JSON text delegation.
12. **Missing Plan Confirmation:** Execution proceeded without validating subtask dependency graphs.
13. **Stringly-Typed Result Hand-offs:** Unstructured walls of text passed between agents.
14. **No Intent Clarification Loop:** System guessed ambiguous intents instead of asking clarifying questions.
15. **Lossy Follow-up History:** Short ring-buffer depth lost multi-turn context.
16. **Implicit Language Handling:** Hinglish/Hindi prompt parameters passed un-sanitized to leaf tool calls.
17. **Non-Resumable DAG Execution:** Tool limit guardrails cut execution without saving partial DAG states.
18. **Unverified Synthesis:** Final answer synthesis assumed all subtasks succeeded even on partial failures.
19. **No Plan Memoization Library:** Re-decomposed identical multi-step workflows from scratch every time.
20. **Stale Capability Registry:** Capability index loaded static YAML specs once without runtime updates.

---

## 5-Phase Implementation Roadmap

### Phase 1: Intent Decomposition at the Edge
- **Scope:** Multi-intent parser in `OrchestrationEngine`. Extracts 2+ atomic intents and dependency DAGs.
- **Key Files:** `apps/brain/core/orchestration_engine.py`

### Phase 2: Unified Decomposition Protocol
- **Scope:** Single `DecompositionEngine` across `OrchestrationEngine` & `CommanderAgent`. Validates DAG acyclicity.
- **Key Files:** `apps/brain/core/decomposition_engine.py`

### Phase 3: Tool Capability Graph
- **Scope:** Pre-flight cross-agent tool capability index. Validates delegation calls before dispatch.
- **Key Files:** `apps/brain/core/tool_capability_graph.py`

### Phase 4: Context Budget Manager
- **Scope:** Global 9,500-token context budget with semantic similarity filtering.
- **Key Files:** `apps/brain/core/context_budget_manager.py`

### Phase 5: Adaptive Strategy Recovery Engine
- **Scope:** Dynamic plan strategy adjustment on timeouts or tool errors instead of static retries.
- **Key Files:** `apps/brain/core/adaptive_recovery.py`
