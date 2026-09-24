# Project: Makima OS 11 Wiring & Architecture Remediation

## Architecture
Makima OS is an asynchronous multi-agent microkernel desktop operating system.
The target remediation resolves 11 architectural and subsystem wiring gaps across 5 milestones with zero regression across tool execution, SAGA safety, and memory persistence:
1. `apps/brain/core/sdk_bridge.py` & `configs/default.yaml` (Uncap Tool Budget to 0 & Relax Schema Cutoff to 250 chars)
2. `apps/brain/core/preference_engine.py` & `apps/brain/main.py` (Wire WebSocket FEEDBACK to PreferenceEngine & Clean ActionConfirmationManager)
3. `apps/brain/tools/reminder_tools.py`, `apps/brain/core/tool_loader.py`, & `apps/brain/main.py` (DurableTaskEngine SQLite-Backed Reminder Tools)
4. `apps/brain/personality.py`, `apps/brain/core/app_bootstrap.py`, & `apps/brain/core/orchestration_engine.py` (Wave 4 PersonalityEngine & Emotion WS Telemetry)
5. `apps/brain/tool_registry.py`, `apps/brain/tools/media_tools.py`, `apps/brain/proactive_orchestrator.py`, & Acceptance Testing (Tool Aliases, Focus Profiles & Full Regression Suite)

*Architectural Invariant*: Per Parent Sentinel directive (commit 00bc794), `ExecutionRuntime` guardrails remain `None`. All guardrail security is handled natively by OpenAI Agents SDK (`@input_guardrail dangerous_command_guard`, `InputGuardrailTripwireTriggered`) and domain tools (`system_tools.py:delete_file` root path protection).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Uncap SDK Tool Budget | `agent.tool_budget_tokens: 0` in `configs/default.yaml` & `sdk_bridge.py:446` fallback | M1 | Survey E1 |
| 2 | Relax Schema Compression Cutoff | `sdk_bridge.py:1571` description truncation relaxed from 50 to 250 chars | M1 | Survey E3 |
| 3 | Wire WebSocket FEEDBACK Loop | Wire `ClientMessageType.FEEDBACK` in `main.py:1662` to `preference_engine.record_feedback(approved=False)` | M2 | Survey E1 |
| 4 | Clean Action Confirmation Logic | Remove dangling `messaging` in `main.py:1550,1638,1658` and route via `ActionConfirmationManager` | M2 | Survey E1 |
| 5 | Standalone Reminder Tools | Create `apps/brain/tools/reminder_tools.py` (`set_reminder`, `list_reminders`, `cancel_reminder`) via `DurableTaskEngine` | M3 | Survey E2 |
| 6 | Tool Loader & Automation Endpoints | Register `register_reminder_tools` in `tool_loader.py` and wire `/api/automation/schedule` & `/api/automation/routines` | M3 | Survey E2 |
| 7 | PersonalityEngine Wave 4 Wiring | Instantiate in `app_bootstrap.py` Wave 4 (`S.PERSONALITY`) and inject into Wave 6 `S.ORCH_ENGINE` | M4 | Survey E2 |
| 8 | Dynamic Emotion Directives & WS | Inject `get_tone_directives()` into prompt `env_blocks` and emit `emotion_update` over WebSocket | M4 | Survey E2 |
| 9 | Media Tool Aliases | Add `register_alias` in `tool_registry.py` and register `play_media`, `play_music` in `media_tools.py` | M5 | Survey E3 |
| 10 | Focus Profile Propagation & Security | Add `set_focus_profile()` to `ProactiveOrchestrator` and wire `/api/security/audit` to real tools | M5 | Survey E3 |
| 11 | Acceptance Verification & Audit | Verify 120+ tools converted, undo stack preserved, and 100% test suite pass rate with forensic audit | M5 | Survey E3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | SDK Tool Budget & Schema Polish | `configs/default.yaml`, `apps/brain/core/sdk_bridge.py`, `tests/test_deep_capability_audit.py` | none | PLANNED |
| M2 | Feedback Loop & Confirmation Cleanup | `apps/brain/core/preference_engine.py`, `apps/brain/main.py`, `tests/test_ws_error_handling.py` | none | PLANNED |
| M3 | Durable Reminder Tools & Automation API | `apps/brain/tools/reminder_tools.py`, `apps/brain/core/tool_loader.py`, `apps/brain/main.py` | none | PLANNED |
| M4 | PersonalityEngine & Emotion Telemetry | `apps/brain/personality.py`, `apps/brain/core/service_registry.py`, `apps/brain/core/app_bootstrap.py`, `apps/brain/core/orchestration_engine.py`, `apps/brain/ws_protocol.py` | none | PLANNED |
| M5 | Aliases, Polish & Full Acceptance Gating | `apps/brain/tool_registry.py`, `apps/brain/tools/media_tools.py`, `apps/brain/proactive_orchestrator.py`, acceptance tests & forensic audit | M1, M2, M3, M4 | PLANNED |

## Interface Contracts
### `apps/brain/core/preference_engine.py` ↔ `apps/brain/main.py`
- `record_feedback(tool_name: str = "", rejected_params: Optional[dict] = None, accepted: bool = False, actual_params: Optional[dict] = None, approved: Optional[bool] = None, **kwargs) -> dict`
- When `approved is not None`, sets `accepted = approved`.

### `apps/brain/core/durable_task_engine.py` ↔ `apps/brain/tools/reminder_tools.py`
- `set_reminder(prompt: str, delay_seconds: int = 60, name: str = "reminder") -> dict`
- Backed by `DurableTaskEngine.checkpoint_task(resume_after_seconds=delay_seconds)` and `DurableTaskEngine.schedule_resume(task_id, delay_seconds)`.
- Returns `{"task_id": str, "status": "scheduled", "resume_after": float, "prompt": str}`.

### `apps/brain/personality.py` ↔ `apps/brain/core/orchestration_engine.py`
- `personality.get_tone_directives() -> str` returns emotional tone instructions for system prompt `env_blocks`.
- `personality.get_emotion_for_ws() -> dict` returns `{"emotion": str, "intensity": float, "valence": float, "arousal": float}`.

## Code Layout
- `configs/default.yaml` — System configuration (`agent.tool_budget_tokens: 0`)
- `apps/brain/core/sdk_bridge.py` — OpenAI Agents SDK bridge, `to_sdk_tools`, `_compress_tool_schema`
- `apps/brain/core/preference_engine.py` — Persistent user preference and feedback engine
- `apps/brain/core/confirmations.py` — Action confirmation lifecycle management
- `apps/brain/tools/reminder_tools.py` — Standalone reminder tools backed by SQLite DurableTaskEngine
- `apps/brain/core/tool_loader.py` — Dynamic domain tool loader
- `apps/brain/personality.py` — 13-emotion emotional state engine and tone directives
- `apps/brain/core/service_registry.py` — Dependency injection registry
- `apps/brain/core/app_bootstrap.py` — Wave-based lifecycle startup
- `apps/brain/core/orchestration_engine.py` — Agent orchestration, prompt assembly, and WS turn hooks
- `apps/brain/ws_protocol.py` — WebSocket message definitions (`EMOTION_UPDATE`)
- `apps/brain/tools/media_tools.py` — Media player tools (`media_play`, `play_media`, `play_music`)
- `apps/brain/proactive_orchestrator.py` — Autonomous proactive goal execution
- `apps/brain/main.py` — FastAPI server and WebSocket dispatcher
