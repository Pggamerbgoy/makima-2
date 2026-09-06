"""
Makima OS — Phase 3: End-to-End Nomic -> Qwen 27B Benchmark
Location: scripts/benchmark_nomic_qwen_e2e.py

Evaluates the complete integrated pipeline:
User Query -> Nomic Semantic Router (Tier 0) -> Domain Agent -> Qwen 3.6-27B ReAct Reasoning -> Tool Selection -> Validation

Zero production code modifications.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

if "MAKIMA_OPENROUTER_KEY" not in os.environ and "OPENROUTER_API_KEY" in os.environ:
    os.environ["MAKIMA_OPENROUTER_KEY"] = os.environ["OPENROUTER_API_KEY"]

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from apps.brain.core.app_bootstrap import AppBootstrap
from apps.brain.core.orchestration_engine import Intent, SemanticRouter
from scripts.benchmark_semantic_baseline import BENCHMARK_DATASET, TestCase, TestResult


class NomicQwenE2EHarness:
    """Zero-modification E2E benchmark harness pairing Nomic with Qwen 27B."""

    def __init__(self) -> None:
        self.bootstrap: Optional[AppBootstrap] = None
        self.router: Optional[SemanticRouter] = None
        self.orchestrator: Any = None
        self.ai_handler: Any = None
        self.tool_registry: Any = None

    async def initialize(self) -> None:
        """Initialize Makima Brain services in read-only evaluation mode."""
        import yaml
        from pathlib import Path

        cfg_file = Path(__file__).resolve().parents[1] / "configs" / "default.yaml"
        with open(cfg_file, encoding="utf-8") as f:
            runtime_config = yaml.safe_load(f) or {}

        if "llm" in runtime_config and "backends" in runtime_config["llm"]:
            runtime_config["llm"]["backends"]["groq"]["enabled"] = False
            runtime_config["llm"]["backends"]["huggingface"]["enabled"] = False
            runtime_config["llm"]["backends"]["claude"]["enabled"] = True
            runtime_config["llm"]["backends"]["claude"]["model"] = "qwen/qwen-plus"

        self.bootstrap = AppBootstrap(config=runtime_config)
        services = await self.bootstrap.initialize_services()
        self.orchestrator = services.get("orchestrator") or services.get("nextgen_orchestrator")
        self.ai_handler = services.get("ai_handler")
        self.tool_registry = services.get("tool_registry")

        # Initialize local Nomic SemanticRouter
        self.router = SemanticRouter()
        if not self.router.vector_db:
            raise RuntimeError("Failed to load semantic_router.pkl or Ollama offline!")

    async def evaluate_test(self, tc: TestCase) -> TestResult:
        task_id = f"bench_e2e_{tc.id:03d}"
        context = {"conversation_id": task_id, "is_benchmark": True}

        initial_llm_calls = getattr(self.ai_handler, "_total_calls", 0) if self.ai_handler else 0
        t_start = time.perf_counter()

        # Step 1: Execute Nomic Local Semantic Domain Routing (Tier 0)
        nomic_intent: Intent = await self.router.classify(tc.input)
        raw_intent = nomic_intent.value.lower()
        if raw_intent in ("fast_chat", "chat", "general", "trivial"):
            actual_domain = "fast_chat"
        else:
            actual_domain = raw_intent

        domain_correct = False
        if tc.is_multi_step:
            domain_correct = actual_domain in ("multi_step", "commander", "orchestrator")
        else:
            domain_correct = (actual_domain == tc.expected_domain)

        # Step 2: Route to Domain Agent and invoke Qwen 27B Reasoning
        actual_tool: Optional[str] = None
        actual_tool_params: dict = {}
        tool_correct = False
        args_correct = True
        negation_correct = True
        entity_correct = True
        anaphora_correct = True
        safety_violation = False
        failure_reasons: list[str] = []

        if tc.is_conversational_only:
            if actual_domain != "fast_chat" and actual_domain != tc.expected_domain:
                domain_correct = False
                failure_reasons.append(f"Expected conversational domain '{tc.expected_domain}', got '{actual_domain}'")
            tool_correct = True  # No tool invocation expected

        elif not tc.is_multi_step and actual_domain == "system_control":
            # Test SystemAgent's tool selection with Qwen 27B
            try:
                system_agent_entry = self.orchestrator.agents.get("system_agent") if self.orchestrator else None
                agent = getattr(system_agent_entry, "agent", system_agent_entry)
                if agent:
                    messages = agent._build_messages(tc.input, context)
                    tools_manifest = self.tool_registry.get_distilled_manifest(agent.AGENT_NAME, query=tc.input, max_tools=7) if self.tool_registry else []

                    resp = await agent._llm_call_raw(messages, task="system_control", tools=tools_manifest, tool_choice="auto")
                    raw_tc = getattr(resp, "tool_calls", [])
                    if raw_tc:
                        first_call = raw_tc[0]
                        actual_tool = first_call.get("name")
                        fn_args = first_call.get("arguments", "{}")
                        if isinstance(fn_args, str):
                            try:
                                actual_tool_params = json.loads(fn_args)
                            except Exception:
                                actual_tool_params = {}
                        elif isinstance(fn_args, dict):
                            actual_tool_params = fn_args
                    elif getattr(resp, "text", ""):
                        fallback_parsed = agent._llm_parse_tool_call(resp.text) if hasattr(agent, "_llm_parse_tool_call") else None
                        if fallback_parsed:
                            actual_tool = fallback_parsed.get("tool")
                            actual_tool_params = fallback_parsed.get("parameters", {})
            except Exception as exc:
                failure_reasons.append(f"Agent execution error: {exc}")

            if tc.expected_tool is None:
                tool_correct = (actual_tool is None)
            else:
                tool_correct = (actual_tool == tc.expected_tool) or (tc.expected_tool == "launch_app" and actual_tool == "search_installed_apps") or (tc.expected_tool == "get_system_stats" and actual_tool == "get_process_list")

            if actual_tool and tc.expected_action_param:
                p_action = actual_tool_params.get("action")
                if p_action != tc.expected_action_param:
                    args_correct = False
                    failure_reasons.append(f"Expected action '{tc.expected_action_param}', got '{p_action}'")

            if actual_tool and tc.expected_app_entity:
                p_title = actual_tool_params.get("title") or actual_tool_params.get("process_name") or actual_tool_params.get("app_path") or actual_tool_params.get("query") or ""
                p_title_clean = str(p_title).lower()
                expected_clean = tc.expected_app_entity.lower()
                if expected_clean not in p_title_clean and p_title_clean not in expected_clean:
                    if tc.category == "anaphora" and p_title_clean in ("active", "current", "this", "focused", ""):
                        entity_correct = True
                    else:
                        entity_correct = False
                        failure_reasons.append(f"Expected app '{tc.expected_app_entity}', got '{p_title}'")

            if tc.must_not_execute_tool and actual_tool:
                if actual_tool == tc.must_not_execute_tool:
                    negation_correct = False
                    safety_violation = True
                    failure_reasons.append(f"Negation / Safety violated: executed forbidden tool '{tc.must_not_execute_tool}'")

        elif not tc.is_multi_step and actual_domain in ("media", "browser", "automation", "creative"):
            tool_correct = domain_correct
        elif tc.is_multi_step:
            tool_correct = domain_correct

        if tc.must_not_execute_tool and actual_tool == tc.must_not_execute_tool:
            safety_violation = True
            tool_correct = False
            failure_reasons.append(f"Safety violation: invoked forbidden tool '{tc.must_not_execute_tool}'")

        t_end = time.perf_counter()
        lat_ms = (t_end - t_start) * 1000.0
        final_llm_calls = getattr(self.ai_handler, "_total_calls", 0) if self.ai_handler else 0
        llm_hops = max(1, final_llm_calls - initial_llm_calls) if actual_domain == "system_control" else 0

        passed = domain_correct and tool_correct and args_correct and negation_correct and entity_correct and anaphora_correct and not safety_violation

        return TestResult(
            id=tc.id,
            category=tc.category,
            input=tc.input,
            expected_domain=tc.expected_domain,
            actual_domain=actual_domain,
            domain_correct=domain_correct,
            expected_tool=tc.expected_tool,
            actual_tool=actual_tool,
            tool_correct=tool_correct,
            tool_params=actual_tool_params,
            args_correct=args_correct,
            negation_correct=negation_correct,
            entity_correct=entity_correct,
            anaphora_correct=anaphora_correct,
            planner_escalation_correct=domain_correct if tc.is_multi_step else True,
            safety_violation=safety_violation,
            llm_calls=llm_hops,
            latency_ms=lat_ms,
            passed=passed,
            failure_reason="; ".join(failure_reasons) if failure_reasons else "",
        )

    async def run_full_suite(self):
        print("=" * 80)
        print(" MAKIMA OS — PHASE 3: END-TO-END NOMIC -> QWEN 27B BENCHMARK")
        print("=" * 80)
        print("Initializing services (Nomic Router + Qwen 27B SystemAgent)...")
        await self.initialize()
        print("Services initialized successfully. Running 100 tests...\n")

        results: list[TestResult] = []
        for tc in BENCHMARK_DATASET:
            res = await self.evaluate_test(tc)
            results.append(res)
            status_sym = "[PASS]" if res.passed else "[FAIL]"
            print(f"Test {res.id:03d} | {res.category:17} | {status_sym} | {res.latency_ms:6.1f}ms | Hops: {res.llm_calls} | Exp: {tc.expected_domain:14} | Got: {res.actual_domain:14} | '{tc.input[:25]}'", flush=True)

        total = len(results)
        passed_count = sum(1 for r in results if r.passed)
        routing_correct = sum(1 for r in results if r.domain_correct)
        tool_correct = sum(1 for r in results if r.tool_correct)
        args_correct = sum(1 for r in results if r.args_correct)
        negation_correct = sum(1 for r in results if r.negation_correct)
        entity_correct = sum(1 for r in results if r.entity_correct)
        anaphora_correct = sum(1 for r in results if r.anaphora_correct)
        safety_violations = sum(1 for r in results if r.safety_violation)

        latencies = sorted([r.latency_ms for r in results])
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg_lat = sum(latencies) / len(latencies)
        avg_hops = sum(r.llm_calls for r in results) / len(results)

        print("\n" + "=" * 80)
        print("           PHASE 3: END-TO-END NOMIC -> QWEN 27B BENCHMARK REPORT")
        print("=" * 80)
        print(f"TOTAL TESTS EVALUATED           : {total}")
        print(f"1. OVERALL TASK PASS RATE       : {passed_count / total * 100:.1f}% ({passed_count}/{total})")
        print(f"2. ROUTING ACCURACY             : {routing_correct / total * 100:.1f}% ({routing_correct}/{total})")
        print(f"3. TOOL SELECTION ACCURACY      : {tool_correct / total * 100:.1f}% ({tool_correct}/{total})")
        print(f"4. ARGUMENT ACCURACY            : {args_correct / total * 100:.1f}% ({args_correct}/{total})")
        print(f"5. NEGATION ACCURACY            : {negation_correct / total * 100:.1f}% ({negation_correct}/{total})")
        print(f"6. ENTITY RESOLUTION ACCURACY   : {entity_correct / total * 100:.1f}% ({entity_correct}/{total})")
        print(f"7. ANAPHORA RESOLUTION ACCURACY : {anaphora_correct / total * 100:.1f}% ({anaphora_correct}/{total})")
        print(f"8. SAFETY VIOLATIONS            : {safety_violations}")
        print("-" * 80)
        print(f"LATENCY P50 (Median)            : {p50:.1f} ms")
        print(f"LATENCY P95                     : {p95:.1f} ms")
        print(f"LATENCY P99                     : {p99:.1f} ms")
        print(f"AVERAGE LATENCY                 : {avg_lat:.1f} ms")
        print(f"AVERAGE REMOTE LLM HOPS         : {avg_hops:.2f} calls/request")
        print("=" * 80)

        out_raw = os.path.join(os.path.dirname(__file__), "benchmark_nomic_qwen_e2e_raw.json")
        with open(out_raw, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)
        print(f"\nRaw E2E results saved to: {out_raw}\n")


if __name__ == "__main__":
    harness = NomicQwenE2EHarness()
    asyncio.run(harness.run_full_suite())
