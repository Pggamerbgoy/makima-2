"""
Makima OS — Isolated Nomic Semantic Router Benchmark
Location: scripts/benchmark_nomic_router.py

Evaluates the local Nomic embedding-based SemanticRouter (nomic-embed-text via Ollama)
on the exact same 100-test benchmark suite for pure domain classification.

Zero production code modifications.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from apps.brain.core.orchestration_engine import Intent, SemanticRouter
from scripts.benchmark_semantic_baseline import BENCHMARK_DATASET, TestCase


@dataclass
class NomicTestResult:
    id: int
    category: str
    input: str
    expected_domain: str
    actual_domain: str
    domain_correct: bool
    latency_ms: float


async def run_nomic_benchmark():
    print("=" * 80)
    print(" MAKIMA BRAIN — ISOLATED NOMIC SEMANTIC ROUTER BENCHMARK")
    print("=" * 80)
    print("Initializing local SemanticRouter (nomic-embed-text on Ollama 11434)...")

    router = SemanticRouter()
    if not router.vector_db:
        print("ERROR: Failed to load semantic_router.pkl or Ollama is offline!")
        return

    print(f"Loaded {len(router.vector_db['labels'])} centroid embeddings. Running 100 tests...\n")

    results: list[NomicTestResult] = []

    for tc in BENCHMARK_DATASET:
        t0 = time.perf_counter()
        intent: Intent = await router.classify(tc.input)
        t_end = time.perf_counter()
        lat_ms = (t_end - t0) * 1000.0

        actual_domain = intent.value.lower()
        if actual_domain in ("fast_chat", "chat", "general", "trivial"):
            actual_domain = "fast_chat"

        # Domain correctness mapping
        domain_correct = False
        if tc.is_multi_step:
            domain_correct = actual_domain in ("multi_step", "commander")
        else:
            domain_correct = (actual_domain == tc.expected_domain)

        res = NomicTestResult(
            id=tc.id,
            category=tc.category,
            input=tc.input,
            expected_domain=tc.expected_domain,
            actual_domain=actual_domain,
            domain_correct=domain_correct,
            latency_ms=lat_ms,
        )
        results.append(res)

        status_sym = "[PASS]" if res.domain_correct else "[FAIL]"
        print(f"Test {res.id:03d} | {res.category:17} | {status_sym} | {res.latency_ms:5.1f}ms | Expected: {tc.expected_domain:14} | Got: {actual_domain:14} | '{tc.input[:30]}'", flush=True)

    # ── AGGREGATE METRICS CALCULATION ──
    total = len(results)
    overall_correct = sum(1 for r in results if r.domain_correct)
    
    # Category subsets
    sys_tests = [r for r in results if r.expected_domain == "system_control"]
    sys_correct = sum(1 for r in sys_tests if r.domain_correct)

    chat_tests = [r for r in results if r.expected_domain == "fast_chat"]
    chat_correct = sum(1 for r in chat_tests if r.domain_correct)

    multi_tests = [r for r in results if r.category == "multi_step"]
    multi_correct = sum(1 for r in multi_tests if r.domain_correct)

    hinglish_tests = [r for r in results if r.category == "hinglish"]
    hinglish_correct = sum(1 for r in hinglish_tests if r.domain_correct)

    negation_tests = [r for r in results if r.category == "negation"]
    negation_correct = sum(1 for r in negation_tests if r.domain_correct)

    ambiguity_tests = [r for r in results if r.category in ("system_vs_chat", "media_vs_chat")]
    ambiguity_correct = sum(1 for r in ambiguity_tests if r.domain_correct)

    # Latency percentiles
    latencies = sorted([r.latency_ms for r in results])
    p50 = latencies[int(len(latencies) * 0.50)]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    avg_lat = sum(latencies) / len(latencies)
    min_lat = min(latencies)
    max_lat = max(latencies)

    # Output Summary Table
    print("\n" + "=" * 80)
    print("              ISOLATED NOMIC SEMANTIC ROUTER BENCHMARK REPORT")
    print("=" * 80)
    print(f"TOTAL TESTS EVALUATED           : {total}")
    print(f"1. OVERALL ROUTING ACCURACY     : {overall_correct / total * 100:.1f}% ({overall_correct}/{total})")
    print("-" * 80)
    print(f"2. SYSTEM_CONTROL ACCURACY      : {sys_correct / len(sys_tests) * 100:.1f}% ({sys_correct}/{len(sys_tests)})")
    print(f"3. CHAT ACCURACY                : {chat_correct / len(chat_tests) * 100:.1f}% ({chat_correct}/{len(chat_tests)})")
    print(f"4. MULTI_STEP ACCURACY          : {multi_correct / len(multi_tests) * 100:.1f}% ({multi_correct}/{len(multi_tests)})")
    print(f"5. HINGLISH ROUTING ACCURACY    : {hinglish_correct / len(hinglish_tests) * 100:.1f}% ({hinglish_correct}/{len(hinglish_tests)})")
    print(f"6. NEGATION ROUTING ACCURACY    : {negation_correct / len(negation_tests) * 100:.1f}% ({negation_correct}/{len(negation_tests)})")
    print(f"7. AMBIGUOUS / ADVERSARIAL ACC  : {ambiguity_correct / len(ambiguity_tests) * 100:.1f}% ({ambiguity_correct}/{len(ambiguity_tests)})")
    print("-" * 80)
    print(f"LATENCY P50 (Median)            : {p50:.1f} ms")
    print(f"LATENCY P95                     : {p95:.1f} ms")
    print(f"LATENCY P99                     : {p99:.1f} ms")
    print(f"LATENCY AVERAGE                 : {avg_lat:.1f} ms")
    print(f"LATENCY MIN / MAX               : {min_lat:.1f} ms / {max_lat:.1f} ms")
    print("=" * 80)

    # Save raw per-test artifact
    artifact_path = os.path.join(os.path.dirname(__file__), "benchmark_nomic_raw.json")
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)
    print(f"\nRaw per-test Nomic predictions saved to: {artifact_path}\n")


if __name__ == "__main__":
    asyncio.run(run_nomic_benchmark())
