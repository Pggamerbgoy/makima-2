# 📋 Makima Known Issues & Backlog

Tracking minor non-blocking edge cases and routing adjustments identified during audits and live testing.

---

### 1. App Launch Tool Routing (RESOLVED ✅ — Commit `feac08f`)
- **Status**: Fixed and verified via `tests/test_app_routing_disambiguation.py`.
- **Changes**: Disambiguated `search_installed_apps` (discovery/listing only) vs `launch_app` (start/open applications) in tool descriptions and `SemanticPlanner` prompt. Prompt `"notepad kholo"` now routes 100% to `launch_app`.

---

### 2. SystemAgent Database Connection Resource Warning
- **Symptom**: `ResourceWarning: unclosed database in <sqlite3.Connection object>` emitted during `SystemAgent._register_tools_with_registry`.
- **Root Cause**: Ad-hoc sqlite connection opened in tool registration helper without context manager `with sqlite3.connect(...) as conn:`.
- **Planned Fix**: Wrap sqlite connections in `SystemAgent` inside context managers or persistent connection pool.

---

### 3. Subprocess Transport Closed Pipe Warning on Shutdown
- **Symptom**: `ValueError: I/O operation on closed pipe` warning logged during graceful shutdown in `BaseSubprocessTransport.__del__`.
- **Root Cause**: Python 3.14 Proactor Event Loop garbage-collecting child process handles before async loop fully terminates.
- **Planned Fix**: Explicitly await process termination in `shutdown_services()` before closing loop.
