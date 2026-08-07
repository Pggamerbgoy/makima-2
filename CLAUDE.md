# CLAUDE.md — Makima Project & Token Budget Guardrails

> **IMPORTANT FOR CLAUDE CODE CLI**: This project operates under a **$35/week ($5/day average) token allowance** with 4-hour reset windows using `claude-opus-4-8`. You MUST adhere to the token efficiency and budget guardrails below on every turn.

---

## 💰 1. $35/Week ($5/Day Avg) & 4-Hour Reset Guardrails (STRICT RULES)

1. **Leverage 4-Hour Reset Windows**:
   - When approaching a 4-hour reset window, prioritize complex architectural tasks or multi-file refactoring so expiring quota is utilized effectively.
   - For regular hours, pace spending to stay within the ~$5.00/day average ($35/week total).
2. **No Blind Repo Scans**:
   - DO NOT read entire directories or open multiple files speculatively.
   - Always use search/ripgrep tools to pinpoint exact file paths, symbols, and line numbers before reading a file.

2. **Concise Responses & No Code Dumping**:
   - DO NOT print entire file contents or unchanged code blocks in your markdown replies.
   - Provide only concise explanations, small diffs, or the exact lines being modified.

3. **"Measure Twice, Cut Once" (Plan-First Workflow)**:
   - For any non-trivial feature or bug fix, first output a **concise 3-5 bullet point Implementation Plan** naming the exact files to be touched.
   - Wait for explicit user confirmation before executing file modification tools.

4. **Context Hygiene (/compact Reminder)**:
   - If a conversation exceeds 10 turns or after completing a major task, proactively advise the user to run `/compact` to clear accumulated chat history.

5. **Model Triage & Free-Tier Routing**:
   - Save `claude-opus-4-8` for architecture, multi-file refactoring, race-conditions, and complex debugging.
   - For trivial documentation, comments, or CSS formatting, advise the user when appropriate that simple tasks can be done via free tiers.

---

## 🏗️ 2. Makima Architecture & Core Paths

- **`apps/brain/`** — Python core backend, AI Handler (`ai_handler.py`), multi-agent orchestration (`agents/`), window management (`window_manager.py`), and WebSocket endpoints.
- **`apps/ui/`** — React / TypeScript / Vite desktop overlay frontend.
- **`docs/` & `memory-bank/`** — Architectural documentation and decision logs.

---

## ⚡ 3. Efficient Testing & Commands

- **Run Specific Backend Tests (Avoid full suite scans to save tokens)**:
  ```powershell
  pytest tests/test_<name>.py -v
  ```
- **UI Lint & Build**:
  ```powershell
  cd apps/ui; npm run build
  ```
- **Check Current Spend**:
  - Type `/cost` at any time inside the interactive Claude Code CLI session.
