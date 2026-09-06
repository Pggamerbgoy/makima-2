"""
Makima OS — P0-B1: Unseen Action Semantic Grounding Benchmark
Location: scripts/benchmark_unseen_action_grounding.py

Evaluates 30 NEW, unseen colloquial and multi-constraint paraphrases testing:
- Minimize vs. Close vs. Kill vs. Focus semantic distinction
- Negation and constraint obedience
- Hinglish and idiomatic English phrasing
"""
import asyncio
import json
import os
import sys
import time
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from apps.brain.core.app_bootstrap import AppBootstrap

UNSEEN_GROUNDING_DATASET = [
    # ── Minimize / Hide / Screen Clearing (Non-Destructive) ─────────────
    {"id": 1, "input": "Chrome ko screen se hata do.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "chrome", "forbidden_tools": ["kill_process"]},
    {"id": 2, "input": "VSCode hatao saamne se.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "vscode", "forbidden_tools": ["kill_process"]},
    {"id": 3, "input": "Get Excel out of my face.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "excel", "forbidden_tools": ["kill_process"]},
    {"id": 4, "input": "Chrome abhi nahi dekhna mujhe.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "chrome", "forbidden_tools": ["kill_process"]},
    {"id": 5, "input": "Bhai screen saaf karo Word hata ke.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "word", "forbidden_tools": ["kill_process"]},
    {"id": 6, "input": "Hide Slack for now.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "slack", "forbidden_tools": ["kill_process"]},
    {"id": 7, "input": "Put Discord away.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "discord", "forbidden_tools": ["kill_process"]},
    {"id": 8, "input": "Terminal ko neeche kar do.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "terminal", "forbidden_tools": ["kill_process"]},
    {"id": 9, "input": "Tuck away Telegram.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "telegram", "forbidden_tools": ["kill_process"]},
    {"id": 10, "input": "Postman ko background me bhej do chupao.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "postman", "forbidden_tools": ["kill_process"]},

    # ── Close / Exit / Terminate Gracefully ─────────────────────────────
    {"id": 11, "input": "Notepad ko bilkul band kar do.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "notepad", "forbidden_actions": ["minimize"]},
    {"id": 12, "input": "Exit Calculator completely.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "calculator", "forbidden_actions": ["minimize"]},
    {"id": 13, "input": "I am done with Word, shut its window.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "word", "forbidden_actions": ["minimize"]},
    {"id": 14, "input": "Paint ko band karo.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "paint", "forbidden_actions": ["minimize"]},
    {"id": 15, "input": "Close Edge right now.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "edge", "forbidden_actions": ["minimize"]},

    # ── Focus / Bring to Front ──────────────────────────────────────────
    {"id": 16, "input": "Spotify saamne lao.", "expected_tool": "manage_window", "expected_action": "focus", "expected_app": "spotify", "forbidden_actions": ["close", "minimize"]},
    {"id": 17, "input": "Bring VS Code back into focus.", "expected_tool": "manage_window", "expected_action": "focus", "expected_app": "vscode", "forbidden_actions": ["close", "minimize"]},
    {"id": 18, "input": "Outlook khol ke aage karo.", "expected_tool": "manage_window", "expected_action": "focus", "expected_app": "outlook", "forbidden_actions": ["close", "minimize"]},
    {"id": 19, "input": "Switch to Chrome.", "expected_tool": "manage_window", "expected_action": "focus", "expected_app": "chrome", "forbidden_actions": ["close", "minimize"]},
    {"id": 20, "input": "Terminal par jump karo.", "expected_tool": "manage_window", "expected_action": "focus", "expected_app": "terminal", "forbidden_actions": ["close", "minimize"]},

    # ── Negation & Disambiguation Constraints ──────────────────────────
    {"id": 21, "input": "Outlook close mat karna bas chupao.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "outlook", "forbidden_actions": ["close"]},
    {"id": 22, "input": "Discord minimize mat karo band hi kar do.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "discord", "forbidden_actions": ["minimize"]},
    {"id": 23, "input": "Don't terminate Chrome, just get it out of view.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "chrome", "forbidden_tools": ["kill_process"], "forbidden_actions": ["close"]},
    {"id": 24, "input": "Don't hide Notepad, close it.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "notepad", "forbidden_actions": ["minimize"]},
    {"id": 25, "input": "VSCode ko band mat karna sirf focus karo.", "expected_tool": "manage_window", "expected_action": "focus", "expected_app": "vscode", "forbidden_actions": ["close", "minimize"]},

    # ── Subtle Colloquial Hinglish Phrasings ───────────────────────────
    {"id": 26, "input": "Chrome bohot distract kar raha hai, hata do.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "chrome", "forbidden_tools": ["kill_process"]},
    {"id": 27, "input": "Excel ka kaam ho gaya, band karo isko.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "excel"},
    {"id": 28, "input": "PowerPoint thodi der ke liye chupao.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "powerpoint", "forbidden_tools": ["kill_process"]},
    {"id": 29, "input": "Slack bandh karna hai, abhi meeting khatam ho gayi.", "expected_tool": "manage_window", "expected_action": "close", "expected_app": "slack"},
    {"id": 30, "input": "Spotify ko peeche bhej do.", "expected_tool": "manage_window", "expected_action": "minimize", "expected_app": "spotify", "forbidden_tools": ["kill_process"]}
]


VALID_DISCOVERY_MAP: dict[str, set[str]] = {
    "launch_app": {"search_installed_apps"},
    "kill_process": {"get_process_list", "manage_window"},
    "manage_window": {"get_process_list", "search_installed_apps", "kill_process"},
    "get_system_stats": {"get_process_list"},
    "get_process_list": {"get_system_stats"},
}

APP_ALIASES: dict[str, set[str]] = {
    "vscode": {"vscode", "code", "visual studio code"},
    "word": {"word", "winword", "msword", "microsoft word"},
    "excel": {"excel", "msexcel", "microsoft excel"},
    "calculator": {"calculator", "calc"},
    "git": {"git", "git bash", "git-bash"},
    "outlook": {"outlook", "msoutlook", "microsoft outlook"},
    "chrome": {"chrome", "google chrome"},
    "notepad": {"notepad", "notepad.exe"},
    "discord": {"discord", "discord.exe"},
    "spotify": {"spotify", "spotify.exe"},
    "node": {"node", "node.exe", "nodejs"},
    "python": {"python", "python.exe", "python3"},
    "slack": {"slack", "slack.exe"},
    "powerpoint": {"powerpoint", "powerpnt", "mspowerpoint", "ppt"},
    "terminal": {"terminal", "cmd", "powershell", "wt", "windows terminal"},
    "paint": {"paint", "mspaint"},
    "edge": {"edge", "msedge", "microsoft edge"},
    "postman": {"postman", "postman.exe"},
}


def extract_target_entity(params: dict) -> str:
    candidates = [
        "title",
        "process_name",
        "app_path",
        "app_name",
        "service_name",
        "name",
        "query",
        "filter_name",
        "target",
        "path",
    ]
    for c in candidates:
        if c in params and params[c]:
            return str(params[c]).strip().lower()
    return ""


def match_target_entity(exp_val: str, act_val: str) -> bool:
    exp = exp_val.lower().strip()
    act = act_val.lower().strip()
    if not exp:
        return True
    if not act:
        return False
    if exp in act or act in exp:
        return True
    exp_aliases = APP_ALIASES.get(exp, {exp})
    for alias in exp_aliases:
        if alias in act or act in alias:
            return True
    return False


async def run_unseen_benchmark():
    print("=" * 80)
    print(" UNSEEN ACTION SEMANTIC GROUNDING BENCHMARK (30 PARAPHRASES)")
    print("=" * 80)
    
    bootstrap = AppBootstrap()
    services = await bootstrap.initialize_services()
    orch = services.get("orchestrator")
    tool_reg = services.get("tool_registry")
    sys_agent_entry = orch.agents.get("system_agent") if orch else None
    agent = getattr(sys_agent_entry, "agent", sys_agent_entry)
    
    if not agent:
        print("[ERROR] SystemAgent could not be loaded!")
        return

    key = os.environ.get("DASHSCOPE_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    
    results = []
    t_start_all = time.perf_counter()

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        for item in UNSEEN_GROUNDING_DATASET:
            t_start = time.perf_counter()
            raw_msg = item["input"]
            context = {"conversation_id": f"unseen_{item['id']}", "is_benchmark": True}
            
            messages = agent._build_messages(raw_msg, context)
            tools_manifest = tool_reg.get_distilled_manifest(agent.AGENT_NAME, query=raw_msg, max_tools=12) if tool_reg else []
            
            body = {
                "model": "qwen-plus",
                "messages": messages,
                "tools": tools_manifest,
                "temperature": 0.1
            }
            
            act_tool = None
            act_params = {}
            
            try:
                resp = await client.post("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions", headers=headers, json=body)
                if resp.status_code == 200:
                    choice = resp.json()["choices"][0]["message"]
                    raw_calls = choice.get("tool_calls", []) or []
                    if raw_calls:
                        fn = raw_calls[0].get("function", {})
                        act_tool = fn.get("name")
                        args_str = fn.get("arguments", "{}")
                        try:
                            act_params = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except Exception:
                            act_params = {}
            except Exception as e:
                print(f"[Err] {e}")

            t_end = time.perf_counter()
            lat_ms = (t_end - t_start) * 1000.0
            
            act_action = act_params.get("action")
            act_target = extract_target_entity(act_params)
            
            # Evaluate Dimensions
            exact_tool_match = (act_tool == item["expected_tool"])
            is_discovery = act_tool in VALID_DISCOVERY_MAP.get(item["expected_tool"], set())
            tool_trajectory_correct = exact_tool_match or is_discovery
            
            if act_tool is None:
                action_correct = False
            elif is_discovery:
                action_correct = True
            elif item.get("expected_action"):
                exp_action = item["expected_action"].lower()
                action_correct = (str(act_action).lower() == exp_action)
            else:
                action_correct = True
                
            target_correct = match_target_entity(item.get("expected_app", ""), act_target)
            
            forbidden_tool_hit = any(ft == act_tool for ft in item.get("forbidden_tools", []))
            forbidden_action_hit = any(fa == act_action for fa in item.get("forbidden_actions", []))
            constraint_correct = not forbidden_tool_hit and not forbidden_action_hit
            
            passed = tool_trajectory_correct and action_correct and target_correct and constraint_correct
            
            status_tag = "✅ PASS" if passed else "❌ FAIL"
            print(f"[{item['id']:02d}/30] \"{raw_msg[:30]:30}\" -> {status_tag} | Tool: {str(act_tool):16} | Act: {str(act_action):10} | Target: {act_target:15} | Lat: {lat_ms:.1f}ms")
            
            results.append({
                "id": item["id"],
                "input": raw_msg,
                "passed": passed,
                "exact_tool_match": exact_tool_match,
                "tool_trajectory_correct": tool_trajectory_correct,
                "action_correct": action_correct,
                "target_correct": target_correct,
                "constraint_correct": constraint_correct,
                "expected_tool": item["expected_tool"],
                "actual_tool": act_tool,
                "expected_action": item.get("expected_action"),
                "actual_action": act_action,
                "expected_app": item.get("expected_app"),
                "actual_app": act_target,
                "forbidden_tool_hit": forbidden_tool_hit,
                "forbidden_action_hit": forbidden_action_hit,
                "latency_ms": lat_ms
            })

    t_total = time.perf_counter() - t_start_all
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    exact_count = sum(1 for r in results if r["exact_tool_match"])
    traj_count = sum(1 for r in results if r["tool_trajectory_correct"])
    act_count = sum(1 for r in results if r["action_correct"])
    target_count = sum(1 for r in results if r["target_correct"])
    constraint_count = sum(1 for r in results if r["constraint_correct"])
    pass_rate = (passed_count / total) * 100.0
    
    print("\n" + "=" * 80)
    print("      UNSEEN ACTION SEMANTIC GROUNDING BENCHMARK REPORT")
    print("=" * 80)
    print(f"TOTAL PARAPHRASES EVALUATED      : {total}")
    print(f"1. OVERALL SEMANTIC PASS RATE    : {pass_rate:.1f}% ({passed_count}/{total})")
    print("-" * 80)
    print(f"2. TOOL TRAJECTORY FIDELITY      : {traj_count / total * 100:.1f}% ({traj_count}/{total})")
    print(f"   └─ EXACT TOOL 1:1 MATCH       : {exact_count / total * 100:.1f}% ({exact_count}/{total}) (Informational)")
    print(f"3. ACTION / VERB FIDELITY        : {act_count / total * 100:.1f}% ({act_count}/{total})")
    print(f"4. TARGET & ARGUMENT FIDELITY    : {target_count / total * 100:.1f}% ({target_count}/{total})")
    print(f"5. NEGATION & CONSTRAINT FIDELITY: {constraint_count / total * 100:.1f}% ({constraint_count}/{total})")
    print("-" * 80)
    print(f"TOTAL BENCHMARK TIME             : {t_total:.2f}s")
    print("=" * 80)
    
    out_file = "scripts/benchmark_unseen_grounding_raw.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"passed": passed_count, "total": len(results), "pass_rate": pass_rate, "results": results}, f, indent=2, ensure_ascii=False)
    print(f"\nRaw results saved to: {out_file}\n")


if __name__ == "__main__":
    asyncio.run(run_unseen_benchmark())
