import asyncio
import json
import sys
import io
import websockets

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

async def send_and_receive(ws, text, task_id):
    print(f"\n========================================")
    print(f">>> SENDING: \"{text}\" (task_id: {task_id})")
    print(f"========================================")
    
    # Drain any lingering events from prior turns
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.1)
        except (asyncio.TimeoutError, Exception):
            break

    msg = {
        "v": 1,
        "type": "user_message",
        "task_id": task_id,
        "payload": {
            "text": text,
            "conversation_id": "live_test_conv"
        }
    }
    await ws.send(json.dumps(msg))
    
    full_text = ""
    start_t = asyncio.get_event_loop().time()
    for _ in range(60):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            data = json.loads(raw)
            mtype = data.get("type")
            msg_task = data.get("task_id")
            payload = data.get("payload", {})
            if isinstance(payload, dict) and "task_id" in payload:
                msg_task = msg_task or payload.get("task_id")
            
            # Ignore heartbeat, watchdog or delta broadcasts from other subsystems
            if mtype in ("health_status", "service_status", "system_metrics", "heartbeat"):
                continue

            if mtype == "ai_chunk":
                chunk = payload.get("text") or payload.get("chunk") or ""
                full_text += chunk
                if payload.get("is_final"):
                    break
            elif mtype in ("agent_done", "ai_response_done"):
                if not msg_task or msg_task == task_id:
                    break
            elif mtype in ("agent_started", "persona_changed", "tool_call_started", "tool_call_finished"):
                print(f"   [EVENT] {mtype}: {payload}")
            elif mtype == "thinking_status":
                print(f"   [THINKING] {payload.get('status')}")
        except asyncio.TimeoutError:
            print("   [TIMEOUT] No more messages received within timeout.")
            break
            
    elapsed = asyncio.get_event_loop().time() - start_t
    res_text = full_text.strip()
    print(f"<<< RESPONSE ({elapsed:.2f}s):")
    print(res_text or "[No text streamed]")
    return res_text

async def run_suite():
    uri = "ws://127.0.0.1:8080/ws"
    print(f"Connecting to {uri}...")
    async with websockets.connect(uri) as ws:
        print("Connected! Starting 20-command comprehensive multi-agent test suite...")

        test_commands = [
            ("1. Fast Chat / Greeting", "hello makima, how are you today?", "test_01_hello"),
            ("2. App Launch (Notepad)", "open notepad", "test_02_open_notepad"),
            ("3. Window Minimize", "minimize notepad", "test_03_min_notepad"),
            ("4. System RAM Status", "ram usage kitna hai abhi?", "test_04_ram_usage"),
            ("5. Full Hardware Stats", "system hardware stats aur CPU load batao", "test_05_sys_stats"),
            ("6. Windows SMTC Song Check", "what song is playing right now?", "test_06_now_playing"),
            ("7. Clipboard Copy", "copy 'Makima OS Live Verification Active' to my clipboard", "test_07_set_clip"),
            ("8. Clipboard Read", "what is currently on my clipboard?", "test_08_get_clip"),
            ("9. Window Focus / Restore", "focus notepad window", "test_09_focus_notepad"),
            ("10. Temp Files Dry-Run", "clean temporary files preview", "test_10_clean_temp"),
            ("11. Close Window", "close notepad", "test_11_close_notepad"),
            ("12. Hinglish Conversation", "kya haal chaal hai makima?", "test_12_hinglish_chat"),
            ("13. App Launch (Calculator)", "open calculator", "test_13_open_calc"),
            ("14. Close Window (Calculator)", "close calculator", "test_14_close_calc"),
            ("15. Media Song Playback", "play believer song on youtube", "test_15_play_song"),
            ("16. Media Song Pause", "pause the song", "test_16_pause_song"),
            ("17. Media Song Resume", "resume playback", "test_17_resume_song"),
            ("18. Master Volume Adjustment", "set system volume to 40", "test_18_set_volume"),
            ("19. Running Process Inspection", "top running processes list dikhao", "test_19_proc_list"),
            ("20. Conversational Wrap-up", "thanks makima you did an amazing job!", "test_20_wrap_up"),
        ]

        results = []
        for i, (label, cmd, tid) in enumerate(test_commands, 1):
            print(f"\n[{i}/20] TEST: {label}")
            resp = await send_and_receive(ws, cmd, tid)
            results.append((label, cmd, resp))
            await asyncio.sleep(2.5)

        print("\n" + "="*60)
        print("           20-COMMAND SUITE COMPLETE")
        print("="*60)
        for i, (label, cmd, resp) in enumerate(results, 1):
            status = "PASS" if resp and "No AI Model Backend Available" not in resp else "WARN/FAIL"
            print(f"{i:02d}. [{status}] {label}: {cmd[:30]}... -> {resp[:45]}...")

if __name__ == "__main__":
    asyncio.run(run_suite())
