import json

with open("scripts/benchmark_nomic_qwen_e2e_raw.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print("=" * 80)
print(" 1. TOP 10 HIGHEST LATENCY TESTS (P95+ TAIL)")
print("=" * 80)
sorted_lat = sorted(data, key=lambda x: x["latency_ms"], reverse=True)
for d in sorted_lat[:10]:
    print(f"Test {d['id']:03d} | {d['latency_ms']:7.1f}ms | Hops: {d['llm_calls']} | Passed: {d['passed']} | Domain: {d['actual_domain']} | \"{d['input']}\"")

print("\n" + "=" * 80)
print(" 2. ALL 9 FAILURES ROOT-CAUSE DISSECTION")
print("=" * 80)
failures = [d for d in data if not d["passed"]]
for d in failures:
    print(f"Test {d['id']:03d} | Category: {d['category']} | Input: \"{d['input']}\"")
    print(f"  - Domain: Expected '{d['expected_domain']}', Got '{d['actual_domain']}' (Correct: {d['domain_correct']})")
    print(f"  - Tool: Expected '{d['expected_tool']}', Got '{d['actual_tool']}' (Correct: {d['tool_correct']})")
    print(f"  - Tool Params: {d['tool_params']}")
    print(f"  - Failure Reason: {d['failure_reason']}\n")
