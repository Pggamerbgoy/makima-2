# Progress — Makima v7.1

## Core Modules & Status

- [x] **Command Router & Intent Classification (`command_router.py`)**: Verified (Pass)
- [x] **Browser Agent & Playwright Controller (`browser_agent.py`)**: Verified (Pass)
- [x] **Media & Audio Player Agent (`media_agent.py`)**: Verified (Pass)
- [x] **Commander Agent & Task Planner (`commander_agent.py`)**: Verified (Pass)
- [x] **Voice & Speech Orchestrator (`speech_orchestrator.py`)**: Verified (Pass)
- [x] **Automation Agent & Reminders (`automation_agent.py`)**: Verified (Pass)
- [x] **Memory Agent & Knowledge Graph (`memory_agent.py`)**: Verified (Pass)

## Identified Architecture Improvements
- [x] Self-learning loop wired: LearningCoordinator/LearningEngine connected to BaseAgent tool failures, AgentOrchestrator turn context/failure signals, and main.py user-turn capture.
- [x] Multi-tab page isolation between `MediaAgent` (`tab="media"`) and `BrowserAgent` (`tab="browser"`) implemented in `BrowserController`.
- [x] Audio contamination ducking check added to `SpeechOrchestrator` (`_media_playing` / `_tts_playing`).
- [x] SQLite thread/task locking eliminated in `EternalMemory` (`timeout=30.0` & `PRAGMA busy_timeout=30000;`).
- [x] Parallel subtask burst debounce added to `CircuitBreaker.record_failure()` in `AIHandler`.
- [ ] Normalize Gemini Vision click coordinates relative to viewport size in `BrowserController._vision_click`.
- [ ] Intercept `RuntimeError` from CAPTCHA detection in `BrowserAgent.execute` to break agent loop instantly.
