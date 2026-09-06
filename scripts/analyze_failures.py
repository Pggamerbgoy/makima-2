import json

with open("scripts/benchmark_autonomous_workflow_discovery_raw.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for m_key in ["qwen_3_5_plus", "nemotron_3_ultra"]:
    m_data = data[m_key]
    print("=" * 80)
    print("MODEL:", m_data["model_label"], f"(Passed: {m_data['goals_completed']}/{m_data['total_tasks']})")
    print("=" * 80)
    for t in m_data["trajectories"]:
        if not t["goal_completed"]:
            steps = [s["tool_name"] for s in t["steps"]]
            print(f"Task [{t['task_id']}] ({t['category']}) -> FAIL")
            print(f"  Goal: {t['goal']}")
            print(f"  Steps Called ({len(steps)}): {steps}")
            print(f"  Discovered Steps: {t['discovered_necessary_steps']}")
            print(f"  Unnecessary (C): {t['unnecessary_steps']}")
            print(f"  Recovery Success: {t['recovery_success']}")
            # print reason
            if t["task_id"] == "AWD-19":
                print("  Reason: Harness mock state bug (reloaded attribute)")
            elif len(steps) == 0:
                print("  Reason: Zero tool calls (model answered immediately with text instead of executing tools)")
            elif t["category"] == "Prerequisite & Missing-Precondition Discovery":
                print("  Reason: Direct single-step execution attempt (e.g. ran python directly without checking runtime, or looped CLI without falling back)")
            elif t["category"] == "Browser Exploration & Deep Navigation":
                print("  Reason: Missing deep multi-page step (e.g. didn't navigate back/forward to next page or stopped early)")
            print()
