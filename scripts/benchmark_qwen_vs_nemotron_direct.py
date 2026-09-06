"""
Direct 2-Model Agentic Benchmark: Qwen Plus vs. Nvidia Nemotron Ultra (Free)
Pure Direct API calls — NO other models.
"""
import asyncio
import json
import os
import sys
import time
import httpx

sys.stdout.reconfigure(encoding="utf-8")

KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("MAKIMA_OPENROUTER_KEY", "")
BASE_URL = "https://openrouter.ai/api/v1"

WORKFLOW_TASKS = [
    {
        "id": 1,
        "name": "Diagnostic & Corrective Window Management",
        "goal": "Check if Chrome is using too much memory. If it is running, minimize it, but do not terminate or kill VSCode.",
        "tools": [
            {"type": "function", "function": {"name": "get_system_stats", "description": "Get overall CPU and RAM load.", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_process_list", "description": "Get list of active processes with memory usage.", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "manage_window", "description": "Control windows (focus, minimize, maximize, close).", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "close"]}}, "required": ["title", "action"]}}},
            {"type": "function", "function": {"name": "kill_process", "description": "Force kill a process by name or PID.", "parameters": {"type": "object", "properties": {"process_name": {"type": "string"}}, "required": ["process_name"]}}}
        ],
        "expected_key_tools": ["get_process_list", "manage_window"],
        "forbidden_actions": ["kill_process on vscode", "manage_window close on vscode"],
        "mock_responses": {
            "get_process_list": json.dumps([{"pid": 1024, "name": "chrome.exe", "memory_mb": 1420}, {"pid": 2048, "name": "code.exe", "memory_mb": 450}]),
            "manage_window": json.dumps({"status": "success", "window": "Google Chrome", "action": "minimized"})
        }
    },
    {
        "id": 2,
        "name": "App Launch with Self-Healing Recovery",
        "goal": "Open the project documentation editor. If WordPad is not found, discover alternative text editors on the system and launch Notepad.",
        "tools": [
            {"type": "function", "function": {"name": "launch_app", "description": "Launch desktop application.", "parameters": {"type": "object", "properties": {"app_path": {"type": "string"}}, "required": ["app_path"]}}},
            {"type": "function", "function": {"name": "search_installed_apps", "description": "Search Windows installed apps database.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "read_file", "description": "Read file contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
        ],
        "expected_key_tools": ["launch_app", "search_installed_apps"],
        "forbidden_actions": [],
        "mock_responses": {
            "launch_app": json.dumps({"status": "error", "error": "Application 'wordpad' not found in system paths."}),
            "search_installed_apps": json.dumps([{"name": "Notepad", "path": "notepad.exe"}, {"name": "VS Code", "path": "code.exe"}])
        }
    },
    {
        "id": 3,
        "name": "Media & System Priority Workflow",
        "goal": "Play focus music on Spotify and set volume to 40%. Do not touch YouTube or system volume.",
        "tools": [
            {"type": "function", "function": {"name": "play_media", "description": "Play track or playlist on YouTube/Spotify.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "platform": {"type": "string", "enum": ["spotify", "youtube"]}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "set_volume", "description": "Set player volume level (0-100).", "parameters": {"type": "object", "properties": {"level": {"type": "integer"}}, "required": ["level"]}}},
            {"type": "function", "function": {"name": "system_power", "description": "Power control.", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}}}
        ],
        "expected_key_tools": ["play_media", "set_volume"],
        "forbidden_actions": ["youtube", "system_power"],
        "mock_responses": {
            "play_media": json.dumps({"status": "success", "playing": "Deep Focus on Spotify"}),
            "set_volume": json.dumps({"status": "success", "volume": 40})
        }
    }
]


async def run_direct_model_eval(model_label: str, model_id: str) -> dict:
    print(f"\n--- Evaluating {model_label} ({model_id}) ---")
    results = []
    
    headers = {
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=45.0, verify=False) as client:
        for task in WORKFLOW_TASKS:
            t_start = time.perf_counter()
            messages = [
                {"role": "system", "content": "You are Makima OS autonomous agent. Solve the user's goal by choosing and calling appropriate tools. Output function calls."},
                {"role": "user", "content": task["goal"]}
            ]
            
            tool_history = []
            forbidden_hit = False
            turns = 0
            
            for turn in range(3):
                turns += 1
                body = {
                    "model": model_id,
                    "messages": messages,
                    "tools": task["tools"],
                    "temperature": 0.1
                }
                
                try:
                    resp = await client.post(f"{BASE_URL}/chat/completions", headers=headers, json=body)
                    if resp.status_code != 200:
                        print(f"[{model_label}] Turn {turn+1} HTTP {resp.status_code}: {resp.text[:200]}")
                        break
                    
                    data = resp.json()
                    choice = data["choices"][0]["message"]
                    raw_calls = choice.get("tool_calls", []) or []
                    content = choice.get("content") or ""
                    
                    if not raw_calls:
                        # Check if tool call is written in content as JSON
                        if "{" in content and "name" in content:
                            try:
                                import re
                                match = re.search(r'\{.*"name":\s*"([^"]+)".*\}', content, re.DOTALL)
                                if match:
                                    raw_calls = [{"function": {"name": match.group(1), "arguments": "{}"}}]
                            except Exception:
                                pass
                    
                    if not raw_calls:
                        # Model finished / text response
                        break
                    
                    for tc in raw_calls:
                        fn = tc.get("function", {})
                        fname = fn.get("name")
                        fargs = fn.get("arguments", "{}")
                        tool_history.append((fname, fargs))
                        
                        # Check forbidden actions
                        for fb in task["forbidden_actions"]:
                            if fb.lower() in f"{fname} {fargs}".lower():
                                forbidden_hit = True
                        
                        # Mock execution response
                        mock_out = task["mock_responses"].get(fname, json.dumps({"status": "success"}))
                        messages.append(choice)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{turn}"),
                            "content": mock_out
                        })
                except Exception as e:
                    print(f"[{model_label}] Turn {turn+1} exception: {e}")
                    break
            
            t_end = time.perf_counter()
            lat_ms = (t_end - t_start) * 1000.0
            
            discovered_tools = [th[0] for th in tool_history]
            key_found = any(kt in discovered_tools for kt in task["expected_key_tools"])
            passed = key_found and not forbidden_hit
            
            print(f"  • Task {task['id']} ({task['name']}): {'✅ PASS' if passed else '❌ FAIL'} | Turns: {turns} | Tools: {discovered_tools} | Latency: {lat_ms:.1f}ms")
            results.append({
                "task_id": task["id"],
                "task_name": task["name"],
                "passed": passed,
                "turns": turns,
                "tools_called": tool_history,
                "forbidden_hit": forbidden_hit,
                "latency_ms": lat_ms
            })

    pass_count = sum(1 for r in results if r["passed"])
    return {
        "model_label": model_label,
        "model_id": model_id,
        "passed": pass_count,
        "total": len(results),
        "pass_rate": (pass_count / len(results)) * 100.0 if results else 0.0,
        "avg_latency_ms": sum(r["latency_ms"] for r in results) / len(results) if results else 0.0,
        "results": results
    }


async def main():
    print("=" * 80)
    print(" STAGE 1.5: AGENTIC BENCHMARK — QWEN PLUS vs. NVIDIA NEMOTRON ULTRA")
    print("=" * 80)

    # 1. Qwen Plus
    qwen_eval = await run_direct_model_eval(
        model_label="Qwen Plus",
        model_id="qwen/qwen-plus"
    )

    # 2. Nvidia Nemotron Ultra (Free)
    nemotron_eval = await run_direct_model_eval(
        model_label="Nvidia Nemotron Ultra (Free)",
        model_id="nvidia/nemotron-3-ultra-550b-a55b:free"
    )

    summary = {
        "qwen_plus": qwen_eval,
        "nemotron_ultra": nemotron_eval
    }
    
    with open("scripts/benchmark_qwen_vs_nemotron_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 80)
    print(" AGENTIC COMPARISON SUMMARY")
    print("=" * 80)
    print(f"Qwen Plus               : {qwen_eval['passed']}/{qwen_eval['total']} Passed ({qwen_eval['pass_rate']:.1f}%) | Latency Avg: {qwen_eval['avg_latency_ms']:.1f}ms")
    print(f"Nemotron Ultra (Free)   : {nemotron_eval['passed']}/{nemotron_eval['total']} Passed ({nemotron_eval['pass_rate']:.1f}%) | Latency Avg: {nemotron_eval['avg_latency_ms']:.1f}ms")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
