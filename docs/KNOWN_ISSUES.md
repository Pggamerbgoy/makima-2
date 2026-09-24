# 📋 Makima Known Issues & Backlog

Tracking minor non-blocking edge cases and routing adjustments identified during audits and live testing.

---

### 1. App Launch Tool Routing (RESOLVED ✅ — Commit `feac08f`)
- **Status**: Fixed and verified via `tests/test_app_routing_disambiguation.py`.
- **Changes**: Disambiguated `search_installed_apps` (discovery/listing only) vs `launch_app` (start/open applications) in tool descriptions and `SemanticPlanner` prompt. Prompt `"notepad kholo"` now routes 100% to `launch_app`.

---

### 2. SystemAgent Database Connection Resource Warning (RESOLVED ✅)
- **Status**: Fixed. Original `SystemAgent` module was retired; remaining unclosed sqlite was the module-level `_APPS_FTS_CONN` in `system_tools.py` plus `DurableTaskEngine` holding an open `EventStore`.
- **Changes**: Added `close_apps_fts()` (atexit + bootstrap stop-hook) and `DurableTaskEngine.stop()` now closes the shared `EventStore` connection.

---

### 3. Subprocess Transport Closed Pipe Warning on Shutdown (RESOLVED ✅)
- **Status**: Fixed. Root cause was Proactor child-process handles GC'd before loop teardown / kill path not reaping process.
- **Changes**: `AppBootstrap.shutdown_services()` now gathers cancelled background tasks, runs stop hooks, then yields the loop (`asyncio.sleep(0)` + drain) before close. `AsyncMcpMultiplexer.stop()` awaits `process.wait()` after `kill()`.

---

### 4. SkillLibrary exec re-verification (RESOLVED)
- **Status**: `_execute_steps_safely` now re-runs `verify_skill_safety` before every `exec`, so skills loaded from SQLite cannot bypass the AST firewall used only at synthesis time.

---

### 5. Finance / Calendar / Notification store persistence (RESOLVED)
- **Status**: All three stores now persist atomically under `~/.makima/` (`finance_expenses.json`, `calendar_events.json`, `notifications.json`) with datetime round-tripping.
