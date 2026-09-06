import asyncio
import json
import sys
import time
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from apps.brain.core.app_bootstrap import AppBootstrap
from apps.brain.ws_protocol import WSMessage


async def run_live_system_agent_pipeline():
    events_log: List[Dict[str, Any]] = []

    async def ws_broadcast_handler(msg: Any):
        if isinstance(msg, WSMessage):
            payload_str = str(msg.payload)
            if len(payload_str) > 300:
                payload_str = payload_str[:300] + "... [TRUNCATED]"
            events_log.append({
                "time": round(time.time(), 3),
                "type": msg.type.value if hasattr(msg.type, "value") else str(msg.type),
                "task_id": msg.task_id,
                "payload": msg.payload
            })
        elif isinstance(msg, dict):
            events_log.append(msg)

    # 1. Initialize Bootstrap
    bootstrap = AppBootstrap(config={})
    bootstrap.ws_broadcast = ws_broadcast_handler
    await bootstrap.initialize_services()
    await bootstrap.start_background_services()

    orchestrator = bootstrap.services["orchestration_engine"]

    commands = [
        ("1. HARDWARE TELEMETRY", "Check my current system stats: CPU load, RAM usage, and disk space."),
        ("2. PROCESS INSPECTION", "List the top 5 memory-consuming running processes on this computer."),
        ("3. APP DISCOVERY", "Search if Google Chrome or Notepad is installed on this PC."),
        ("4. FILE SYSTEM I/O", "Write 'Makima Live OS Pipeline Verified - OK' to ./makima_workspace/live_os_log.txt and read it back."),
        ("5. NETWORK DIAGNOSTICS", "Show my active network adapters and IP configurations."),
    ]

    print("\n" + "=" * 90)
    print("🚀 MAKIMA REAL-PIPELINE SYSTEM AGENT LIVE EXECUTION TRACE")
    print("=" * 90)

    for label, cmd in commands:
        print(f"\n[{label}]")
        print(f"👉 USER PROMPT: \"{cmd}\"")
        events_log.clear()
        t0 = time.monotonic()
        task_id = f"real_sys_{int(time.time() * 1000)}"

        try:
            await orchestrator.handle_message(task_id, cmd, context={"conversation_id": "live_user_session"})
            dt = round((time.monotonic() - t0) * 1000, 1)

            print(f"⚡ COMPLETED IN: {dt} ms")
            print(f"📡 EVENTS EMITTED ({len(events_log)} total):")
            
            final_answer = ""
            for ev in events_log:
                ev_type = ev.get("type")
                payload = ev.get("payload", {})
                if ev_type == "agent_started":
                    print(f"   ↳ [AGENT_STARTED] Agent: '{payload.get('agent')}' | Mode: {payload.get('mode', 'direct')}")
                elif ev_type == "tool_started" or "calling tools" in str(payload):
                    print(f"   ↳ [TOOL_INVOCATION] {payload}")
                elif ev_type == "agent_done":
                    summary = payload.get("result_summary", "")
                    if len(summary) > 400:
                        summary = summary[:400] + "... [TRUNCATED]"
                    print(f"   ↳ [AGENT_DONE] Output: {summary}")
                    final_answer = payload.get("result_summary", "")
                elif ev_type == "ai_chunk":
                    chunk_text = payload.get("text", "")
                    if chunk_text:
                        final_answer = chunk_text

            if not final_answer and events_log:
                final_answer = str(events_log[-1].get("payload", ""))
            
            print(f"\n💬 AGENT FINAL CONVERSATIONAL RESPONSE:\n{final_answer}\n")
            print("-" * 90)

        except Exception as e:
            print(f"❌ ERROR: {e}")

        await asyncio.sleep(1.0)

    await bootstrap.shutdown_services()
    print("\n" + "=" * 90)
    print("✅ LIVE PIPELINE TEST COMPLETED FOR ALL SYSTEM AGENT COMMANDS")
    print("=" * 90)


if __name__ == "__main__":
    asyncio.run(run_live_system_agent_pipeline())
