# Makima v7.1 — Build Handoff Guide

> **PURPOSE:** This document lets you (or any AI assistant like BlackBox) continue building Makima from where Antigravity left off. Everything needed is here.

## Project Location
```
C:\Users\kamit\.gemini\antigravity\scratch\makima\
```

## Architecture Reference
The full architecture spec is at:
```
C:\Users\kamit\Downloads\makima_v7_improved_plan.md
```
**READ THAT FILE FIRST.** It has every module spec, failure matrix, edge case, and build order.

---

## What Has Been Built ✅

### Proto Files (all 7 — COMPLETE)
```
proto/
├── health.proto       ✅ Universal health check (all services implement this)
├── audio.proto        ✅ WASAPI audio capture
├── whisper.proto      ✅ whisper.cpp STT
├── embed.proto        ✅ ONNX embeddings
├── screen.proto       ✅ DXGI screen capture
├── notify.proto       ✅ Windows toast notifications
└── clipboard.proto    ✅ Clipboard watcher
```

### Config Files (COMPLETE)
```
configs/
├── default.yaml           ✅ All 30+ config keys with full documentation
└── focus_profiles.yaml    ✅ Work/Quiet/Meeting/Gaming presets
```

### Python Brain — Core Modules (COMPLETE)
```
apps/brain/
├── __init__.py            ✅
├── ws_protocol.py         ✅ 30+ WS message types, builders, validation
├── ai_handler.py          ✅ 6-backend failover, circuit breakers, rate limit, sanitization
├── command_router.py      ✅ Intent classification, priority queue, context building
├── agent_orchestrator.py  ✅ 9-agent lifecycle, guardrails, parallel dispatch
├── watchdog_manager.py    ✅ gRPC health polling, exponential backoff restart
└── health_aggregator.py   ✅ Single health snapshot, dashboard source
```

### Python Brain — All 9 Agents (COMPLETE)
```
apps/brain/agents/
├── __init__.py            ✅
├── base_agent.py          ✅ Abstract base with LLM access, tools, guardrails
├── commander_agent.py     ✅ Task decomposition, parallel dispatch, recursion guard
├── research_agent.py      ✅ Web search + synthesis
├── code_agent.py          ✅ Code gen/debug/review
├── creative_agent.py      ✅ Writing/stories
├── memory_agent.py        ✅ Memory CRUD, graph queries
├── system_agent.py        ✅ Desktop control with confirmation gates
├── messaging_agent.py     ✅ Send approval gates, disambiguation
├── media_agent.py         ✅ Music/video control
└── automation_agent.py    ✅ Browser control, CAPTCHA handling
```

---

## What Still Needs To Be Built ❌

### Priority 1 — Remaining Python Brain Modules
Create these files in `apps/brain/`. Refer to `makima_v7_improved_plan.md` for exact specs per module.

| File | What It Does | Reference Section |
|------|-------------|-------------------|
| `eternal_memory.py` | SQLite + Rust HNSW facade. `save_turn()`, `search(query, k)`, `get_history(n)`. WAL mode, 3s buffered write. PyO3 calls via `run_in_executor()` | "MEMORY & KNOWLEDGE" in arch doc |
| `entity_extractor.py` | Daemon thread, LLM triple extraction, min confidence 0.7, 60s thread timeout, conflict detection | Same section |
| `memory_forget.py` | "Forget X" handling, tombstone, cascade check, TTL support | Same section |
| `learning_engine.py` | Feedback DB (thumbs up/down), pattern analyzer, proactive suggestions | Same section |
| `app_learner.py` | `win32gui` foreground watcher (500ms), `_learning_in_progress` set guard | Same section |
| `state_checkpointer.py` | 15s periodic CBOR checkpoint via PyO3. Atomic write (tmp→fsync→rename) | "StateCheckpointer" in arch doc |
| `rate_limit_manager.py` | Per-provider sliding 60s window. `can_send()`, `record_usage()`, 429 handling | "RateLimitManager" in arch doc |
| `disk_guard.py` | Poll `~/.makima/` every 60s. 80%/90%/95% thresholds. Emergency compaction | "DiskGuard" in arch doc |
| `cost_tracker.py` | Per-model cost table, SQLite logging, budget enforcement (warn 80%, block 100%) | "cost_tracker" in arch doc |
| `context_budget.py` | Backend-aware token allocation. Splits: history 40%, memory 25%, graph 15%, screen 10%, system 10% | "context_budget.py" in arch doc |
| `screen_reader.py` | gRPC to screen service, frame delta check, Gemini Vision primary, OCR fallback | "PERCEPTION" in arch doc |
| `speech_orchestrator.py` | gRPC to audio+whisper, wake word→STT→Router pipeline, TTS, ducking, barge-in, PTT | Same section |
| `clipboard_handler.py` | gRPC to clipboard service, ClipboardContext (max 4000 chars), never stores to memory | Same section |
| `multimodal_handler.py` | Image/PDF/code file drops. base64→vision, pdfminer, syntax label, CSV/JSON schema | Same section |
| `skill_teacher.py` | WASM sandbox (wasmtime), capability manifest enforcement | "SKILLS & TOOLS" in arch doc |
| `tool_registry.py` | Central tool registry, JSON manifest for LLM, tool call dispatch | Same section |
| `focus_profiles.py` | Load YAML presets, toggle features, < 200ms apply | "INTERFACE" in arch doc |
| `offline_queue.py` | Buffer messages when offline, replay on reconnect, stale screen→None, cap 50 | "SAFETY" in arch doc |
| `agent_guardrails.py` | Per-task limits (5min/50k tokens/20 tool calls), save partial on cutoff | Same section |
| `window_manager.py` | Win32+psutil: launch, focus, close, snap. Destructive ops need confirmation | "DESKTOP CONTROL" in arch doc |
| `uia_bridge.py` | pywinauto+uiautomation wrapper, 3-retry, 5s timeout, coordinate click fallback | Same section |
| `browser_controller.py` | Playwright: managed + CDP attach, vision fallback, CAPTCHA detection | Same section |
| `messaging_hub.py` | Adapter pattern: WhatsApp/Telegram/Discord/Email. `send()` after approval only | Same section |
| `notification_hub.py` | gRPC to notify service, outbound toasts + inbound reading | "INTEGRATIONS" in arch doc |
| `calendar_adapter.py` | Google Cal/Outlook/ICS backends, graceful degradation | Same section |
| `ollama_manager.py` | `ensure_running()`, `list_models()`, `pull_model()`, `delete_model()` | Same section |
| `main.py` | FastAPI app, ASGI lifespan, REST + WebSocket endpoints, module init | Entry point |

### Priority 2 — Rust Core
Create `crates/makima-core/` with:
```
crates/makima-core/
├── Cargo.toml             # pyo3, rusqlite, serde, serde_cbor, uuid, sha2
├── src/
│   ├── lib.rs             # PyO3 module registration
│   ├── vector_index.rs    # HNSW: add, search, delete, snapshot, restore
│   ├── triple_store.rs    # SQLite WAL: insert, query, tombstone, cascade, TTL
│   ├── file_indexer.rs    # File hash tracking for re-indexing
│   ├── checkpoint.rs      # CBOR atomic write/restore
│   └── pyo3_bindings.rs   # #[pyclass] + #[pymethods] for all types
```

**CRITICAL PyO3 rules** (from arch doc section "PyO3 concurrency safety rules"):
1. Never call PyO3 from asyncio event loop directly → use `run_in_executor()`
2. All PyO3 calls need `asyncio.wait_for()` timeout (5s default)
3. Rust code releasing GIL (`py.allow_threads`) must never hold Python-owned data
4. HNSW `add()`/`search()` are thread-safe. TripleStore `write()` needs internal Mutex
5. Rust panic → PyO3 catches as `PyRuntimeError` → Python logs + disables component
6. No Python callbacks registered in Rust — return values only

### Priority 3 — Native C/C++ Service Stubs
Create gRPC server stubs in `native/`. Each must implement `health.proto Health.Check()`.
```
native/
├── audio/       # C: WASAPI capture stub
├── whisper/     # C++: whisper.cpp wrapper stub
├── embed/       # C++: ONNX embedder stub
├── screen/      # C++: DXGI capture stub
├── notify/      # C++: Windows toast stub
├── clipboard/   # C: WM_CLIPBOARDUPDATE stub
└── input/       # C++: SendInput stub
```

### Priority 4 — TypeScript Electron UI
Create `apps/ui/` with Electron + React + Vite. This is Step 13 in the build order.

### Priority 5 — Build Scripts
```
scripts/
├── verify_build.ps1   # Step 0 gate: check Rust, Python, Node, protoc, Ollama
├── build_all.ps1      # Unified build: cargo → maturin → npm
├── dev.ps1            # Dev server launch
└── test_all.ps1       # Run all tests
```

---

## How To Create Each Remaining Module

For EVERY module listed above, follow this pattern:

### 1. Open `makima_v7_improved_plan.md`
### 2. Find the module's spec section (use the "Reference Section" column above)
### 3. Follow this template:

```python
"""
Makima v7.1 — [Module Name]

[Copy the exact description from the arch doc's repository layout section]
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("makima.[module_name]")

# Implement exactly what the arch doc describes.
# Follow these rules:
# - All PyO3 calls → loop.run_in_executor() with 5s timeout
# - All gRPC calls → set deadline (2s interactive, 30s batch)
# - Handle every failure case from the Failure Matrix (section "Failure matrix")
# - Emit WS events using ws_protocol.py builders
# - Log errors but never crash the brain
```

### 4. Check the Failure Matrix
The arch doc has a 38-row failure table. Find rows relevant to your module:
- `P1-P19` for Python brain failures
- `N1-N10` for native service failures
- `R1-R5` for Rust/memory failures
- `M1-M8` for messaging/desktop control failures
- `T1-T7` for TypeScript UI failures

### 5. Check Edge Cases
Section "Extended edge cases per subsystem" has ~40 cases. Implement the ones for your module.

---

## main.py Template (Entry Point)

```python
"""Makima v7.1 — Brain Entry Point"""
import asyncio
import logging
import yaml
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from pathlib import Path

# Load config
with open(Path(__file__).parent.parent.parent / "configs" / "default.yaml") as f:
    CONFIG = yaml.safe_load(f)

# Import all modules
from .ws_protocol import WSMessage, PROTOCOL_VERSION
from .ai_handler import AIHandler
from .command_router import CommandRouter
from .agent_orchestrator import AgentOrchestrator
from .watchdog_manager import WatchdogManager
from .health_aggregator import HealthAggregator
# ... import remaining modules as you build them

# WebSocket connections
ws_clients: set[WebSocket] = set()

async def ws_broadcast(msg: WSMessage):
    """Broadcast a WS message to all connected clients."""
    data = msg.to_json()
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    ws_clients -= dead

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    logging.basicConfig(level=logging.INFO)
    
    # Init modules in dependency order
    ai_handler = AIHandler(CONFIG, ws_broadcast=ws_broadcast)
    # memory = EternalMemory(CONFIG)  # uncomment when built
    orchestrator = AgentOrchestrator(ai_handler, memory=None, ws_broadcast=ws_broadcast, config=CONFIG)
    router = CommandRouter(ai_handler, orchestrator, ws_broadcast=ws_broadcast)
    watchdog = WatchdogManager(CONFIG, ws_broadcast=ws_broadcast)
    health = HealthAggregator(watchdog=watchdog, ai_handler=ai_handler, ws_broadcast=ws_broadcast)
    
    await router.start()
    await watchdog.start()
    await health.start()
    
    app.state.router = router
    app.state.health = health
    
    yield
    
    # SHUTDOWN
    await router.stop()
    await watchdog.stop()
    await health.stop()

app = FastAPI(title="Makima Brain", version="7.1", lifespan=lifespan)

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "7.1"}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            msg = WSMessage.from_json(raw)
            if msg.type == "user_message":
                from .ws_protocol import generate_task_id
                task_id = generate_task_id()
                await app.state.router.handle_message(
                    task_id=task_id,
                    message=msg.payload.get("text", ""),
                )
    except Exception:
        pass
    finally:
        ws_clients.discard(ws)
```

---

## Build Order (from arch doc)

```
Step 0  → verify_build.ps1 (build gate)
Step 1  → Rust core + PyO3
Step 2  → C++ ONNX embedder
Step 3  → C audio + C++ whisper
Step 4  → Python skeleton + Watchdog + HealthAggregator  ← WE ARE HERE
Step 5  → AIHandler + RateLimitManager                   ← DONE
Step 6  → Memory + Router + Checkpoint                   ← PARTIALLY DONE
Step 7  → 3 Agents + DiskGuard
Step 8  → Speech e2e + Hotword
Step 9  → Screen + Multimodal
Step 10 → Desktop control
Step 11 → Browser + Messaging
Step 12 → Clipboard + Notifications
Step 13 → TypeScript Electron UI
Step 14 → Calendar + OllamaManager
Step 15 → Learning + Graph
Step 16 → Polish + Chaos Tests (22 scenarios in arch doc)
Step 17 → Advanced UX
```

---

## Dependencies To Install

```bash
# Python
pip install fastapi uvicorn websockets httpx pyyaml psutil

# For later modules:
pip install edge-tts pyttsx3 pywinauto uiautomation playwright pdfminer.six grpcio grpcio-tools

# Rust
cargo install maturin

# Build PyO3 bindings
cd crates/makima-core
maturin develop
```

---

## Quick Test (After Building main.py)

```bash
cd C:\Users\kamit\.gemini\antigravity\scratch\makima
pip install fastapi uvicorn websockets httpx pyyaml
uvicorn apps.brain.main:app --host 127.0.0.1 --port 8080
# Then open ws://127.0.0.1:8765 and send:
# {"v":1, "type":"user_message", "payload":{"text":"hello"}}
```

---

## Key Design Rules (Non-Negotiable)

1. **Never crash the brain.** Every error is caught, logged, and degraded gracefully.
2. **PyO3 calls always go through `run_in_executor()`** with 5s timeout.
3. **gRPC calls always have deadlines** (2s interactive, 30s batch).
4. **Destructive operations require user confirmation** (send message, kill process, delete file).
5. **Privacy Mode = Ollama only, no screen capture, no clipboard, no notifications.**
6. **API keys never appear in responses** — sanitize with regex before returning.
7. **Partial results are always saved** on guardrail cutoff — never discard work.
8. **One interactive agent at a time.** Background agents in daemon threads.
