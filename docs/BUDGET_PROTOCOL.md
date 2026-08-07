# 💰 Makima Development: $35/Week ($5/Day Avg) & 4-Hour Reset Protocol

This document outlines the operational strategy for building **Makima** within a **$35/week ($5/day average) token allowance** with periodic **4-hour reset windows** using **Claude Opus 4.8** (`claude-opus-4-8`) via Aerolink.

---

## 1. Why Budget Strategy Matters with Opus 4.8

Claude Opus 4.8 is an elite reasoning model with a 1M context window. With a weekly rolling budget ($35/week) and 4-hour reset intervals, you have significant tactical advantage if managed correctly.

### 4-Hour Reset Strategy ("Use It or Lose It" Windows)
- **Before a Reset Window**: Execute heavy tasks (e.g., multi-file refactoring, concurrency fixes, architecture planning) to make full use of expiring quota.
- **After a Reset Window**: Start with a fresh context (`/compact` or new terminal session) for clean, high-speed execution.

### Target Spend Breakdown ($35/Week -> ~$5.00/Day Avg)
- **Morning Session**: ~$2.00 (Core architecture or backend feature implementation)
- **Afternoon Session**: ~$2.00 (UI integration, WebSocket wiring, or complex debugging)
- **Buffer / Testing**: ~$1.00 (Targeted bug fixes and automated test runs)

---

## 2. The 4 Golden Rules of Token Conservation

### Rule 1: Always Use `-p` (Plan Mode) Before Coding
Before making modifications to `apps/brain` or `apps/ui`, ask Claude for a concise plan:
```powershell
claude -p "In apps/brain/window_manager.py, give me a 3-bullet plan to fix window focus tracking without changing code."
```
Once approved, open an interactive session and tell it: *"Implement the approved window focus tracking plan."*

### Rule 2: Force Focused File Reads
Never use vague prompts like: *"Look around the project and fix audio bugs."*  
Always name the exact module:
```powershell
claude -p "Check apps/brain/agents/media_agent.py around line 40 and fix the null error."
```

### Rule 3: Use `/compact` After Every Completed Task
In an interactive Claude Code session, type:
```powershell
/compact
```
This summarizes earlier messages and removes old file snapshots from context, cutting per-message cost by up to 80%.

### Rule 4: Offload Cheap Tasks to Free Backends
Makima has integrated support for **OpenRouter Free Tier** and **Groq** in `.env`.
- Use Opus 4.8 for: Race conditions, multi-agent deadlocks, design system changes, and WebSocket protocol changes.
- Use cheaper/free models for: Writing docstrings, generating simple tests, README updates, or formatting CSS.

---

## 3. Quick CLI Cheat-Sheet

| Command / Prompt | Purpose | Token Impact |
| :--- | :--- | :--- |
| `claude` | Starts interactive session (reads `CLAUDE.md`) | Low (Optimized via guardrails) |
| `/cost` (inside chat) | Checks current dollar expenditure | **Free / 0 tokens** |
| `/compact` (inside chat) | Compresses chat context | **Saves 50-80% on next turns** |
| `claude -p "..."` | One-shot print prompt without interactive loop | Lowest cost for quick queries |

---

## 4. Automated Guardrail (`CLAUDE.md`)
The root `CLAUDE.md` file automatically instructs `claude.cmd` to:
- Avoid speculative multi-file reads
- Refrain from re-printing unchanged code in chat
- Recommend `/compact` after 10 turns
