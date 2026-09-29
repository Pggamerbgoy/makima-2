#!/usr/bin/env python3
"""
Agent Bridge MCP Server (Hardened for Windows & Multi-Agent Concurrency)
========================================================================
Antigravity and OpenCode co-work bridge:
  1. Discuss & brainstorm
  2. Propose & approve task plans
  3. Claim and execute tasks
  4. Submit outputs
  5. Peer review & adversarial verification
"""

from datetime import datetime
import json
import os
import re
import sys
import tempfile
import threading

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from mcp.server.fastmcp import FastMCP

# ─── Server Init (Host and Port configured on constructor) ───────────────────
mcp = FastMCP("Agent-Bridge", host="127.0.0.1", port=8765)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_state.json")
_state_lock = threading.Lock()

# ─── Shared State ─────────────────────────────────────────────────────────────
def _fresh_state():
    return {
        "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "project_status": "idle",      # idle → discussing → planning → executing → reviewing → done
        "plan_status": "none",         # none → proposed → accepted → rejected
        "plan": None,
        "messages": [],
        "tasks": {},
        "results": {},
        "verifications": {}
    }

def _load():
    with _state_lock:
        if not os.path.exists(STATE_FILE):
            return _fresh_state()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return _fresh_state()
                return json.loads(content)
        except Exception:
            return _fresh_state()

def _save(s):
    with _state_lock:
        dir_name = os.path.dirname(STATE_FILE) or "."
        # Atomic file write to prevent Windows file corruption
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(s, tf, indent=2, ensure_ascii=False)
            temp_name = tf.name
        os.replace(temp_name, STATE_FILE)

state = _load()

def _norm(name: str) -> str:
    return (name or "").strip().lower()

# ─── TOOL 1 · post_message ────────────────────────────────────────────────────
@mcp.tool()
def post_message(agent_name: str, message: str) -> str:
    """
    Discussion board mein message post karo.
    Dono agents isse use karke aapas mein baat karte hain.

    agent_name : 'antigravity' ya 'opencode'
    message    : Jo kehna chahte ho
    """
    agent = _norm(agent_name)
    entry = {
        "id": len(state["messages"]) + 1,
        "agent": agent,
        "message": message.strip(),
        "timestamp": datetime.now().isoformat()
    }
    state["messages"].append(entry)
    if state["project_status"] == "idle":
        state["project_status"] = "discussing"
    _save(state)
    return f"✅ [{agent.upper()}] Message #{entry['id']} posted."

# ─── TOOL 2 · read_discussion ─────────────────────────────────────────────────
@mcp.tool()
def read_discussion(last_n: int = 20) -> str:
    """
    Discussion ke last N messages padho.
    last_n : Kitne messages dekhne hain (default 20)
    """
    msgs = state["messages"][-last_n:]
    if not msgs:
        return "📭 Koi message nahi hai abhi tak."
    lines = [
        f"[{m.get('timestamp', '')[11:19]}] {m['agent'].upper()}: {m['message']}"
        for m in msgs
    ]
    return "\n".join(lines)

# ─── TOOL 3 · propose_plan ────────────────────────────────────────────────────
@mcp.tool()
def propose_plan(agent_name: str, project_title: str, tasks_json: str) -> str:
    """
    Project ka plan propose karo. Tasks JSON string mein do.

    agent_name    : Plan propose karne wala agent ('antigravity' ya 'opencode')
    project_title : Project ka title
    tasks_json    : JSON array string —
                    '[
                       {"id":"T1","title":"Frontend UI","assigned_to":"antigravity","description":"React UI"},
                       {"id":"T2","title":"Backend API","assigned_to":"opencode","description":"FastAPI routes"}
                     ]'
    """
    agent = _norm(agent_name)
    cleaned = re.sub(r"^```(?:json)?\s*", "", tasks_json.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        task_list = json.loads(cleaned)
        if not isinstance(task_list, list):
            return "❌ tasks_json array/list hona chahiye."
    except Exception as e:
        return f"❌ tasks_json parse error: {e}"

    tasks_dict = {}
    for t in task_list:
        tid = str(t.get("id", "")).strip()
        assigned = _norm(t.get("assigned_to", ""))
        tasks_dict[tid] = {
            "id": tid,
            "title": t.get("title", ""),
            "assigned_to": assigned,
            "description": t.get("description", ""),
            "status": "pending",
            "claimed_by": None,
            "started_at": None
        }

    state["plan"] = {
        "title": project_title,
        "proposed_by": agent,
        "proposed_at": datetime.now().isoformat(),
        "task_list": list(tasks_dict.values())
    }
    state["tasks"] = tasks_dict
    state["plan_status"] = "proposed"
    state["project_status"] = "planning"
    _save(state)

    out = [
        f"📋 PLAN PROPOSED by {agent.upper()}",
        f"Project : {project_title}",
        f"Tasks   : {len(tasks_dict)}",
        ""
    ]
    for tid, t in tasks_dict.items():
        out.append(f"  [{tid}] {t['title']} → {t['assigned_to'].upper()}")
        out.append(f"       {t['description']}")
    out.append("\n⏳ Dusra agent respond_to_plan() se accept/reject karega.")
    return "\n".join(out)

# ─── TOOL 4 · respond_to_plan ─────────────────────────────────────────────────
@mcp.tool()
def respond_to_plan(agent_name: str, decision: str, feedback: str = "") -> str:
    """
    Proposed plan ko accept ya reject karo.
    agent_name : Respond karne wala agent
    decision   : 'accept' ya 'reject'
    feedback   : Note ya reason
    """
    agent = _norm(agent_name)
    dec = _norm(decision)

    if state["plan_status"] == "none":
        return "❌ Koi plan propose nahi hua. Pehle propose_plan() use karo."

    if dec == "accept":
        state["plan_status"] = "accepted"
        state["project_status"] = "executing"
        state["messages"].append({
            "id": len(state["messages"]) + 1,
            "agent": agent,
            "message": f"✅ Plan ACCEPTED. {feedback or 'Kaam shuru karte hain!'}",
            "timestamp": datetime.now().isoformat()
        })
        _save(state)
        return f"✅ Plan ACCEPTED by {agent.upper()}! Ab claim_task() se kaam shuru karo."

    elif dec == "reject":
        state["plan_status"] = "rejected"
        state["project_status"] = "discussing"
        state["messages"].append({
            "id": len(state["messages"]) + 1,
            "agent": agent,
            "message": f"❌ Plan REJECTED. Reason: {feedback}",
            "timestamp": datetime.now().isoformat()
        })
        _save(state)
        return f"❌ Plan REJECTED by {agent.upper()}.\nReason: {feedback}\nNaya plan banao."

    return "❌ decision sirf 'accept' ya 'reject' hona chahiye."

# ─── TOOL 5 · claim_task ──────────────────────────────────────────────────────
@mcp.tool()
def claim_task(agent_name: str, task_id: str) -> str:
    """
    Apna assigned task claim karo aur kaam shuru karo.
    agent_name : Task claim karne wala agent
    task_id    : Jaise 'T1', 'T2'
    """
    agent = _norm(agent_name)
    tid = str(task_id).strip()

    if state["plan_status"] != "accepted":
        return "❌ Plan accepted nahi hua abhi tak."
    if tid not in state["tasks"]:
        return f"❌ Task '{tid}' exist nahi karta."

    task = state["tasks"][tid]
    if _norm(task.get("assigned_to", "")) != agent:
        return f"❌ '{tid}' {task['assigned_to'].upper()} ka task hai, {agent.upper()} ka nahi."
    if task["status"] == "in_progress" and _norm(task.get("claimed_by", "")) == agent:
        return f"⚠️ '{tid}' already tumhare paas in-progress hai."

    state["tasks"][tid].update({
        "status": "in_progress",
        "claimed_by": agent,
        "started_at": datetime.now().isoformat()
    })
    state["project_status"] = "executing"
    _save(state)

    return (f"🚀 Task '{tid}' claimed by {agent.upper()}!\n"
            f"Title      : {task['title']}\n"
            f"Description: {task['description']}\n\n"
            f"Kaam complete hone par submit_result() use karna.")

# ─── TOOL 6 · submit_result ───────────────────────────────────────────────────
@mcp.tool()
def submit_result(agent_name: str, task_id: str, summary: str, output: str) -> str:
    """
    Completed task ka result submit karo.
    agent_name : Submit karne wala agent
    task_id    : Completed task ID
    summary    : Kya kiya — brief summary
    output     : Output / code changes / file paths / test output
    """
    agent = _norm(agent_name)
    tid = str(task_id).strip()

    if tid not in state["tasks"]:
        return f"❌ Task '{tid}' nahi mila."
    if _norm(state["tasks"][tid].get("claimed_by", "")) != agent:
        return f"❌ Tumne yeh task claim nahi kiya tha. Claimed by: {state['tasks'][tid].get('claimed_by', 'None')}."

    state["tasks"][tid].update({
        "status": "submitted",
        "completed_at": datetime.now().isoformat()
    })
    state["results"][tid] = {
        "submitted_by": agent,
        "summary": summary,
        "output": output,
        "submitted_at": datetime.now().isoformat(),
        "verified": False
    }

    all_submitted = all(t["status"] in {"submitted", "verified", "done"} for t in state["tasks"].values())
    if all_submitted:
        state["project_status"] = "reviewing"
    _save(state)

    other = "opencode" if agent == "antigravity" else "antigravity"
    return (f"✅ Result submitted for '{tid}' by {agent.upper()}!\n"
            f"Summary: {summary}\n\n"
            f"📢 {other.upper()} — verify_result() se '{tid}' verify karo.")

# ─── TOOL 7 · verify_result ───────────────────────────────────────────────────
@mcp.tool()
def verify_result(agent_name: str, task_id: str, verdict: str, feedback: str) -> str:
    """
    Dusre agent ke result ko verify karo aur mistakes batao.
    agent_name : Verify karne wala agent
    task_id    : Verify karna wala task
    verdict    : 'approved' ya 'needs_revision'
    feedback   : Review notes aur findings
    """
    agent = _norm(agent_name)
    v_norm = _norm(verdict)
    tid = str(task_id).strip()

    if tid not in state["results"]:
        return f"❌ '{tid}' ka result abhi submit nahi hua."
    if _norm(state["results"][tid]["submitted_by"]) == agent:
        return "❌ Apna khud ka result verify nahi kar sakte."

    state["verifications"][tid] = {
        "verified_by": agent,
        "verdict": v_norm,
        "feedback": feedback,
        "verified_at": datetime.now().isoformat()
    }

    if v_norm == "approved":
        state["tasks"][tid]["status"] = "done"
        state["results"][tid]["verified"] = True
        all_done = all(t["status"] == "done" for t in state["tasks"].values())
        if all_done:
            state["project_status"] = "done"
        _save(state)
        return f"✅ Task '{tid}' APPROVED by {agent.upper()}!\nFeedback: {feedback}"
    else:
        state["tasks"][tid]["status"] = "needs_revision"
        state["project_status"] = "executing"
        _save(state)
        original = state["results"][tid]["submitted_by"]
        return (f"🔁 Task '{tid}' NEEDS REVISION (by {agent.upper()})\n\n"
                f"Issues / Feedback:\n{feedback}\n\n"
                f"📢 {original.upper()} — claim_task() se wapas lo aur fix karo.")

# ─── TOOL 8 · get_project_status ──────────────────────────────────────────────
@mcp.tool()
def get_project_status() -> str:
    """Poore project ka full status dekho."""
    STATUS_EMOJI = {"idle": "💤", "discussing": "💬", "planning": "📐",
                    "executing": "⚙️", "reviewing": "🔍", "done": "🎉"}
    TASK_EMOJI = {"pending": "⏳", "in_progress": "🔄", "submitted": "📤",
                  "done": "✅", "needs_revision": "🔁"}

    lines = [
        "╔══════════════════════════════════════╗",
        "║       PROJECT STATUS REPORT          ║",
        "╚══════════════════════════════════════╝",
        f"Session : {state['session_id']}",
        f"Status  : {STATUS_EMOJI.get(state['project_status'], '')} {state['project_status'].upper()}",
        f"Plan    : {state['plan_status'].upper()}",
    ]
    if state.get("plan"):
        lines.append(f"Project : {state['plan']['title']}")

    if state.get("tasks"):
        lines.append(f"\n📋 TASKS ({len(state['tasks'])} total):")
        for tid, t in state["tasks"].items():
            em = TASK_EMOJI.get(t["status"], "❓")
            lines.append(f"  {em} [{tid}] {t['title']} ({t['assigned_to'].upper()}) — {t['status'].upper()}")
            if tid in state.get("verifications", {}):
                v = state["verifications"][tid]
                lines.append(f"       └─ {v['verdict'].upper()} by {v['verified_by'].upper()}")
                if v["verdict"] == "needs_revision":
                    lines.append(f"          Issues: {v['feedback']}")

    if state.get("messages"):
        lines.append(f"\n💬 Discussion: {len(state['messages'])} messages")

    return "\n".join(lines)

# ─── TOOL 9 · reset_session ───────────────────────────────────────────────────
@mcp.tool()
def reset_session() -> str:
    """Naya project shuru karo. Sab kuch clear ho jayega."""
    global state
    state = _fresh_state()
    _save(state)
    return f"🔄 Fresh session started: {state['session_id']}"

# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Agent Bridge MCP Server starting on Streamable HTTP transport...")
    print("   Endpoint: http://127.0.0.1:8765/mcp")
    print("=" * 55)
    mcp.run(transport="streamable-http")
