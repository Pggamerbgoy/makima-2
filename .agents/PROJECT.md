# Project: Makima Brain SOTA Subsystem Upgrades

## Architecture
Makima Brain is an asynchronous multi-agent microkernel ecosystem in Python.
The target upgrades harden 5 core subsystems against production concurrency bottlenecks, high latency, unindexed queries, and brittle parsing:
1. `apps/brain/core/decomposition_engine.py` & `apps/brain/core/kernel.py` (DAG Waves & Atomic Preemption Leases) — **DONE**
2. `apps/brain/eternal_memory.py` & `apps/brain/embeddings.py` (SQLite FTS5 + BM25 + RRF + Ebbinghaus Decay) — **DONE**
3. `apps/brain/tools/mcp_adapter.py`, `apps/brain/tool_registry.py`, & `apps/brain/core/orchestration_engine.py` (Async MCP Multiplexing & Intent Resilience)
4. `apps/brain/learning_coordinator.py` & `apps/brain/learning_engine.py` (Bounded Reflexion Queue & Contradiction Resolution)
5. `apps/brain/voice_pipeline.py` & `apps/brain/ws_protocol.py` (In-Memory PCM STT & Clause-Boundary Streaming TTS)

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | DAG Parallel Wave Scheduling | `compute_execution_waves()` computing topological frontiers | M1 | Spec §2 |
| 2 | Atomic Preemption Leases | Generation-counted `InteractiveSlotManager` preventing slot corruption | M1 | Spec §2 |
| 3 | SQLite FTS5 BM25 Search | Sub-millisecond keyword search table & triggers in EternalMemory | M2 | Spec §3 |
| 4 | Ebbinghaus Decay & RRF Fusion | Spaced reinforcement recency & reciprocal rank fusion across dense/sparse search | M2 | Spec §3 |
| 5 | Non-blocking MCP Multiplexing | Asynchronous JSON-RPC 2.0 stdio multiplexer with unique request ID resolution | M3 | Spec §4 |
| 6 | Robust Tool Parsing & Fallback | Hardened schema validation & prompt intent classification recovery | M3 | Spec §4 |
| 7 | Bounded Reflexion Worker Loop | Non-blocking queue with backpressure for learning signals | M4 | Spec §5 |
| 8 | Contradiction Resolution | Semantic search and deprecation of conflicting behavior rules | M4 | Spec §5 |
| 9 | In-Memory Whisper STT | Zero-disk PCM to Float32 NumPy array transcription | M5 | Spec §6 |
| 10 | Clause Streaming TTS | Punctuation-delimited real-time audio chunk dispatch via WebSocket | M5 | Spec §6 |
| 11 | Zero-Mock E2E Validation | Executable test scripts in `scripts/` validating all 5 subsystems & WebSocket | M6 | Spec §7 |
| 12 | 95-Module Audit Clean Run | `scripts/audit_all_brain_modules.py` passing 95/95 modules with 0 errors | M6 | Spec §8 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 0 | Survey & Mapping | Baseline inspection of all target files and tests | none | DONE |
| 1 | Multi-Agent DAG & Concurrency | `decomposition_engine.py`, `kernel.py` | M0 | DONE |
| 2 | EternalMemory & Hybrid Search | `eternal_memory.py`, `embeddings.py` | M0 | DONE |
| 3 | Tool Runtime & MCP Multiplexing | `mcp_adapter.py`, `tool_registry.py`, `orchestration_engine.py` | M0 | IN_PROGRESS |
| 4 | Self-Learning Reflexion Loop | `learning_coordinator.py`, `learning_engine.py` | M0 | PLANNED |
| 5 | Real-Time Voice Pipeline | `voice_pipeline.py`, `ws_protocol.py` | M0 | PLANNED |
| 6 | E2E Verification & Module Audit | `scripts/`, `ws://127.0.0.1:8080/ws`, 95-module audit | M1-M5 | PLANNED |

## Code Layout
- `apps/brain/core/decomposition_engine.py` — DAG Frontier & Subtask Decomposition
- `apps/brain/core/kernel.py` — Microkernel & Interactive Slot Preemption
- `apps/brain/core/orchestration_engine.py` — Router & Intent Planning
- `apps/brain/eternal_memory.py` — Long-Term Memory, SQLite FTS5, Hybrid RRF
- `apps/brain/embeddings.py` — Dense Vector Utilities & Caching
- `apps/brain/tools/mcp_adapter.py` — Non-blocking MCP Client / Multiplexer
- `apps/brain/tool_registry.py` — Dynamic Tool Registration & Execution
- `apps/brain/learning_coordinator.py` — Reflexion Feedback Loop & Bounded Queue
- `apps/brain/learning_engine.py` — Rule Storage & Contradiction Resolution
- `apps/brain/voice_pipeline.py` — In-Memory STT & Streaming TTS Dispatcher
- `apps/brain/ws_protocol.py` — WebSocket Protocol & Server Message Definitions
- `scripts/` — Zero-mock test scripts and module audit
