# Makima v7.1 — System Architecture

*Generated via Phase 1 Codebase Analysis.*

## 1. Structure
Makima is a desktop AI assistant split into a Python brain, a native Rust overlay, and React UIs. The active production chat surface is `apps/chat_ui`; the native overlay is `apps/native_overlay`.
- `apps/brain/`: The core Python orchestrator, AI handler, agents (Commander, Browser), and native tools.
- `apps/chat_ui/`: The active React/Vite ChatGPT/Gemini-style chat UI with provider selection, multimodal composer, media cards, and local library.
- `apps/ui/`: Legacy Tauri shell retained only for compatibility/build history; it is not the active overlay runtime.
- `apps/native_overlay/`: Rust + Slint native Windows quick-command/voice overlay. It has no React or WebView dependency and connects directly to the local `/ws` endpoint.
- `configs/`: Centralized configuration (YAML) managing LLM backends, context budgets, thresholds, and module toggles.

## 2. Stack
- **Frontend**: React 19, TypeScript, Vite, React Router, Lucide-react.
- **Native quick surface**: Rust + Slint; native window, tray menu, global hotkeys, compact notification/reply bar, and expanded quick-chat view.
- **Backend (Brain)**: Python (Playwright for browser, gRPC for native services like audio/whisper/screen).
- **Design System**: Custom CSS (`design-system.css`) using 'Obsidian Violet' palette, glassmorphism, and raw CSS variables.

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
- **Fallback IPC**: `tauriEngine.ts` implements safe fallbacks for web-only mode (when `__TAURI_INTERNALS__` is absent), allowing UI dev without the Rust backend.
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

## 4.3 Shared Tool Runtime (2026-08-12)
- `ToolRegistry.call_tool()` now routes registered tools through `apps/brain/tools/runtime.py`, activating permission policy, schema filtering/validation, timeout, retry, and standardized failure handling while preserving legacy string results for agents.
- Tools marked `parallel_safe: false` are serialized per tool name at the registry boundary, protecting shared browser/stateful handlers even when callers request parallel dispatch.
- Agent calls provide the agent name as the runtime consumer, and disabled AI backends are skipped before any network call.
- `AppBootstrap` registers guardrails before the kernel, connects the health aggregator back to the orchestration engine, and publishes compatibility aliases (`router`, `learning`, `eternal_memory`) for legacy REST/WS handlers.
- Ollama model controls are exposed through `apps/brain/ollama_service.py`; unavailable local Ollama remains a degraded feature rather than a startup failure.

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

