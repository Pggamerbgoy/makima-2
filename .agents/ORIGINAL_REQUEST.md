# Original User Request

## Initial Request — 2026-08-16T15:31:46Z

Implement Stage 1 (P0-A) of the Makima Brain architecture optimization: promote the validated local Nomic SemanticRouter to primary Tier-0 semantic domain routing in `apps/brain/core/orchestration_engine.py` and eliminate the redundant remote Qwen intent-planning LLM hop, strictly preserving raw prompt integrity, safety gates, and multi-step DAG routing.

Working directory: c:\code\makima
Integrity mode: development

## Requirements

### R1. Tier-0 Nomic Promotion & Fast-Path Domain Routing (P0-A)
- In `apps/brain/core/orchestration_engine.py`, make `SemanticRouter.classify(message)` the primary Tier-0 domain classifier in `classify_intent()`.
- Eliminate the redundant remote Qwen intent-classification LLM hop (`_plan_intent_with_llm`) from the normal high-confidence routing path.
- The router must answer ONLY: "Which domain/agent should reason about this request?" (Zero tool generation, zero parameter generation, zero action mapping in the router).
- Ensure the selected domain agent receives the COMPLETE, UNALTERED original user prompt.

### R2. Low-Confidence & Outage Escalation Fallback
- When Nomic returns low confidence (<0.45) or uncertain classification: escalate directly to the existing remote semantic intent reasoner (`_plan_intent_with_llm`) using the full original user message. Do NOT silently default to `FAST_CHAT` or guess arbitrary domains.
- When Ollama/Nomic is offline, times out (>1.5s), or encounters exceptions: fail fast without blocking beyond the configured router timeout, then escalate to `_plan_intent_with_llm` as the resilient remote reasoning fallback.
- Preserve existing DAG / Multi-Step conjunction routing to `DecompositionEngine` / `CommanderAgent`.

### R3. Strict Architecture Invariants & Zero P0-B Scope
- DO NOT implement P0-B (do not modify `BaseAgent._execute_with_tools` or final synthesis behavior).
- DO NOT use keyword action maps, regex action shortcuts, fuzzy matching, or query→action caches.
- Preserve all existing safety invariants: `_pre_tool_gate`, `_check_destructive`, HITL confirmation, critical process protection, and zero speculative destructive execution.

## Acceptance Criteria

### Execution & Verification Gates
- [ ] Nomic routing benchmark ≥ 98.0% on the frozen 100-test suite (`scripts/benchmark_nomic_router.py`).
- [ ] E2E benchmark ≥ 91.0%, 0 safety violations, 100% negation obedience, 100% action isolation (`scripts/benchmark_nomic_qwen_e2e.py`).
- [ ] Agent fidelity ≥ 95.0% (`scripts/benchmark_agent_fidelity.py`).
- [ ] Full module audit passes 95/95 modules with 0 errors (`scripts/audit_all_brain_modules.py`).
- [ ] For high-confidence single-step Nomic-routed requests: exactly 1 remote Qwen reasoning hop and 0 duplicate intent-planning hops.
- [ ] Low-confidence Nomic results escalate to `_plan_intent_with_llm` (not silent `FAST_CHAT`).
- [ ] Nomic/Ollama failure escalates to `_plan_intent_with_llm` (not an arbitrary domain).
- [ ] Original user prompt reaches the selected domain agent unchanged.
- [ ] Tier-0 performs domain routing only; no tool/action/parameter generation or execution.
- [ ] No P0-B changes to `BaseAgent._execute_with_tools` or final synthesis.

## 2026-08-21T07:03:42Z

Execute complete P0/P1 runtime correctness and wiring repairs on the Makima repository (c:\code\makima) so that all implemented systems have proven end-to-end execution paths.

Working directory: c:\code\makima
Integrity mode: development

## Requirements

### R1. Core Runtime & Safety Gaps
- InvariantVerifier must never convert tool failure prefixes or verifier exceptions into success (VERIFIED_FAILURE / UNVERIFIABLE).
- AppBootstrap must fail fast if critical dependencies (ai_handler, orchestrator, orchestration_engine) fail during lifespan startup.
- Kernel background agent tasks must log unhandled exceptions on completion.

### R2. Voice & Messaging Wiring
- ProductionVoiceEngine must expose `speak()` and `synthesize_speech_stream()` adapters matching VoiceFacade expectations.
- MessagingAgent `approve_send()` must honestly report delivery state rather than faking remote delivery when no network provider is connected.

### R3. Memory, Learning & Context Pipeline
- LearningEngine must reload active behavioral rules from SQLite into in-memory cache upon system startup.
- ContextBuilder must ensure recalled long-term episodic memory (`tier_3_memory`) and learned behavioral rules (`tier_4_rules`) are active and injected for agents executing user tasks.
- Window world-state domain cache must be populated dynamically so `win_count` and window awareness are available.

### R4. Tool & Capability Mesh Registration
- Register all 6 domain-specific tool modules (calendar, finance, notifications, data, devops, security) in `register_core_tools`.
- Ensure ToolRegistry tools are imported and discoverable by the CapabilityMesh authority.

### R5. Agent Execution Pipeline & Multi-Step Routing
- Wire multi-step intent routing (`commander_agent` / `EcosystemAgent`) properly to the production coordinator.
- Hook conversation turns into personality processing so dynamic system prompt updates are maintained.
- Ensure specialized agents utilize ExecutionRuntime and verifiers for tool operations.

## Acceptance Criteria

### Automated Testing & Production Verification
- [ ] All tests in `tests/test_phase14_domain_verifiers.py`, `tests/test_phase12_voice_pipeline.py`, `tests/test_p0_learning_roundtrip.py`, and `tests/test_tool_registry_runtime.py` pass cleanly.
- [ ] InvariantVerifier returns `VERIFIED_FAILURE` when given a `[Failed]` tool output string.
- [ ] `VoicePipeline` has both `speak` and `synthesize_speech_stream` methods available.
- [ ] `LearningEngine` populates `_behavior_rules_cache` on startup from SQLite.
- [ ] No regression in WebSocket message dispatch or existing agent tools.
- [ ] Full repository test suite passes.

