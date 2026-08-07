# Makima v7.1 — Build TODO

## Step 0 (verify_build.ps1)
- [x] Run `scripts/verify_build.ps1` ✅ Script created

## Step 1 (Rust core + PyO3)
- [x] Write `crates/makima-core/Cargo.toml`
- [x] Write `crates/makima-core/src/lib.rs`
- [x] Write `crates/makima-core/src/vector_index.rs` (HNSW)
- [x] Write `crates/makima-core/src/triple_store.rs` (SQLite WAL)
- [x] Write `crates/makima-core/src/checkpoint.rs` (CBOR atomic)
- [ ] Run `maturin develop` to compile PyO3 bindings

## Step 2-4 (C++ services + Python skeleton)
- [x] All 7 native service stubs created (audio, whisper, embed, screen, notify, clipboard, input)
- [x] All 7 proto files present

## Python Brain Modules (ALL COMPLETE ✅)
- [x] ws_protocol.py
- [x] ai_handler.py
- [x] command_router.py (+ personality engine wired in)
- [x] agent_orchestrator.py
- [x] watchdog_manager.py
- [x] health_aggregator.py
- [x] eternal_memory.py
- [x] entity_extractor.py
- [x] memory_forget.py
- [x] learning_engine.py
- [x] app_learner.py
- [x] cost_tracker.py
- [x] context_budget.py
- [x] screen_reader.py
- [x] speech_orchestrator.py
- [x] clipboard_handler.py
- [x] multimodal_handler.py
- [x] skill_teacher.py
- [x] tool_registry.py
- [x] focus_profiles.py
- [x] offline_queue.py
- [x] agent_guardrails.py
- [x] window_manager.py
- [x] uia_bridge.py
- [x] browser_controller.py
- [x] messaging_hub.py
- [x] notification_hub.py
- [x] calendar_adapter.py
- [x] ollama_manager.py
- [x] personality.py (13-emotion dynamic system)
- [x] state_checkpointer.py
- [x] rate_limit_manager.py
- [x] disk_guard.py
- [x] main.py (FastAPI entry point, all modules wired)

## All 9 Agents (COMPLETE ✅)
- [x] base_agent, commander, research, code, creative
- [x] memory, system, messaging, media, automation

## Wiring
- [x] Wire instantiated modules into `apps/brain/main.py` lifespan
- [x] Personality engine integrated into CommandRouter

## Remaining
- [ ] Run `maturin develop` for Rust bindings
- [ ] Electron UI (apps/ui/) — SKIPPED per user request
- [ ] `python -m py_compile` for all brain modules
- [ ] Start FastAPI app and verify `/health` + WS handshake
