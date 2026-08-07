# Makima OS v7.2 — Vast Agent Situational Encyclopedia & Multi-Agent Matrix

**Location:** `apps/brain/coordination/agent_situational_encyclopedia.py`  
**Purpose:** An exhaustive, dynamic operational knowledge base embedded directly into Makima's **Commander Agent** and **Capability Registry** to ensure she can handle deep, unexpected, compound, or highly technical multi-domain requests in English, Hindi, or Hinglish.

---

## 1. Why This Encyclopedia Exists

Previous agent orchestration systems relied on a brief 10-line static prompt listing surface-level tool names. When users asked complex, informal, or multi-step questions (e.g., *"bhai desktop saaf kar de aur arijit ka gana chala de aur mera rust project build kar"*), a surface-level prompt would often fail to decompose the task correctly.

The **Agent Situational Encyclopedia** solves this by providing Makima with over 100 proven multi-agent orchestration patterns across 12 major engineering, OS, media, data, security, and automation domains.

---

## 2. Complete 15-Agent Functional Capability Matrix

| Agent | Primary Role & Specialization | Key Tools & Capabilities | Proven Situational Use Cases |
| :--- | :--- | :--- | :--- |
| **commander_agent** | Strategic DAG Orchestration & Plan Synthesis | Decomposes tasks into subtasks (`st_1`, `st_2`), dependency graphs, dynamic routing | Compound multi-agent tasks, fallback routing, cross-domain pipelines |
| **system_agent** | OS Control, File Ops, & Desktop Organization | `organize_desktop`, `move_file`, `copy_file`, `rename_file`, `kill_process`, win32 bridge | *"Desktop idhar udhar ki files lineup karo"*, *"Downloads saaf karo"*, process recovery |
| **media_agent** | Music & Video Playback Control | Zero-shot LLM semantic parsing, Spotify/YouTube playback, `set_volume`, playlists | *"wo purana arijit ka sad song chalao aur volume kam karo"*, podcast bookmarks |
| **code_agent** | Code Generation, AST Repair, & Debugging | `code_read`, `code_edit`, AST parser, sandbox runner, Qwen Coder routing | Fixing async deadlocks, multi-file refactoring, writing unit tests, rust/python lints |
| **research_agent** | Multi-Hop Web Research & Executive Synthesis | `web_search`, domain credibility scoring, corporate entity disambiguation | Competitive LLM benchmark comparison, market intelligence reports |
| **data_analyst_agent** | Polars/Pandas DataFrame & Charting | CSV/JSON ingestion, statistical summaries, trend visualization, matplotlib/plotly | Sales growth charting, quarterly revenue summaries, outlier detection |
| **security_agent** | Pre-Commit Security & Vulnerability Auditing | Static AST regex scan, hardcoded secret check, SQL injection audit, port scanning | API key leak prevention, dependency vulnerability checks, docker port scans |
| **devops_agent** | CI/CD, Docker Containers, & Kubernetes | Docker logs/restart, build script execution, deployment health checks | Docker OOM crash recovery, port collision resolution, CI pipeline triggers |
| **document_agent** | Professional Excel, Word, PDF, & PowerPoint | `generate_xlsx`, `generate_docx`, `generate_pptx`, PDF reports | Multi-sheet financial reports, executive slide decks, architecture specs |
| **browser_agent** | Stealth Web Automation & Scraping | Headless/Visible navigation, DOM distillation, form filling, element click | Monthly invoice downloads, automated portal logins, dynamic web scraping |
| **messaging_agent** | Multi-Channel Dispatch & Notifications | WhatsApp, Telegram, Discord webhooks, Email drafting and sending | Daily standup summaries, automated CI/CD build alerts to team chats |
| **memory_agent** | Long-Term EternalMemory Vector Search | HNSW vector store, SQLite WAL integrity, user preference recall | Remembering custom folder rules, preferred coding styles, user work habits |
| **automation_agent** | Reminders, Background Timers, & Cron Jobs | One-shot timers, recurring cron expressions, UI macro recording | Scheduled weekly disk cleanups, recurring health checks, build monitors |
| **voice_agent** | Speech Synthesis (Kokoro-ONNX) & STT | Kokoro TTS streaming, Whisper gRPC STT, voice speed/pitch tuning | Reading aloud executive briefings, hands-free voice command confirmation |
| **creative_agent** | Storytelling, Copywriting, & Design Ideation | UX copywriting, slide deck text drafting, image prompt creation | Creating compelling slide copy, drafting executive summaries, brand copy |

---

## 3. The 12 Situational Domains & Execution Strategies

### Domain 1: OS & Desktop File Management (`system_agent` + `memory_agent`)
- **Situation:** *Cluttered Desktop / "Idhar Udhar Ki Files Lineup Karo"*
- **DAG Strategy:** 
  1. `memory_agent` retrieves any custom user directory rules.
  2. `system_agent.organize_desktop(path="Desktop", mode="by_extension")` classifies files by extension (.pdf, .png, .exe, .py) and moves them into categorized folders.
  3. Returns a structured markdown table of moved files and created directories.

### Domain 2: Media & Entertainment Control (`media_agent` + `system_agent`)
- **Situation:** *Semantic Natural Language Music & Volume Commands*
- **DAG Strategy:** 
  1. Zero-shot LLM semantic parser extracts track query (`"arijit sad songs"`), platform (`"youtube"` or `"spotify"`), and volume adjustment (`-15`).
  2. `system_agent.set_volume()` adjusts system audio.
  3. `media_agent.play_media()` launches target stream with zero hardcoded regexes.

### Domain 3: Full-Stack Software Engineering (`code_agent` + `security_agent` + `devops_agent`)
- **Situation:** *Async Deadlock / Race Condition in Backend Event Loop*
- **DAG Strategy:**
  1. `code_agent` inspects `asyncio.Lock`, singletons, and thread pool dispatch.
  2. Performs a structured **Devil's Advocate** self-critique pass.
  3. Applies an AST-verified patch and validates compilation via `py -3 -m py_compile`.

### Domain 4: Web Research & Competitive Analysis (`research_agent` + `data_analyst_agent` + `document_agent`)
- **Situation:** *Comparative LLM Serving Framework Spreadsheet*
- **DAG Strategy:**
  1. `research_agent` gathers multi-hop web metrics with domain credibility scores.
  2. `data_analyst_agent` normalizes latency, throughput, and licensing data.
  3. `document_agent.generate_xlsx()` outputs a professionally formatted spreadsheet.

### Domain 5: DevOps & Container Diagnostics (`devops_agent` + `system_agent`)
- **Situation:** *Docker Container OOM Crash / Service Recovery*
- **DAG Strategy:**
  1. `devops_agent.docker_logs()` retrieves trailing stderr stack traces.
  2. `system_agent` checks host memory and port binding collisions.
  3. Re-allocates resources and restarts service automatically.

### Domain 6: Pre-Commit Security Audits (`security_agent` + `code_agent`)
- **Situation:** *Secret Leak Prevention & SQL Injection Audit*
- **DAG Strategy:**
  1. `security_agent.security_scan()` audits modified AST nodes for API keys, JWTs, and SQL string concatenation.
  2. `code_agent` generates remediation patch replacing secrets with environment variable references.

### Domain 7: Data Science & Analytics Reporting (`data_analyst_agent` + `document_agent`)
- **Situation:** *CSV Sales Statistical Analysis & PowerPoint Presentation*
- **DAG Strategy:**
  1. `data_analyst_agent` computes Polars/Pandas statistical metrics (mean, std, quantiles) and renders PNG charts.
  2. `document_agent.generate_pptx()` compiles an executive slide deck with embedded chart graphics.

### Domain 8: Browser Automation & Scraping (`browser_agent` + `system_agent`)
- **Situation:** *Stealth Portal Login & Invoice Download*
- **DAG Strategy:**
  1. `browser_agent` opens visible browser window (`headless=False`), navigates portal, and clicks download.
  2. `system_agent.move_file()` transfers downloaded PDF from Downloads to `Documents/Invoices/`.

### Domain 9: Messaging & Team Notifications (`messaging_agent` + `code_agent`)
- **Situation:** *Automated Standup Dispatch to WhatsApp / Discord*
- **DAG Strategy:**
  1. `code_agent` extracts daily git commit messages and build status.
  2. `messaging_agent` formats markdown summary and dispatches via verified webhook/API.

### Domain 10: Long-Term Memory & User Adaptation (`memory_agent` + `code_agent`)
- **Situation:** *Remembering & Enforcing Preferred Code Style*
- **DAG Strategy:**
  1. `memory_agent.eternal_memory_store()` embeds user coding preferences in SQLite/HNSW store.
  2. Before any code generation, `code_agent` queries memory to match conventions.

### Domain 11: Voice & Hands-Free Interaction (`voice_agent` + `research_agent`)
- **Situation:** *Kokoro-ONNX Executive Readout*
- **DAG Strategy:**
  1. `research_agent` produces a clean executive text briefing.
  2. `voice_agent.kokoro_tts_speak()` streams high-fidelity natural speech audio.

### Domain 12: Hinglish Compound DAG Orchestration (`commander_agent` + Parallel Leaf Agents)
- **Situation:** *"Bhai desktop saaf kar de aur arijit ka gana chala de aur mera rust project build kar"*
- **DAG Strategy:**
  - **Subtask 1 (`st_1`):** `system_agent` -> organize desktop files by extension.
  - **Subtask 2 (`st_2`):** `media_agent` -> search and play Arijit Singh on YouTube/Spotify.
  - **Subtask 3 (`st_3`):** `code_agent` -> run `cargo check` on Rust project workspace.
  - **Execution:** All 3 execute in **parallel** (zero inter-dependencies). Commander returns a unified Hinglish completion report.

---

## 4. Architectural Integration & Dynamic Injection

1. **Dynamic Semantic Querying:**
   When a user sends any prompt, `CommanderAgent._generate_plan()` calls `get_situational_encyclopedia().build_commander_situational_prompt(query=message)`.
2. **Relevance Scoring:**
   The encyclopedia computes Jaccard and overlap similarity scores against its 100+ patterns and injects the top matching multi-agent DAG strategies directly into the LLM system prompt.
3. **Zero-Crash Resilience:**
   If Pydantic, external dependencies, or specific leaf agents are offline, graceful fallback stubs ensure Makima's planning engine never crashes.
4. **Verified Performance:**
   100% unit test coverage across 12 domains via `tests/test_agent_situational_encyclopedia.py` and `tests/test_100_situational_tasks.py`.

---

## 5. 100-Task Verification Benchmark Report

We executed an exhaustive verification suite (`tests/test_100_situational_tasks.py`) testing **100 DIFFERENT REAL-WORLD TASKS / QUERIES** in English, Hindi, Hinglish, OS control, DevOps, Security, Music/Media, Code Refactoring, Debugging, Data Science, Browser Scraping, WhatsApp/Discord messaging, and Compound DAGs against the `AgentSituationalEncyclopedia`.

### Verification Scorecard (100 / 100 Passed - 100.0% Accuracy)
```text
==================================================================================
                      100-TASK VERIFICATION REPORT
==================================================================================
Total Tasks Tested : 100
Total Tasks Passed : 100 / 100 (100.0%)
Average Relevance  : 0.605 (High semantic confidence across all queries)
Execution Latency  : 53.20 ms total (0.53 ms / query - zero event loop blocking)
----------------------------------------------------------------------------------
Domain Distribution across 100 Tasks:
  - OS & Desktop              : 10 tasks matched
  - Media Control             : 10 tasks matched
  - Browser Automation        : 10 tasks matched
  - Security Auditing         : 10 tasks matched
  - Data Science              : 10 tasks matched
  - Software Engineering      : 9 tasks matched
  - DevOps & Containers       : 9 tasks matched
  - Web Research              : 8 tasks matched
  - Voice & Speech            : 7 tasks matched
  - Messaging & Alerts        : 6 tasks matched
  - Compound Orchestration    : 6 tasks matched
  - Memory & Personalization  : 5 tasks matched
==================================================================================
```
Every single query mapped to its correct domain, primary agents, supporting agents, tools, and execution steps with zero regex hardcoding.

