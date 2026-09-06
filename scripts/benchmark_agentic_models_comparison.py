"""
Makima OS — Stage 1.5: Multi-Model Agentic Capability Benchmark
Location: scripts/benchmark_agentic_models_comparison.py

Compares:
1. Qwen 3.6-27B (DashScope API)
2. Nvidia Nemotron 70B / 120B (OpenRouter Free API)

Evaluates on:
- Workflow Generalization (High-level goal -> autonomous multi-step discovery)
- ReAct Tool Sequence Quality
- Error Recovery (Verbal Reflexion)
- Argument Precision & Constraint Obedience
- Latency & Token Efficiency
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.stdout.reconfigure(encoding="utf-8")

from apps.brain.core.app_bootstrap import AppBootstrap


@dataclass
class WorkflowTask:
    id: int
    name: str
    goal: str
    available_tools: list[dict]
    expected_key_tools: list[str]
    negative_constraints: list[str]
    mock_responses: dict[str, Any]


WORKFLOW_TASKS: list[WorkflowTask] = [
    WorkflowTask(
        id=1,
        name="Diagnostic & Corrective Window Management",
        goal="Check if Chrome is using too much memory. If it is running, minimize it, but do not terminate or kill VSCode.",
        available_tools=[
            {"type": "function", "function": {"name": "get_system_stats", "description": "Get overall CPU and RAM load.", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_process_list", "description": "Get list of active processes with memory usage.", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "manage_window", "description": "Control windows (focus, minimize, maximize, close).", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "action": {"type": "string", "enum": ["focus", "minimize", "maximize", "close"]}}, "required": ["title", "action"]}}},
            {"type": "function", "function": {"name": "kill_process", "description": "Force kill a process by name or PID.", "parameters": {"type": "object", "properties": {"process_name": {"type": "string"}}, "required": ["process_name"]}}}
        ],
        expected_key_tools=["get_process_list", "manage_window"],
        negative_constraints=["kill_process on vscode", "manage_window close on vscode"],
        mock_responses={
            "get_process_list": [{"pid": 1024, "name": "chrome.exe", "memory_mb": 1420}, {"pid": 2048, "name": "code.exe", "memory_mb": 450}],
            "manage_window": {"status": "success", "window": "Google Chrome", "action": "minimized"}
        }
    ),
    WorkflowTask(
        id=2,
        name="App Launch with Self-Healing Recovery",
        goal="Open the project documentation editor. If WordPad is not found, discover alternative text editors on the system and launch Notepad.",
        available_tools=[
            {"type": "function", "function": {"name": "launch_app", "description": "Launch desktop application.", "parameters": {"type": "object", "properties": {"app_path": {"type": "string"}}, "required": ["app_path"]}}},
            {"type": "function", "function": {"name": "search_installed_apps", "description": "Search Windows installed apps database.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "read_file", "description": "Read file contents.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
        ],
        expected_key_tools=["launch_app", "search_installed_apps"],
        negative_constraints=[],
        mock_responses={
            "launch_app": "[Error] Application 'wordpad' not found in system paths.",
            "search_installed_apps": [{"name": "Notepad", "path": "notepad.exe"}, {"name": "VS Code", "path": "code.exe"}],
            "launch_app": {"status": "success", "pid": 4120}
        }
    ),
    WorkflowTask(
        id=3,
        name="Media & System Priority Workflow",
        goal="Play focus music on Spotify and set volume to 40%. Do not touch YouTube or system volume.",
        available_tools=[
            {"type": "function", "function": {"name": "play_media", "description": "Play track or playlist on YouTube/Spotify.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "platform": {"type": "string", "enum": ["spotify", "youtube"]}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "set_volume", "description": "Set player volume level (0-100).", "parameters": {"type": "object", "properties": {"level": {"type": "integer"}}, "required": ["level"]}}},
            {"type": "function", "function": {"name": "system_power", "description": "Power control.", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}}}
        ],
        expected_key_tools=["play_media", "set_volume"],
        negative_constraints=["platform: youtube", "system_power"],
        mock_responses={
            "play_media": {"status": "success", "playing": "Deep Focus on Spotify"},
            "set_volume": {"status": "success", "volume": 40}
        }
    )
]


async def evaluate_model_on_workflows(model_backend: str, model_name: str) -> dict:
    import yaml
    from pathlib import Path

    cfg_file = Path(__file__).resolve().parents[1] / "configs" / "default.yaml"
    with open(cfg_file, encoding="utf-8") as f:
        runtime_config = yaml.safe_load(f) or {}

    if model_backend == "qwen":
        runtime_config["llm"]["backends"]["qwen"]["enabled"] = True
        runtime_config["llm"]["backends"]["qwen"]["model"] = model_name
    elif model_backend == "claude": # OpenRouter backend in default.yaml
        runtime_config["llm"]["backends"]["claude"]["enabled"] = True
        runtime_config["llm"]["backends"]["claude"]["model"] = model_name

    bootstrap = AppBootstrap(config=runtime_config)
    services = await bootstrap.initialize_services()
    ai_handler = services.get("ai_handler")

    task_results = []
    total_tokens = 0
    total_latency_ms = 0.0

    for task in WORKFLOW_TASKS:
        t_start = time.perf_counter()
        messages = [
            {"role": "system", "content": "You are Makima OS autonomous reasoning agent. Execute the user's goal by choosing appropriate tools in sequence. Output JSON tool calls."},
            {"role": "user", "content": task.goal}
        ]

        turns_taken = 0
        executed_tools = []
        constraints_violated = False

        # Run up to 3 ReAct turns
        for turn in range(3):
            turns_taken += 1
            resp = await ai_handler.generate(
                messages,
                task="system_control" if model_backend == "qwen" else "general",
                tools=task.available_tools,
                tool_choice="auto"
            )
            raw_tcs = getattr(resp, "tool_calls", []) or []
            text = getattr(resp, "text", "") or ""

            if not raw_tcs and text:
                parsed = ai_handler.try_parse_json(text)
                if isinstance(parsed, dict) and parsed.get("tool"):
                    raw_tcs = [{"name": parsed["tool"], "arguments": json.dumps(parsed.get("params", {}))}]

            if not raw_tcs:
                break

            for tc in raw_tcs:
                t_name = tc.get("name")
                t_args = tc.get("arguments", "{}")
                executed_tools.append(t_name)

                # Check negative constraints
                for nc in task.negative_constraints:
                    if nc.lower() in f"{t_name} {t_args}".lower():
                        constraints_violated = True

                # Provide mock tool response
                resp_mock = task.mock_responses.get(t_name) or task.mock_responses.get(f"{t_name}:{t_args}") or {"status": "success"}
                messages.append({"role": "assistant", "content": f"Called {t_name}", "tool_calls": [{"id": f"call_{turn}", "type": "function", "function": {"name": t_name, "arguments": t_args}}]})
                messages.append({"role": "tool", "tool_call_id": f"call_{turn}", "content": json.dumps(resp_mock)})

        t_end = time.perf_counter()
        lat_ms = (t_end - t_start) * 1000.0
        total_latency_ms += lat_ms

        discovered_key_tools = all(kt in executed_tools for kt in task.expected_key_tools[:1])
        task_passed = discovered_key_tools and not constraints_violated

        task_results.append({
            "task_id": task.id,
            "task_name": task.name,
            "passed": task_passed,
            "turns": turns_taken,
            "executed_tools": executed_tools,
            "constraints_violated": constraints_violated,
            "latency_ms": lat_ms
        })

    passed_count = sum(1 for r in task_results if r["passed"])
    return {
        "model": model_name,
        "backend": model_backend,
        "tasks_evaluated": len(task_results),
        "tasks_passed": passed_count,
        "pass_rate_pct": (passed_count / len(task_results)) * 100.0,
        "avg_latency_ms": total_latency_ms / len(task_results),
        "task_breakdown": task_results
    }


async def main():
    print("=" * 80)
    print(" STAGE 1.5: MULTI-MODEL AGENTIC CAPABILITY BENCHMARK")
    print("=" * 80)

    # 1. Evaluate Qwen 3.6-27B (DashScope)
    print("\n[1/2] Evaluating Qwen 3.6-27B on autonomous multi-step workflow tasks...")
    qwen_res = await evaluate_model_on_workflows("qwen", "qwen3.6-27b")
    print(f"Qwen 27B Result: {qwen_res['tasks_passed']}/{qwen_res['tasks_evaluated']} Passed ({qwen_res['pass_rate_pct']:.1f}%) | Avg Latency: {qwen_res['avg_latency_ms']:.1f}ms")

    # 2. Evaluate Nvidia Nemotron (OpenRouter)
    print("\n[2/2] Evaluating Nvidia Nemotron 70B/120B on autonomous multi-step workflow tasks...")
    try:
        nemotron_res = await evaluate_model_on_workflows("claude", "nvidia/llama-3.1-nemotron-70b-instruct:free")
        print(f"Nemotron Result: {nemotron_res['tasks_passed']}/{nemotron_res['tasks_evaluated']} Passed ({nemotron_res['pass_rate_pct']:.1f}%) | Avg Latency: {nemotron_res['avg_latency_ms']:.1f}ms")
    except Exception as e:
        nemotron_res = {"model": "nvidia/llama-3.1-nemotron-70b-instruct:free", "error": str(e), "tasks_passed": 0, "tasks_evaluated": 3, "pass_rate_pct": 0.0, "avg_latency_ms": 0.0, "task_breakdown": []}
        print(f"Nemotron Evaluation error: {e}")

    report = {
        "qwen_27b": qwen_res,
        "nemotron": nemotron_res
    }

    out_file = os.path.join(os.path.dirname(__file__), "benchmark_agentic_models_raw.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved agentic comparison raw report to: {out_file}\n")


if __name__ == "__main__":
    asyncio.run(main())
