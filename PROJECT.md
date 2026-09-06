# Project: Makima Brain Stage 1 (P0-A) Architecture Optimization

## Architecture
Makima Brain is an intelligent desktop orchestrator. The request processing architecture consists of:
1. **WebSocket Ingestion (`apps/brain/main.py`)**: Receives raw user messages and session context.
2. **Orchestration Engine (`apps/brain/core/orchestration_engine.py`)**:
   - **Tier-0 Domain Routing (P0-A)**: Local `SemanticRouter` uses `nomic-embed-text` embeddings (768-dim) cosine similarity against 264 precomputed domain centroids in `apps/brain/data/semantic_router.pkl` with a threshold of 0.45.
   - **Escalation Fallback**: If Nomic confidence is low (< 0.45) or if Ollama is offline/timed out (> 1.5s), escalates to `_plan_intent_with_llm()` via remote Qwen.
   - **Conjunction / Multi-Step Splitter**: Detects multi-step conjunctions and dispatches to `CommanderAgent` / `DecompositionEngine` (`ParallelDAGScheduler`).
3. **Kernel & Dispatch (`apps/brain/core/kernel.py`)**: Dispatches the raw unaltered user prompt to registered domain agents (`SystemAgent`, `BrowserAgent`, `CodeAgent`, `MediaAgent`, etc.).
4. **Domain Agent Execution (`apps/brain/agents/`)**: Executes ReAct tool selection loops with strict downstream safety gates (`_pre_tool_gate`, `_check_destructive`, Kernel PID 4 protection, HITL confirmation).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | `SemanticRouter.classify_with_score` | Method returning `(Optional[Intent], float)` with cosine similarity score | M1 | ORIGINAL_REQUEST §R1, Survey 1 |
| 2 | `SemanticRouter.classify` threshold update | Returns `Intent` with confidence threshold 0.45, preserving backward compatibility | M1 | ORIGINAL_REQUEST §R1, Survey 1 |
| 3 | Tier-0 Nomic Promotion in `classify_intent` | Promote local Nomic router to primary classification step in `classify_intent()`, eliminating redundant Qwen intent-planning LLM hop | M1 | ORIGINAL_REQUEST §R1, Survey 1 |
| 4 | Raw Prompt Integrity Invariant | Ensure raw, unaltered user string is passed to `dispatch()` and domain agents without modification | M1 | ORIGINAL_REQUEST §R1, Survey 3 |
| 5 | Tier-0 Pure Routing (No Tool/Param Gen) | Semantic router outputs domain `Intent` only; zero tool/parameter generation | M1 | ORIGINAL_REQUEST §R1, Survey 3 |
| 6 | Low-Confidence Escalation Fallback | Escalate to `_plan_intent_with_llm()` when Nomic confidence < 0.45 instead of defaulting to FAST_CHAT | M2 | ORIGINAL_REQUEST §R2, Survey 1 |
| 7 | Ollama Outage / Timeout Resilient Fallback | Escalate to `_plan_intent_with_llm()` when Ollama is offline, times out (>1.5s), or raises exceptions | M2 | ORIGINAL_REQUEST §R2, Survey 1 |
| 8 | Multi-Step DAG & Conjunction Preservation | Conjunction splitting and `Intent.MULTI_STEP` dispatch to `CommanderAgent` / `DecompositionEngine` preserved intact | M2 | ORIGINAL_REQUEST §R2, Survey 3 |
| 9 | Default Fallback Behavior | Fall back to `Intent.FAST_CHAT` only if both Tier-0 Nomic and remote `_plan_intent_with_llm` fail | M2 | ORIGINAL_REQUEST §R2, Survey 1 |
| 10 | Zero P0-B Scope Isolation | Strict boundary: zero edits to `BaseAgent._execute_with_tools` or final synthesis logic | M1/M2/M3 | ORIGINAL_REQUEST §R3, Survey 3 |
| 11 | Benchmark Gate: Nomic Router (≥ 98.0%) | Isolated benchmark verification (`scripts/benchmark_nomic_router.py`) | M3 | ORIGINAL_REQUEST §Acceptance Criteria, Survey 2 |
| 12 | Benchmark Gate: Brain Module Audit (95/95) | Module import & registration audit (`scripts/audit_all_brain_modules.py`) | M3 | ORIGINAL_REQUEST §Acceptance Criteria, Survey 2 |
| 13 | Benchmark Gate: E2E Nomic + Qwen (≥ 91.0%) | E2E task accuracy, 0 safety violations, 100% negation obedience, 1 LLM hop (`scripts/benchmark_nomic_qwen_e2e.py`) | M3 | ORIGINAL_REQUEST §Acceptance Criteria, Survey 2 |
| 14 | Benchmark Gate: Agent Fidelity (≥ 95.0%) | Tool/verb/entity fidelity & zero side-effect safety (`scripts/benchmark_agent_fidelity.py`) | M3 | ORIGINAL_REQUEST §Acceptance Criteria, Survey 2 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Tier-0 Nomic Promotion & Fast-Path Routing | `apps/brain/core/orchestration_engine.py`: Add `classify_with_score()`, update `classify()`, promote `SemanticRouter` to primary in `classify_intent()`, eliminate redundant LLM hop on confidence >= 0.45 | None | PLANNED |
| 2 | Low-Confidence Fallback, Outage Escalation & DAG Invariants | `apps/brain/core/orchestration_engine.py`: Low confidence (<0.45) escalation to `_plan_intent_with_llm()`, Ollama timeout/offline exception handling, multi-step conjunction routing preservation, safe fallback cascade | M1 | PLANNED |
| 3 | E2E Benchmark Verification, Adversarial Gate & Forensic Audit | Run all 4 benchmark scripts (`benchmark_nomic_router.py`, `audit_all_brain_modules.py`, `benchmark_nomic_qwen_e2e.py`, `benchmark_agent_fidelity.py`), Reviewer review, Challenger stress tests, Forensic Auditor integrity veto check | M2 | PLANNED |

## Interface Contracts

### `SemanticRouter` (`apps/brain/core/orchestration_engine.py`)
```python
class SemanticRouter:
    async def classify_with_score(self, message: str) -> tuple[Optional[Intent], float]:
        """Returns (best_intent, cosine_similarity_score). Returns (None, 0.0) on error/timeout/unloaded."""
        ...

    async def classify(self, message: str) -> Intent:
        """Returns best_intent if score >= 0.45, else Intent.FAST_CHAT."""
        ...
```

### `OrchestrationEngine.classify_intent` (`apps/brain/core/orchestration_engine.py`)
```python
async def classify_intent(self, message: str, context: Optional[dict] = None) -> IntentResult:
    """
    Tier-0 Primary: SemanticRouter.classify_with_score(message)
    - If score >= 0.45: return IntentResult(intent=intent, confidence=score, source="nomic_tier0")
    - If score < 0.45 or error/timeout: escalate to self._plan_intent_with_llm(message, context)
    - If LLM planner returns None: return IntentResult(intent=Intent.FAST_CHAT, confidence=0.0, source="fallback")
    """
    ...
```

## Code Layout
- `apps/brain/core/orchestration_engine.py`: Single modification target for Stage 1 (P0-A).
- `apps/brain/data/semantic_router.pkl`: Local Nomic vector database (read-only).
- `apps/brain/agents/base_agent.py`: Out of scope (P0-B protected).
- `scripts/benchmark_nomic_router.py`: Verification benchmark 1.
- `scripts/audit_all_brain_modules.py`: Verification benchmark 2.
- `scripts/benchmark_nomic_qwen_e2e.py`: Verification benchmark 3.
- `scripts/benchmark_agent_fidelity.py`: Verification benchmark 4.
