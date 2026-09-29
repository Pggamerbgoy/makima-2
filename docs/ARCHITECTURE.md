# Makima v9.x — System Architecture

*Generated via Phase 1 Codebase Analysis.*

## 1. Structure
Makima is a desktop AI assistant split into a Python brain, a native Rust overlay, and React UIs. The active production chat surface is `apps/chat_ui`; the native overlay is `apps/native_overlay`.
- `apps/brain/`: The core Python orchestrator, AI handler, agents (Commander, Browser), and native tools.
- `apps/chat_ui/`: The active React/Vite ChatGPT/Gemini-style chat UI with provider selection, multimodal composer, media cards, and local library.
- `apps/native_overlay/`: Rust + Slint native Windows quick-command/voice overlay. It has no React or WebView dependency and connects directly to the local `/ws` endpoint.
- `configs/`: Centralized configuration (YAML) managing LLM backends, context budgets, thresholds, and module toggles.

## 2. Stack
- **Frontend**: React 19, TypeScript, Vite, React Router, Lucide-react.
- **Native quick surface**: Rust + Slint; native window, tray menu, global hotkeys, compact notification/reply bar, and expanded quick-chat view.
- **Backend (Brain)**: Python (Playwright for browser, gRPC for native services like audio/whisper/screen).
- **Design System**: Single `apps/chat_ui/src/index.css` using 'Obsidian Violet' palette, glassmorphism, and CSS custom properties.

## 3. Entry Points & Data Flow
1. **Startup**: The user launches the standalone Vite chat UI (`apps/chat_ui`) and optionally the native overlay (`apps/native_overlay`).
2. **Engine Boot**: The existing local launcher starts `python -m apps.brain.main`; the native overlay only attaches to the local service and does not own the backend process.
3. **UI Interaction**: The Chrome chat communicates with the Python brain through WebSockets (`/ws`) plus REST (`/media/*`, `/llm/*`, `/settings`) on port 8080. The native overlay uses the same WebSocket for lightweight text, voice-session controls, progress, streaming, and approvals.
4. **Overlay**: `Ctrl+Shift+O` (with `Alt+Space` fallback) toggles the native window. Background AI output wakes a compact one-line reply bar; explicit expand/hotkey reveals the full quick-chat surface. Approval requests expand automatically.

## 4. Conventions & Resilient Patterns
- **DIRECT-LLM-FIRST / Agent Conservation**: Commands that can be answered directly by the LLM (e.g. conversational questions, news summaries) route directly to fast_chat/general. Specialized background agents and modules remain inactive by default unless physical tool interaction is required.
- **EternalMemory WAL SQLite & Auto-Initialization**: SQLite runs in WAL mode (`PRAGMA journal_mode=WAL`) with a 30s busy timeout (`PRAGMA busy_timeout=30000`). Database reads use thread-safe short-lived connections in executors, while `save_turn` automatically ensures tables exist even before `start()` is invoked.
- **Audio Bleed Ducking Protection**: `SpeechOrchestrator` maintains an active media/TTS lock (`_media_playing` / `_tts_playing` via `set_media_playing`) that ducks and warns on microphone capture during background audio playback.
- **Win32 API Resilience**: `WindowManager` wraps `win32gui.EnumWindows` callbacks with explicit return codes (`return True`) and `try/except` guards to prevent Win32 Error 122 (`ERROR_INSUFFICIENT_BUFFER`) on Windows 11.
- **Web-only fallback**: The chat UI runs in a browser against the local brain over HTTP/WS; no Tauri IPC bridge is required for the active surface.
- **Process Management**: Rust uses `std::sync::Mutex` to track the Python child process, ensuring only one instance of the engine runs at a time.
- **Styling**: Utility classes combined with semantic component classes (`.btn`, `.card`, `.glass`) and native CSS custom properties.

## 4.1 Self-Learning Loop (v7.2+)
- `LearningEngine` (SQLite) stores behavior rules, style prefs, and user persona traits; `LearningCoordinator` extracts actionable rules from negative feedback, tool failures, and agent failures.
- `main.py` captures every incoming user turn for implicit trait extraction, records interaction for proactive suppression, wires LearningEngine/LearningCoordinator into `AgentOrchestrator`, all agents, and the router, and routes negative WS feedback into LearningCoordinator.
- `AgentOrchestrator._run_agent()` injects style prefs + matched behavior rules into agent context before execution, bumps rule hit counters after successful turns, and emits failure signals on timeout/guardrail/exception.
- `BaseAgent._use_tool()` reports repeated tool failures to LearningCoordinator, and `BaseAgent._build_messages()` injects style prefs, learned behavior rules, and persona context into prompts.
- Known gap: routing-correction, regeneration, and conversation-correction signal receivers exist in LearningCoordinator but are not yet emitted by CommandRouter heuristics.

## 4.2 Planner-first capability routing (v8.1+)
- Requests that do not match a cheap agent rule now go through a schema-validated private intent planner before semantic/fast-chat fallback. The planner selects an existing `Intent`/agent and returns only structured `entities`; its reasoning is never sent to the UI.
- Action-capable agents receive their filtered tool manifest and a repair turn is required if the model returns prose without invoking a tool. This prevents system/browser actions from becoming text-only hallucinations when the user uses natural phrasing such as “can you open Outlook?”.
- When an action route still cannot produce a tool call, the agent returns a concise `[Makima diagnostic]` message identifying the agent, available tool surface, and likely provider tool-calling issue. Internal chain-of-thought and secrets are never exposed.
- Windows app launch accepts dynamic registered app names through the OS shell without treating the legacy alias table as the application catalog; targets are restricted to safe simple names or existing paths.

## 4.3 Validated DAG Decomposition & Pre-flight Tool Validation (v8.0+)
- **`DecompositionEngine`** (`apps/brain/core/decomposition_engine.py`): Standalone validated DAG decomposition module that prevents sub-agents from dropping complex multi-step instructions or hallucinating tool parameters.
- **Pre-flight Validation Pipeline** (4 phases):
  1. **DAG Structural Integrity**: Detects cycles, dangling dependencies, and self-references. Topological sort validates ordering.
  2. **Tool Existence + Schema Compliance**: Every tool call is validated against the `ToolRegistry`. Parameters are checked against JSON Schema (type, required, enum, minLength/maxLength, minimum/maximum). Non-existent tools trigger auto-suggestions via fuzzy matching.
  3. **Agent Capability Matching**: Explicit agent assignments are validated against `AgentOrchestrator`. Missing agents are cleared for auto-routing.
  4. **Topological Sort**: Subtasks are ordered by dependencies (DAG execution order).
- **Strict vs. Non-strict Mode**: Strict mode marks any validation failure as `INVALID`. Non-strict mode (default) auto-corrects by stripping invalid tool calls and marking subtasks as `PARTIAL`.
- **Lightweight Schema Validator**: Custom recursive JSON Schema validator (`ToolSchemaValidator`) handles the subset of JSON Schema used by tool definitions (type, required, properties, items, enum, minLength/maxLength, minimum/maximum, pattern, anyOf/oneOf). Zero external dependencies.
- **Integration**: Consumed by `EliteCoordinator._decompose()`, `OrchestrationEngine`, or any future orchestrator via constructor injection. No circular dependencies.
- **Heuristic Fallback**: When LLM decomposition fails or times out, rule-based pattern matching decomposes common multi-step tasks (research → code, system control, browser automation, document creation, media playback, messaging).
- **Max Subtasks Cap**: Hard limit of 12 subtasks per decomposition to prevent runaway LLM output. Configurable via `max_subtasks` parameter.
- **Schema File**: `apps/brain/schemas/decomposition_schema.json` defines the canonical structure for LLM-generated decomposition output (subtask_id, instruction, agent_name, required_tools, dependencies, priority, required_capabilities).

## 4.4 Shared Tool Runtime (2026-08-12)
- `ToolRegistry.call_tool()` now routes registered tools through `apps/brain/tools/runtime.py`, activating permission policy, schema filtering/validation, timeout, retry, and standardized failure handling while preserving legacy string results for agents.
- Tools marked `parallel_safe: false` are serialized per tool name at the registry boundary, protecting shared browser/stateful handlers even when callers request parallel dispatch.
- Agent calls provide the agent name as the runtime consumer, and disabled AI backends are skipped before any network call.
- `AppBootstrap` registers guardrails before the kernel, connects the health aggregator back to the orchestration engine, and publishes compatibility aliases (`router`, `learning`, `eternal_memory`) for legacy REST/WS handlers.
- Ollama model controls are exposed through `apps/brain/ollama_service.py`; unavailable local Ollama remains a degraded feature rather than a startup failure.

## 4.5 Orchestration Engine & Streaming Lifecycle (v9.0+)
- **OpenAI Agents SDK Integration** (`apps/brain/core/orchestration_engine.py`): Primary asynchronous message dispatcher for Makima OS. Replaces monolithic routing loops with unified OpenAI Agents SDK execution (`make_unified_agent`, `Runner.run_streamed`), streaming delta tokens in real-time over WebSockets via `MakimaRunHooks`.
- **Concurrent Context Gathering**:
  - Independent environmental context blocks are fetched concurrently via `asyncio.gather(_fetch_fg_win(), _fetch_clip(), _fetch_rules(), _fetch_prefs())`, ensuring wall-clock latency is bounded by the slowest query rather than the sequential sum.
  - Active window titles, clipboard buffer history, relevant episodic memory rules, and user preference hints are injected alongside attached documents, screenshots, and live system capabilities from `CapabilityProbe`.
  - **Token-Bomb Truncation Guard**: Prompt payloads are hard-capped at 30,000 characters to prevent buffer-bloat and denial-of-service from massive clipboard pastes.
- **In-flight Deduplication & Cancellation**:
  - `_active_tasks` set prevents duplicate execution of double-clicked or retried WebSocket turns.
  - `cancel_task(task_id)` registers cancelled IDs, signals the underlying `asyncio.Task` handle, cleans up task manager handles, and broadcasts a `[Task cancelled by user]` terminal notice.
- **Out-of-Order Canvas Artifact Emission Fix**:
  - In `handle_message()`, substantial code blocks detected in the model output are converted into canvas artifacts using `_maybe_build_canvas_item()`.
  - **Emission Sequence Invariant**: The `canvas_item` WebSocket message is strictly emitted *before* the terminal `build_ai_chunk(..., is_final=True)` message.
  - *Rationale*: Frontend state machines (e.g. `useWebSocket`) finalize the streaming turn, close spinners, and unmount temporary stream buffers immediately upon receiving `is_final: True`. Emitting canvas artifacts after `is_final` caused race-condition dropped cards, missing code canvases, or layout flickers.
- **CRLF Windows Line-Ending Regex Compatibility**:
  - Code block extraction uses `re.findall(r"```([^\r\n]*)\r?\n(.*?)```", text, flags=re.DOTALL)` combined with `content.strip("\r\n")`.
  - *Rationale*: Standard Unix regex patterns (`[^\n]*\n`) capture trailing carriage returns (`\r`) on Windows systems into the language identifier string (e.g., `'python\r'`), which breaks syntax highlighters and markdown renderers in React. The CRLF-aware regex cleanly parses both Windows (`\r\n`) and Unix (`\n`) formats.

## 4.6 Proactive Orchestration & Graded Autonomy Engine (v9.x+)
- **Signal-Driven Autonomy** (`apps/brain/proactive_orchestrator.py`): Converts Makima from a purely reactive assistant into an autonomous desktop agent operating under risk-tiered boundaries and graded autonomy.
- **5-Tier Autonomy Hierarchy (`AutonomyLevel`)**:
  - `LEVEL_0_OBSERVATIONAL` (0): Passive telemetry and background audit logging only; zero UI intrusion.
  - `LEVEL_1_AMBIENT_CUE` (1): Non-intrusive status indicators or ambient cues.
  - `LEVEL_2_SUGGESTION_CARD` (2, Default): Interactive UI proposal cards requiring explicit user confirmation.
  - `LEVEL_3_REVERSIBLE_AUTO` (3): Direct autonomous execution for low-risk, fully reversible actions.
  - `LEVEL_4_FULL_AUTONOMOUS` (4): Full autonomous execution for routine proven habits.
- **Modes & Persistence**:
  - Supported modes: `"off"`, `"suggest"`, `"auto"`. Mode transitions persist to `configs/proactive_state.json` across process restarts.
  - Hard safety classifier: Deterministic keyword regex (`_DANGEROUS_PATTERNS`) classifies destructive commands (app termination, file deletion, reboot/shutdown, external messaging, payment/credential operations) as "dangerous", unconditionally requiring confirmation cards regardless of LLM confidence.
- **Decoupled Dual-Loop Architecture**:
  - **Fast Durable Task Loop (`_durable_loop`)**: Runs on a tight 2.5-second polling interval (`asyncio.sleep(2.5)`). Dedicated exclusively to querying `DurableTaskEngine` for scheduled tasks and user reminders whose `resume_after <= now`.
  - **Ambient Habit & Situational Loop (`_loop`)**: Runs on a 300-second (5-minute) interval (`interval_s: 300`). Evaluates causal triggers including critical battery levels (<=18% unplugged), cognitive overload (rapid app switching/thrashing), and terminal/IDE build errors.
  - *Architectural Rationale*: Decoupling scheduled task polling from the 300s ambient evaluation cycle eliminates multi-minute resumption lag, ensuring reminders and alarms fire with second-level precision while keeping ambient LLM evaluations lightweight.
- **Safe `reconstructed_context` NoneType Fallback**:
  - In `_check_durable_task_resumptions()`, task context retrieval is guarded:
    ```python
    rctx = resumed.reconstructed_context if isinstance(getattr(resumed, "reconstructed_context", None), dict) else {}
    ```
  - *Rationale*: Tasks recovered from SQLite event stores with missing, corrupted, or null context previously triggered fatal `AttributeError: 'NoneType' object has no attribute 'get'` crashes. Safe dictionary defaulting protects recurring task re-arming (`repeat_interval_s >= 60`) and clean one-shot completion marking (`mark_completed`).
- **Focus Profile Integration (`SET_FOCUS_PROFILE` / `FOCUS_CHANGE`)**:
  - Ingress WebSocket messages (`set_focus_profile`, `focus_change`) in `apps/brain/main.py` query `FocusProfiles` to compute profile toggles and invoke `ProactiveOrchestrator.set_focus_profile(profile_name, toggles)`.
  - Polymorphic interface accepts `(profile_name, toggles)`, single string profile names (`"quiet"`, `"work"`), or full toggle dictionaries.
  - Restricted profiles (`quiet`, `meeting`, `gaming`) automatically set `proactive_suggestions_enabled: False`, muting ambient habit triggers and proactive suggestion cards during meetings or gaming sessions.
  - *Resumption Invariant*: Scheduled durable task resumptions (`_check_durable_task_resumptions`) continue executing even during quiet hours or when focus profiles disable ambient suggestions, ensuring user-defined alarms and reminders are never missed.

## 5. Risk Zones
- The Tauri Rust backend forcefully kills the Python process (`child.kill()`) on `stop_engine`. However, EternalMemory mitigates corruption via WAL mode and automatic `flush()` checkpointing.
- `is_engine_running` only checks if the child process has exited; it doesn't verify if the Python REST/WS server is actually healthy and accepting connections.
- CAPTCHA challenges during browser automation are actively intercepted and break the retry loop immediately to prompt manual user resolution.

## 5.1 Multimodal media pipeline (2026-08-12)
- `MediaStore` (`apps/brain/media_store.py`) stores generated-id files and an atomically replaced manifest below `~/.makima/media/`. Images/documents/audio are capped at 20 MB; videos at 100 MB. Only generated ids are accepted by downstream APIs.
- REST endpoints expose upload, library listing, metadata, inline content, download, and delete. The chat WebSocket receives only `{id, kind, mime_type}` attachment references; the legacy single-file base64 payload remains a compatibility path.
- `MultimodalService` resolves media references into provider-safe model content. Images use inline image content; text documents are bounded and injected as text; Gemini audio/video uses the Gemini interaction API (inline for small files and Files API processing for larger files).
- Provider catalog metadata includes explicit text/image/audio/video capabilities. Unsupported media produces a visible `media_capability_unavailable` error instead of silently routing to a text-only provider.
- `apps/chat_ui` renders `MediaCard`, `MediaLibraryPanel`, `AgentActivityTimeline`, action approval cards, strict Mermaid output, and task-scoped streaming state. Structured media is preferred while Markdown/YouTube fallback remains supported.

## 6. Native Overlay & Interactive UI Architecture
- **Interactive Action Confirmation Protocol**: When destructive actions are triggered by background agents (`action_confirm_request`), the native overlay expands its approval card and sends `approve_action` / `reject_action` using the original server `task_id` and action. The full Chrome chat continues to render its own approval UI.
- **Live Agent Activity & Thinking Status**: The native overlay status line reflects real-time background agent states (`agent_started`, `agent_progress`, `agent_done`, `thinking_status`). Normal output wakes only the compact reply bar so it does not cover the desktop.
- **Whisper Voice STT Preview**: When Push-to-Talk (`ptt_down` / `ptt_up`) captures speech, the UI renders `stt_transcript_preview` allowing the user to confirm (`stt_confirm`) or cancel (`stt_cancel`) transcriptions before submission.
- **Overlay Window Ergonomics**: `apps/native_overlay` provides an always-on-top native window, tray actions, inline compact reply input, separate expand control, recent history, Chrome handoff, and compact/expanded hotkey behavior. Full media/library/canvas surfaces remain in `apps/chat_ui`.

