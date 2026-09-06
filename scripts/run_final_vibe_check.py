"""
Makima Final Vibe Check Test Suite.
Tests all 8 user scenarios for full pipeline validation.
"""
import asyncio
import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from apps.brain.core.orchestration_engine import OrchestrationEngine
from apps.brain.core.contracts import ExecutionResult
from apps.brain.ai_handler import LLMResponse
from apps.brain.tools.system_tools import set_volume, launch_app


class LiveVibeMockKernel:
    def __init__(self):
        self.executed = []

    async def execute_direct_tool(self, tool_name, params=None, task_id="direct_task", context=None):
        self.executed.append((tool_name, params))
        if tool_name in ("media_play", "play_media"):
            q = (params or {}).get("query", "Kesariya")
            return ExecutionResult(
                execution_id="exec_1",
                action_id="act_1",
                task_id=task_id,
                tool_output={"status": "success", "resolved_title": q, "platform": "youtube"},
                is_verified=True,
            )
        elif tool_name == "set_volume":
            lvl = (params or {}).get("level")
            delta = (params or {}).get("delta")
            res = await set_volume(level=lvl, delta=delta)
            return ExecutionResult(
                execution_id="exec_2",
                action_id="act_2",
                task_id=task_id,
                tool_output=res,
                is_verified=True,
            )
        elif tool_name == "launch_app":
            app = (params or {}).get("app_path", "notepad")
            return ExecutionResult(
                execution_id="exec_3",
                action_id="act_3",
                task_id=task_id,
                tool_output=f"Launched {app}",
                is_verified=True,
            )
        return ExecutionResult(
            execution_id="exec_4",
            action_id="act_4",
            task_id=task_id,
            tool_output="OK",
            is_verified=True,
        )


class LiveVibeAIHandler:
    def try_parse_json(self, s):
        return None

    async def generate(self, messages, task="general", **kwargs):
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        if "fibonacci" in user_msg.lower():
            return LLMResponse(
                text="```python\ndef fibonacci(n: int) -> list[int]:\n    if n <= 0:\n        return []\n    fib = [0, 1]\n    while len(fib) < n:\n        fib.append(fib[-1] + fib[-2])\n    return fib[:n]\n```\nHere is the clean Python implementation of Fibonacci series.",
                backend="mock",
                model="qwen-flash",
                total_tokens=120,
            )
        elif "einstein" in user_msg.lower():
            return LLMResponse(
                text="Albert Einstein (1879–1955) ek renowned theoretical physicist the jinhone Theory of Relativity develop ki thi aur 1921 mein Photoelectric Effect ke liye Physics ka Nobel Prize jeeta.",
                backend="mock",
                model="qwen-flash",
                total_tokens=90,
            )
        return LLMResponse(text="Processed.", backend="mock", model="qwen-flash", total_tokens=20)


class MemoryStoreProbe:
    def __init__(self):
        self.stored = []

    async def save_turn(self, msg, role="user", conversation_id="default"):
        self.stored.append({"msg": msg, "role": role})

    async def search(self, q, k=5):
        return []

    async def get_history(self, n=50, conversation_id="default"):
        return []


async def run_vibe_check():
    ws_events = []

    async def mock_ws_broadcast(msg):
        ws_events.append(msg.to_dict())

    kernel = LiveVibeMockKernel()
    ai = LiveVibeAIHandler()
    mem = MemoryStoreProbe()

    engine = OrchestrationEngine(
        agent_orchestrator=kernel,
        eternal_memory=mem,
        ws_broadcast=mock_ws_broadcast,
        ai_handler=ai,
    )

    scenarios = [
        ("1. Kesariya bajao", "Kesariya bajao", "direct", [{"tool_name": "media_play", "params": {"query": "Kesariya"}}], "toast"),
        ("2. Volume 30 karo", "Volume 30 karo", "direct", [{"tool_name": "set_volume", "params": {"level": 30}}], "silent"),
        ("3. Awaz kam karo", "Awaz kam karo", "direct", [{"tool_name": "set_volume", "params": {"level": "kam"}}], "silent"),
        ("4. Kal subah 9 baje reminder", "Kal subah 9 baje reminder lagao", "direct", [{"tool_name": "system_show_notification", "params": {"title": "Reminder", "message": "Kal subah 9 baje"}}], "toast"),
        ("5. Python mein fibonacci likho", "Python mein fibonacci likho", "conversational", [], "full"),
        ("6. Ye yaad karo paani", "Ye yaad karo ki mujhe roz paani peena chahiye", "direct", [{"tool_name": "memory_store", "params": {"content": "mujhe roz paani peena chahiye"}}], "toast"),
        ("7. Notepad kholo", "Notepad kholo", "direct", [{"tool_name": "launch_app", "params": {"app_path": "notepad"}}], "toast"),
        ("8. Einstein kaun tha", "Einstein kaun tha", "conversational", [], "full"),
    ]

    results = []
    for title, query, strat, tools, f_mode in scenarios:
        ws_events.clear()
        t0 = time.perf_counter()

        engine.semantic_planner = type(
            "Planner",
            (),
            {
                "plan": lambda *a, **kw: asyncio.sleep(
                    0,
                    result=PlanAssessment(
                        task=query,
                        execution_strategy=strat,
                        direct_tools=tools,
                        feedback_mode=f_mode,
                    ),
                )
            },
        )()

        await engine.handle_message(task_id=f"task_{len(results)+1}", message=query)
        dt = (time.perf_counter() - t0) * 1000.0

        toasts = [e for e in ws_events if e.get("type") == "toast_notification"]
        chunks = [e for e in ws_events if e.get("type") == "ai_chunk" and e.get("payload", {}).get("text")]
        full_text = "".join(c.get("payload", {}).get("text", "") for c in chunks)

        outcome_type = "TOAST" if toasts else ("CHAT_BUBBLE" if chunks else "SILENT")
        toast_msg = toasts[0]["payload"]["message"] if toasts else ""
        toast_lvl = toasts[0]["payload"]["level"] if toasts else ""

        results.append({
            "scenario": title,
            "query": query,
            "strategy": strat,
            "feedback_mode": f_mode,
            "outcome_type": outcome_type,
            "toast_message": toast_msg,
            "toast_level": toast_lvl,
            "chat_sample": full_text[:80].replace("\n", " "),
            "time_ms": dt,
            "error": None,
        })

    print("\n" + "=" * 96)
    print(f"{'#':<3} | {'SCENARIO':<30} | {'FEEDBACK':<12} | {'TIME':<9} | {'DETAILS'}")
    print("=" * 96)
    for i, r in enumerate(results, 1):
        details = (
            f"[{r['toast_level'].upper()}] {r['toast_message']}"
            if r["outcome_type"] == "TOAST"
            else (r["chat_sample"] if r["outcome_type"] == "CHAT_BUBBLE" else "(Executed silently in background)")
        )
        print(f"{i:<3} | {r['scenario']:<30} | {r['outcome_type']:<12} | {r['time_ms']:>6.1f} ms | {details}")
    print("=" * 96)

    # 1. Media play: Toast with Now Playing & success level + Chat bubble
    assert results[0]["outcome_type"] == "TOAST", "Scenario 1 should emit TOAST"
    assert "Kesariya" in results[0]["toast_message"], "Scenario 1 should contain song title"
    assert "Now Playing" in results[0]["toast_message"], "Scenario 1 should have Now Playing banner"
    assert results[0]["toast_level"] == "success", "Scenario 1 should be level=success"
    assert "Kesariya" in results[0]["chat_sample"], "Scenario 1 should also emit chat bubble line"

    # 2. Volume 30: Silent execution
    assert results[1]["outcome_type"] == "SILENT", "Scenario 2 should be SILENT"

    # 3. Awaz kam: Silent execution
    assert results[2]["outcome_type"] == "SILENT", "Scenario 3 should be SILENT"

    # 4. Reminder: Toast + Chat bubble
    assert results[3]["outcome_type"] == "TOAST", "Scenario 4 should emit TOAST"
    assert "Reminder" in results[3]["chat_sample"], "Scenario 4 should also emit chat bubble line"

    # 5. Fibonacci: Chat bubble with code <20s (No toast)
    assert results[4]["outcome_type"] == "CHAT_BUBBLE", "Scenario 5 should emit CHAT_BUBBLE"
    assert "def fibonacci" in results[4]["chat_sample"], "Scenario 5 should contain Python code"
    assert results[4]["time_ms"] < 20000, "Scenario 5 should finish in <20s"

    # 6. Memory store: Clean user fact stored in memory probe
    assert any("paani peena" in s["msg"] for s in mem.stored), "Scenario 6 should store fact in memory"

    # 7. Notepad: Toast + Chat bubble
    assert results[6]["outcome_type"] == "TOAST", "Scenario 7 should emit TOAST"
    assert "Notepad" in results[6]["chat_sample"], "Scenario 7 should also emit chat bubble line"

    # 8. Einstein: Full conversational chat response (No toast)
    assert results[7]["outcome_type"] == "CHAT_BUBBLE", "Scenario 8 should emit CHAT_BUBBLE"
    assert "Einstein" in results[7]["chat_sample"], "Scenario 8 should have Einstein biography"

    print("\n>>> ALL 8 VIBE CHECK SCENARIOS VERIFIED 100% PASS <<<\n")


if __name__ == "__main__":
    asyncio.run(run_vibe_check())
