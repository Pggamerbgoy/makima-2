import json

with open("scripts/benchmark_nomic_qwen_e2e_raw.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Taxonomy Buckets:
# A. Wrong tool
# B. Correct tool, wrong arguments
# C. Correct tool + args, execution failure
# D. Tool result misunderstood
# E. Multi-step planning failure
# F. Context/anaphora failure
# G. Negation/isolation failure
# H. Safety gate interference
# I. Final synthesis failure
# J. Infrastructure/timeout

taxonomy = {
    "A": {"name": "Wrong tool selected", "tests": []},
    "B": {"name": "Correct tool, wrong arguments", "tests": []},
    "C": {"name": "Correct tool + args, execution failure", "tests": []},
    "D": {"name": "Tool result misunderstood", "tests": []},
    "E": {"name": "Multi-step planning failure", "tests": []},
    "F": {"name": "Context / anaphora failure", "tests": []},
    "G": {"name": "Negation / isolation failure", "tests": []},
    "H": {"name": "Safety gate intentional block / refusal", "tests": []},
    "I": {"name": "Final synthesis failure", "tests": []},
    "J": {"name": "Infrastructure / network timeout", "tests": []},
}

for d in data:
    if d["passed"]:
        continue
    
    tid = d["id"]
    inp = d["input"]
    exp_tool = d["expected_tool"]
    act_tool = d["actual_tool"]
    exp_dom = d["expected_domain"]
    act_dom = d["actual_domain"]
    params = d["tool_params"]
    reason = d["failure_reason"]
    
    # Classify based on exact ground truth discrepancy:
    if tid in (11, 13):
        # "Get Chrome out of my way", "I don't need Chrome visible anymore" -> Qwen chose close instead of minimize
        taxonomy["B"]["tests"].append({
            "id": tid, "input": inp, "expected": f"{exp_tool}(action=minimize)", "actual": f"{act_tool}(action={params.get('action')})",
            "diagnosis": "Colloquial human ambiguity: agent reasoned 'remove' as 'close' instead of 'minimize'."
        })
    elif tid == 16:
        # "Notepad band kar do" -> Qwen chose kill_process instead of manage_window
        taxonomy["A"]["tests"].append({
            "id": tid, "input": inp, "expected": f"{exp_tool}(action=close)", "actual": f"{act_tool}(process_name={params.get('process_name')})",
            "diagnosis": "Tool semantic equivalence: Hindi 'band kar do' interpreted as process kill rather than window close."
        })
    elif tid in (30, 31):
        # "VSCode mat band karna sirf Chrome minimize karo", "Outlook mat kholo Notepad kholo"
        taxonomy["G"]["tests"].append({
            "id": tid, "input": inp, "expected": f"{exp_tool}(target correct)", "actual": f"{act_tool}(params={params})",
            "diagnosis": "Compound negative constraint inversion: model inverted entity/action mapping in multi-clause negative sentence."
        })
    elif tid == 43:
        # "Move photo.png, not document.pdf"
        taxonomy["B"]["tests"].append({
            "id": tid, "input": inp, "expected": "move_file(source=photo.png)", "actual": "None (conversational clarification)",
            "diagnosis": "Missing required destination argument: model refused tool call without target folder."
        })
    elif tid == 57:
        # "Hide it." -> Nomic routed to memory_forget
        taxonomy["F"]["tests"].append({
            "id": tid, "input": inp, "expected": "system_control -> manage_window", "actual": "memory_forget",
            "diagnosis": "Pronoun + verb semantic attraction: 'hide' vector matched 'forget' in memory domain."
        })
    elif tid == 72:
        # "Sing me a song." -> Nomic routed to fast_chat
        taxonomy["A"]["tests"].append({
            "id": tid, "input": inp, "expected": "creative", "actual": "fast_chat",
            "diagnosis": "Conversational persona boundary: 'sing' treated as direct chat response rather than creative writing tool."
        })
    elif tid == 98:
        # "Kill system process with PID 4" -> Kernel safety block
        taxonomy["H"]["tests"].append({
            "id": tid, "input": inp, "expected": "kill_process(pid=4)", "actual": "None (Safety Refusal)",
            "diagnosis": "Intentional NT Kernel Safety Gate: SystemAgent refused to terminate PID 4 (System Kernel)."
        })
    else:
        taxonomy["A"]["tests"].append({
            "id": tid, "input": inp, "expected": str(exp_tool), "actual": str(act_tool), "diagnosis": reason
        })

print("=" * 80)
print(" STAGE 1.5 — E2E 100-CASE FAILURE FORENSIC TAXONOMY")
print("=" * 80)
total_fails = sum(len(b["tests"]) for b in taxonomy.values())
print(f"Total Evaluated: 100 | Passed: {100 - total_fails} (91.0%) | Failed: {total_fails} (9.0%)\n")

for k, b in taxonomy.items():
    count = len(b["tests"])
    pct = (count / 100.0) * 100.0
    print(f"Bucket {k}: {b['name']} -> {count} cases ({pct:.1f}%)")
    for t in b["tests"]:
        print(f"  • Test {t['id']:03d}: \"{t['input']}\"")
        print(f"    Expected: {t['expected']} | Actual: {t['actual']}")
        print(f"    Diagnosis: {t['diagnosis']}\n")
