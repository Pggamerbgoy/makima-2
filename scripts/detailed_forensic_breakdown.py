import json

with open("scripts/benchmark_nomic_qwen_e2e_raw.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print("=== EXACT FAILURES IN BENCHMARK_NOMIC_QWEN_E2E ===")
for d in data:
    if not d["passed"]:
        print(f"ID: {d['id']:03d} | Cat: {d['category']} | Input: \"{d['input']}\"")
        print(f"  Exp Domain: {d['expected_domain']} | Act Domain: {d['actual_domain']}")
        print(f"  Exp Tool: {d['expected_tool']} | Act Tool: {d['actual_tool']}")
        print(f"  Params: {d['tool_params']}")
        print(f"  Failure Reason: {d['failure_reason']}")
        print(f"  Latency: {d['latency_ms']:.1f}ms | Hops: {d['llm_calls']}\n")
