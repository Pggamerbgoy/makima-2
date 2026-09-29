# 🔄 Makima v2 — New Session Handoff & Context Briefing
> **Notice for the Next Assistant/Chat Session**: Read this file first! It contains the complete conversation history, architectural decisions, and the exact next step to resume immediately.

---

## 🎯 Executive Summary & User Goal

The user wants to **rebuild Makima completely from scratch** in the fresh folder `makima_v2/`.
- **Inspiration & Pattern**: **Nous Research Hermes Agent** (`nousresearch/hermes-agent`) — persistent state, modular toolsets, multi-platform gateway daemon, dual-tier memory, and self-improving Markdown skills.
- **Why Rebuild?**: Makima v1 suffered from monolithic God-modules (`system_tools.py` 4,300 LOC, `sdk_bridge.py` 2,900 LOC), hardcoded Win32 assumptions, and SDK bridge bloat. Makima v2 must be clean, modular, and 100% dynamic.

---

## 🔒 4 Non-Negotiable Core Rules (Established With User)

1. **Ruflo Swarm Coordination**:
   - The project is coordinated via the Ruflo Swarm Framework.
   - Roles: `ruflo-orchestrator-lead` (Planning), `ruflo-arch-auditor` (Architecture & Concurrency), `ruflo-code-auditor` (Tools & Interfaces).
   - Tasks and memories are logged to Ruflo Swarm ledger.
2. **Free-Tier & Zero-Cost First Policy**:
   - **Primary Model**: Google GenAI SDK (Gemini 2.5 Flash free tier on Google AI Studio, Gemini 3.8 Live audio).
   - **OpenAI & Anthropic**: Sirf free tiers / free endpoints use honge (Groq Free Tier Llama 3.3 70B, OpenRouter Free models, Ollama 100% local offline).
   - **Zero Mandatory Paid Keys**: User par koi paid credit card API subscription enforce nahi hoga.
3. **The Zero-Hardcoding & 100% Dynamic Mandate**:
   - Tools auto-discovered via `@tool` decorator (`tools/**/`).
   - Models auto-probed from env vars and local Ollama (`/api/tags`).
   - Skills loaded live from `~/.makima/skills/*.md` without restart.
   - OS paths dynamically resolved via `pathlib.Path.home()` and `platform.system()`.
   - Context budget calculated dynamically based on active model's true token window (1M vs 128k vs 8k).
4. **The 3-Step Pre-Implementation Gate (Before ANY Code is Written)**:
   - **Step A**: Explicit TODO checklist for the module.
   - **Step B**: Data Flow & Caller-Callee mapping (kaha call ho raha hai, callers kaun hain).
   - **Step C**: Live Verification Script execution (`tests/verify_<module>.py`) in terminal to prove runtime execution before marking done.

---

## 📁 What Was Completed in the Previous Session

The fresh folder `c:\code\makima\makima_v2\` was completely created with all foundational documentation and scaffolding:
1. `MASTER_PLAN.md`: Full 6-phase engineering build roadmap and priorities.
2. `RESOURCES_MANIFEST.md`: Complete catalog of official SDKs, pre-trained weights, free APIs, and OS hooks.
3. `ENGINEERING_RULES.md`: Mandatory engineering rules and swarm protocols.
4. `BUILD_SEQUENCE.md`: 18-step chronological build list with Mermaid dependency graph.
5. `TODO.md`: Interactive checkbox list tracking all 18 steps and subtasks.
6. `README.md`: Architecture overview and quickstart commands.
7. `pyproject.toml`: Production dependencies with exact Windows stability pins.
8. `configs/settings.yaml`: Dynamic configuration template.
9. Package skeletons with `__init__.py`:
   - `makima/core/`, `makima/provider/`, `makima/tools/`, `makima/gateway/`, `makima/memory/`, `makima/skills/`, `makima/voice/`, `makima/api/`, `makima/cli/`.

---

## 🚀 Immediate Next Step When You Start The New Chat

**Resume at Step 1 of Phase 1:**
- **Module**: `makima/config.py` (Dynamic Configuration Engine using `pydantic-settings`).
- **Protocol to follow**:
  1. Output mandatory `[SKILL CHECK]`.
  2. Present the **Step 1 TODO Checklist** and **Data Flow Diagram** (how `config.py` loads `settings.yaml` + `.env` and serves all callers).
  3. Write `makima/config.py`.
  4. Write and execute `tests/verify_config.py` in the terminal using PowerShell.
  5. Check off Step 1 in `TODO.md` and proceed to **Step 2 (Provider Adapters & Circuit Breakers)**.

---

## 💬 Verbatim User Preferences
- *"ha fresh folder bna ke karo"*
- *"google sdk ke alawan open ai and anthropic ke free features he use karna"*
- *"koi bhi chije hardcoded nhi honi chahiye ok update the plan mostly dynamic"*
- *"MOSTLY WORK MUST BE DONE BY THE RUFLO AGENTS OK"*
- *"KOI BHI MODULE YA KUCH BHI BANANE SE PEHLE TODO LIST BANANA HAI AND DATA FLOW USKE CONNECTION KAHA CALL HO RHA YE SAB DEKHNAHAI AND WORK KAR RAH HAI YA NHI"*
