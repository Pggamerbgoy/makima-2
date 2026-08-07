# Makima v7.1 — System Architecture

*Generated via Phase 1 Codebase Analysis.*

## 1. Structure
Makima is a desktop AI assistant split into a Python brain, a Rust (Tauri) native shell, and a React UI.
- `apps/brain/`: The core Python orchestrator, AI handler, agents (Commander, Browser), and native tools.
- `apps/ui/`: The frontend React (Vite) application providing the user interface.
- `apps/ui/src-tauri/`: The Tauri Rust backend bridging the React UI with native OS features (global hotkeys, window management) and spawning the Python brain process.
- `configs/`: Centralized configuration (YAML) managing LLM backends, context budgets, thresholds, and module toggles.

## 2. Stack
- **Frontend**: React 19, TypeScript, Vite, React Router, Lucide-react.
- **Backend (UI Shell)**: Tauri v2, Rust.
- **Backend (Brain)**: Python (Playwright for browser, gRPC for native services like audio/whisper/screen).
- **Design System**: Custom CSS (`design-system.css`) using 'Obsidian Violet' palette, glassmorphism, and raw CSS variables.

## 3. Entry Points & Data Flow
1. **Startup**: The user launches the Tauri app (`apps/ui/src-tauri/src/lib.rs`).
2. **Engine Boot**: The Tauri backend exposes a `start_engine` IPC command. When called by the UI (via `tauriEngine.ts`), Tauri spawns `python -m apps.brain.main` as a child process.
3. **UI Interaction**: The React app communicates with the Tauri shell via IPC (`@tauri-apps/api/core`) for window states, and likely communicates with the Python brain via WebSockets (port 8765) or REST (port 8080) as defined in `default.yaml`.
4. **Overlay**: A global shortcut (`Alt+Space`) is registered in Rust to instantly center and focus the Tauri webview window.

## 4. Conventions & Resilient Patterns
- **DIRECT-LLM-FIRST / Agent Conservation**: Commands that can be answered directly by the LLM (e.g. conversational questions, news summaries) route directly to fast_chat/general. Specialized background agents and modules remain inactive by default unless physical tool interaction is required.
- **EternalMemory WAL SQLite & Auto-Initialization**: SQLite runs in WAL mode (`PRAGMA journal_mode=WAL`) with a 30s busy timeout (`PRAGMA busy_timeout=30000`). Database reads use thread-safe short-lived connections in executors, while `save_turn` automatically ensures tables exist even before `start()` is invoked.
- **Audio Bleed Ducking Protection**: `SpeechOrchestrator` maintains an active media/TTS lock (`_media_playing` / `_tts_playing` via `set_media_playing`) that ducks and warns on microphone capture during background audio playback.
- **Win32 API Resilience**: `WindowManager` wraps `win32gui.EnumWindows` callbacks with explicit return codes (`return True`) and `try/except` guards to prevent Win32 Error 122 (`ERROR_INSUFFICIENT_BUFFER`) on Windows 11.
- **Fallback IPC**: `tauriEngine.ts` implements safe fallbacks for web-only mode (when `__TAURI_INTERNALS__` is absent), allowing UI dev without the Rust backend.
- **Process Management**: Rust uses `std::sync::Mutex` to track the Python child process, ensuring only one instance of the engine runs at a time.
- **Styling**: Utility classes combined with semantic component classes (`.btn`, `.card`, `.glass`) and native CSS custom properties.

## 5. Risk Zones
- The Tauri Rust backend forcefully kills the Python process (`child.kill()`) on `stop_engine`. However, EternalMemory mitigates corruption via WAL mode and automatic `flush()` checkpointing.
- `is_engine_running` only checks if the child process has exited; it doesn't verify if the Python REST/WS server is actually healthy and accepting connections.
- CAPTCHA challenges during browser automation are actively intercepted and break the retry loop immediately to prompt manual user resolution.

## 6. Window Overlay & Interactive UI Architecture
- **Interactive Action Confirmation Protocol**: When destructive actions are triggered by background agents (`action_confirm_request`), BOTH `OverlayPage.tsx` and `ChatPage.tsx` render an interactive Frosted Glass Modal (`.overlay-confirm-card`). User approvals and rejections are routed via `useMakimaWebSocket` (`approve_action` / `reject_action`) matching the backend `task_id`.
- **Live Agent Activity & Thinking Status**: The overlay titlebar and status pill (`.overlay-status-pill`) reflect real-time background agent states (`agent_started`, `agent_progress`, `agent_done`, `thinking_status`), eliminating perceived freezes during multi-step tasks.
- **Whisper Voice STT Preview**: When Push-to-Talk (`ptt_down` / `ptt_up`) captures speech, the UI renders `stt_transcript_preview` allowing the user to confirm (`stt_confirm`) or cancel (`stt_cancel`) transcriptions before submission.
- **Overlay Window Ergonomics**: `OverlayPage` features an Always-On-Top pin toggle (`togglePin`), an Open in Main App handoff button (`openInMainWindow`), full 50-turn scrollable history, multiline textarea support (`Shift+Enter`), and safe `Escape` handling (clears text before hiding window).

