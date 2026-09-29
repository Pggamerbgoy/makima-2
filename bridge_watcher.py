#!/usr/bin/env python3
"""
==============================================================================
⚠️ FROZEN IMMUTABLE UTILITY - MULTI-AGENT BRIDGE WATCHER ⚠️
==============================================================================
RULE FOR ALL AI AGENTS (Lead, Dev, Reviewer, etc.):
DO NOT MODIFY THIS FILE AT RUNTIME. Both Antigravity instances and OpenCode
share this exact script. Do not hardcode your agent name or rewrite this file.

Instead, invoke this script from CLI with your agent's identity:
  python -u bridge_watcher.py --agent antigravity_dev
  python -u bridge_watcher.py --agent antigravity_lead

OPTIONS:
  --agent <name>       : Your agent name. Ignores events triggered by yourself.
  --continuous         : Keep running indefinitely (daemon). Default is ONE-SHOT exit
                         which cleanly triggers Antigravity IDE's reactive wakeup.
  --timeout <seconds>  : Auto-exit after inactivity (default: 300s).
  --poll-interval <sec>: Polling cadence in seconds (default: 0.5s).
==============================================================================
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

STATE_FILE = Path(__file__).resolve().parent / "bridge_state.json"


def read_state_safe(path: Path) -> dict:
    """Read bridge state JSON with retries against concurrent file locks."""
    for _ in range(5):
        try:
            if not path.exists():
                return {}
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except (json.JSONDecodeError, PermissionError, OSError):
            time.sleep(0.05)
    return {}


def main():
    parser = argparse.ArgumentParser(
        description="FROZEN: Watch bridge_state.json for multi-agent updates without file mutation."
    )
    parser.add_argument(
        "--agent",
        "--wait-for",
        dest="agent",
        type=str,
        default="",
        help="Identity of this agent (e.g. 'antigravity_lead' or 'antigravity_dev'). Ignores self actions.",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep running continuously without exiting on first event (default is ONE-SHOT exit for auto-wakeup).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Timeout in seconds before exiting if no events occur (default: 300s).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Polling interval in seconds (default: 0.5s).",
    )
    parser.add_argument(
        "--from-msg-id",
        type=int,
        default=-1,
        help="Ignore messages with ID <= this value. Defaults to current message count at startup.",
    )

    args = parser.parse_args()
    agent_name = (args.agent or "").strip().lower()
    auto_exit = not args.continuous

    # Initial baseline
    initial_data = read_state_safe(STATE_FILE)
    messages = initial_data.get("messages", [])
    
    if args.from_msg_id >= 0:
        last_msg_id = args.from_msg_id
    else:
        last_msg_id = messages[-1].get("id", len(messages)) if messages else 0

    last_session_id = initial_data.get("session_id", "")
    last_plan_status = initial_data.get("plan_status", "none")
    last_tasks_str = json.dumps(initial_data.get("tasks", {}), sort_keys=True)
    last_results_str = json.dumps(initial_data.get("results", {}), sort_keys=True)

    print(
        f"[BRIDGE_WATCHER_ONLINE] Agent: '{agent_name or 'broadcast'}' | "
        f"Baseline Msg ID: {last_msg_id} | "
        f"Mode: {'AUTO-WAKEUP (one-shot exit)' if auto_exit else 'CONTINUOUS DAEMON'}",
        flush=True,
    )

    start_time = time.time()

    while True:
        time.sleep(args.poll_interval)

        # Check inactivity timeout
        if args.timeout > 0 and (time.time() - start_time) > args.timeout:
            print(f"[BRIDGE_WATCHER_TIMEOUT] No new events within {args.timeout}s.", flush=True)
            sys.exit(0)

        data = read_state_safe(STATE_FILE)
        if not data:
            continue

        event_triggered = False

        # 0. Session Reset Detection
        session_id = data.get("session_id", "")
        if session_id and last_session_id and session_id != last_session_id:
            print(f"[BRIDGE_EVENT:SESSION_RESET] Session changed to {session_id}", flush=True)
            last_session_id = session_id
            last_msg_id = 0
            event_triggered = True

        # 1. New Messages
        current_msgs = data.get("messages", [])
        if isinstance(current_msgs, list):
            new_msgs = [m for m in current_msgs if isinstance(m, dict) and m.get("id", 0) > last_msg_id]
            for m in new_msgs:
                sender = (m.get("agent") or "unknown").strip().lower()
                mid = m.get("id", 0)
                text = m.get("message", "")
                
                # Ignore messages sent by self
                if agent_name and sender == agent_name:
                    last_msg_id = max(last_msg_id, mid)
                    continue

                print(f"[BRIDGE_EVENT:NEW_MESSAGE] ID: #{mid} | From: {sender} | Content: {text}", flush=True)
                last_msg_id = max(last_msg_id, mid)
                event_triggered = True

        # 2. Plan Status Changes
        plan_status = data.get("plan_status", "none")
        if plan_status != last_plan_status:
            plan = data.get("plan")
            plan_desc = plan.get("description", "") if isinstance(plan, dict) else str(plan or "")
            proposed_by = (plan.get("proposed_by", "") if isinstance(plan, dict) else "").strip().lower()
            print(
                f"[BRIDGE_EVENT:PLAN_STATUS] Status: {plan_status} | ProposedBy: {proposed_by} | Desc: {plan_desc}",
                flush=True,
            )
            last_plan_status = plan_status
            if not agent_name or proposed_by != agent_name:
                event_triggered = True

        # 3. Tasks Updates
        tasks = data.get("tasks", {})
        tasks_str = json.dumps(tasks, sort_keys=True)
        if tasks_str != last_tasks_str:
            print(f"[BRIDGE_EVENT:TASKS_UPDATED] Tasks: {tasks_str}", flush=True)
            last_tasks_str = tasks_str
            event_triggered = True

        # 4. Results Updates
        results = data.get("results", {})
        results_str = json.dumps(results, sort_keys=True)
        if results_str != last_results_str:
            print(f"[BRIDGE_EVENT:RESULTS_UPDATED] Results: {results_str}", flush=True)
            last_results_str = results_str
            event_triggered = True

        # One-shot exit to trigger Antigravity Reactive Wakeup
        if auto_exit and event_triggered:
            print(f"[BRIDGE_WATCHER_EXIT] Event detected for '{agent_name}'. Waking up agent now.", flush=True)
            sys.exit(0)


if __name__ == "__main__":
    main()
