"""
Makima OS — VoiceEngine (Clean Rebuild)
Location: apps/brain/voice/engine.py

Full-duplex Gemini Multimodal Live voice session with:
  - Dynamic tool declarations from ToolRegistry (not a hardcoded list)
  - All tasks routed to OrchestrationEngine.handle_message()
  - Per-session state management (pause / resume / barge-in)
  - Native spoken confirmation handling via voice_confirm tool
  - WakeDaemon integration for always-on wake word
  - Wake-word→session handoff (native audio → Gemini Live)

Public API (duck-type compatible with main.py voice handlers):
    start_voice_session(voice_session_id, conversation_id, settings)
    handle_voice_utterance(voice_session_id, task_id, audio_b64, sample_rate)
    pause_voice_session(voice_session_id)
    resume_voice_session(voice_session_id)
    stop_voice_session(voice_session_id)
    set_wake_word_enabled(enabled: bool)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("makima.voice.engine")

# ── Max tools surfaced to Gemini per session (keep under API limit) ───────────
_MAX_VOICE_TOOLS = 30

# ── Universal tool that lets Gemini delegate anything to the Orchestrator ─────
_DELEGATE_TOOL_DECL = {
    "name": "execute_task",
    "description": (
        "Execute any task or command through Makima's full agent swarm. "
        "Use this for media control, browser, code, messaging, research, system ops, "
        "reminders, timers — anything the user asks that doesn't have a more specific tool."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {
                "type": "STRING",
                "description": "Natural language description of what to do",
            }
        },
        "required": ["task"],
    },
}

_CONFIRM_TOOL_DECL = {
    "name": "voice_confirm",
    "description": (
        "Resolve a pending action confirmation. Use when the user has verbally approved "
        "or rejected an action that requires their consent."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "approved": {
                "type": "BOOLEAN",
                "description": "True if the user approved, False if they rejected",
            },
            "task_id": {
                "type": "STRING",
                "description": "The task_id of the pending confirmation, if known",
            },
        },
        "required": ["approved"],
    },
}


@dataclass
class VoiceSession:
    """State for a single active voice session."""
    voice_session_id: str
    conversation_id: str
    settings: Dict[str, Any] = field(default_factory=dict)
    paused: bool = False
    started_at: float = field(default_factory=time.monotonic)

    # Gemini Live internal
    _client: Any = field(default=None, repr=False)
    _session: Any = field(default=None, repr=False)
    _session_cm: Any = field(default=None, repr=False)
    _receive_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _active: bool = field(default=True, repr=False)


class VoiceEngine:
    """
    Production voice engine for Makima OS.

    Coordinates:
      - Gemini Multimodal Live API (bidirectional audio)
      - ToolRegistry → dynamic Gemini tool declarations
      - OrchestrationEngine → all task execution
      - Gemini Live voice_confirm → spoken confirmations
      - WakeDaemon → always-on wake phrase detection
    """

    SYSTEM_PROMPT = (
        "You are Makima — an elite, intelligent, charming personal AI companion running on the user's PC. "
        "You operate entirely through voice — no screen, no keyboard. "
        "Speak naturally in Hinglish (blended Hindi + English) or pure English, matching the user's language. "
        "Keep all responses concise and conversational since you are speaking aloud in real-time. "
        "When the user gives you any command — play music, open an app, search the web, "
        "write code, send a message, set a timer, control the computer — "
        "immediately call the appropriate tool without asking for confirmation unless "
        "the action is irreversible (e.g. deleting files, sending messages to contacts). "
        "For irreversible actions, briefly describe what you are about to do and ask "
        "'Shall I proceed? Say haan to confirm or cancel to stop.' then call voice_confirm. "
        "Never say 'I cannot do that' — if a specific tool is not available, use execute_task."
    )

    def __init__(
        self,
        api_key: str,
        ws_broadcast: Any,
        command_router: Any,          # OrchestrationEngine instance
        tool_registry: Any,           # ToolRegistry instance
        model: str = "gemini-3.1-flash-live-preview",
        voice_name: str = "Aoede",
        voice_config: Any = None,
    ) -> None:
        self.api_key = api_key
        self.ws_broadcast = ws_broadcast
        self.command_router = command_router
        self.tool_registry = tool_registry
        self.model = (
            getattr(voice_config, "gemini_model", None)
            or model
            or "gemini-3.1-flash-live-preview"
        )
        self.voice_name = voice_name
        self._voice_config = voice_config

        # Sessions map: voice_session_id → VoiceSession
        self._sessions: Dict[str, VoiceSession] = {}
        self._sessions_lock = asyncio.Lock()

        # Wake daemon (optional, may be None if disabled in config)
        self._wake_daemon: Any = None
        self._wake_enabled = True

        # Gemini client (lazy-init to avoid import errors at startup)
        self._client: Any = None

        # Tracked background tasks to prevent GC reclamation
        self._background_tasks: set[asyncio.Task] = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Called by AppBootstrap lifecycle hooks — start WakeDaemon."""
        await self._start_wake_daemon()

    async def stop(self) -> None:
        """Called by AppBootstrap shutdown — stop all sessions and WakeDaemon."""
        async with self._sessions_lock:
            session_ids = list(self._sessions.keys())
        for sid in session_ids:
            await self.stop_voice_session(sid)
        if self._wake_daemon:
            await self._wake_daemon.stop()
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        self._background_tasks.clear()

    # ── WakeDaemon integration ────────────────────────────────────────────────

    async def _start_wake_daemon(self) -> None:
        if not self._wake_enabled:
            return
        cfg = self._voice_config
        if cfg and not cfg.wake_daemon.enabled:
            logger.info("WakeDaemon disabled in config — skipping")
            return
        try:
            from .wake import WakeDaemon
            phrases = cfg.wake_phrases if cfg else ["hey makima", "makima"]
            threshold = cfg.wake_daemon.threshold if cfg else 0.6
            refractory_s = cfg.wake_daemon.refractory_s if cfg else 2.0
            sample_rate = cfg.vad.sample_rate if cfg else 16000
            device_index = cfg.wake_daemon.device_index if cfg else None

            self._wake_daemon = WakeDaemon(
                wake_phrases=phrases,
                threshold=threshold,
                refractory_s=refractory_s,
                sample_rate=sample_rate,
                device_index=device_index,
                on_wake_callback=self._on_wake_detected,
            )
            await self._wake_daemon.start()
        except Exception as e:
            logger.warning("WakeDaemon could not start: %s", e)

    async def _on_wake_detected(self) -> None:
        """
        Called by WakeDaemon when a wake phrase is heard.
        Starts a new voice session automatically if none is active.
        """
        async with self._sessions_lock:
            active = bool(self._sessions)
        if active:
            logger.debug("Wake detected but a session is already active — ignoring")
            return

        session_id = f"wake_{uuid.uuid4().hex[:12]}"
        logger.info("Wake word triggered → auto-starting voice session %s", session_id)
        await self.start_voice_session(session_id, conversation_id=session_id, settings={})

        # Notify UI
        if self.ws_broadcast:
            try:
                from ..ws_protocol import build_voice_event
                await self.ws_broadcast(build_voice_event(
                    "voice_session_state", session_id,
                    state="listening", requires_wake_word=False,
                    auto_started=True,
                ))
            except Exception:
                pass

    async def set_wake_word_enabled(self, enabled: bool) -> None:
        self._wake_enabled = enabled
        if enabled and not self._wake_daemon:
            await self._start_wake_daemon()
        elif not enabled and self._wake_daemon:
            await self._wake_daemon.stop()
            self._wake_daemon = None
        logger.info("Wake word enabled: %s", enabled)

    # ── Session management ────────────────────────────────────────────────────

    @staticmethod
    def _valid_session_id(value: str) -> bool:
        import re
        return bool(value) and bool(re.match(r"^[\w\-]{4,128}$", value))

    def _get_client(self, api_key: str = "") -> Any:
        effective_key = api_key or self.api_key
        if self._client is None or (api_key and api_key != getattr(self, "_active_client_key", None)):
            from google import genai
            self._client = genai.Client(
                api_key=effective_key,
                http_options={"api_version": "v1beta"},
            )
            self._active_client_key = effective_key
        return self._client

    async def start_voice_session(
        self,
        voice_session_id: str,
        conversation_id: str = "",
        settings: Dict[str, Any] = None,
    ) -> None:
        if not self._valid_session_id(voice_session_id):
            logger.warning("Rejected malformed voice session id: %s", voice_session_id)
            return

        # Stop stale session with same id
        async with self._sessions_lock:
            old = self._sessions.pop(voice_session_id, None)
        if old:
            await self._close_session(old)

        session = VoiceSession(
            voice_session_id=voice_session_id,
            conversation_id=conversation_id or voice_session_id,
            settings=settings or {},
        )
        async with self._sessions_lock:
            self._sessions[voice_session_id] = session

        # Connect Gemini Live in background
        task = asyncio.create_task(
            self._connect_and_stream(session),
            name=f"voice_session_{voice_session_id}",
        )
        session._receive_task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        logger.info("Voice session started: %s (conv=%s)", voice_session_id, session.conversation_id)

    async def handle_voice_utterance(
        self,
        voice_session_id: str,
        task_id: str,
        audio_b64: str,
        sample_rate: int = 16000,
    ) -> None:
        session = self._sessions.get(voice_session_id)
        if not session or session.paused or not session._session:
            return
        try:
            pcm_bytes = base64.b64decode(audio_b64)
            from google.genai import types
            await session._session.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
            )
        except Exception as e:
            logger.debug("Error forwarding audio to Gemini Live: %s", e)

    async def pause_voice_session(self, voice_session_id: str) -> None:
        session = self._sessions.get(voice_session_id)
        if session:
            session.paused = True
            logger.info("Voice session paused: %s", voice_session_id)

    async def resume_voice_session(self, voice_session_id: str) -> None:
        session = self._sessions.get(voice_session_id)
        if session:
            session.paused = False
            logger.info("Voice session resumed: %s", voice_session_id)

    async def stop_voice_session(self, voice_session_id: str) -> None:
        async with self._sessions_lock:
            session = self._sessions.pop(voice_session_id, None)
        if session:
            await self._close_session(session)
            logger.info("Voice session stopped: %s", voice_session_id)

    async def barge_in(self, voice_session_id: str) -> None:
        """Interrupt current TTS playback — signals to Gemini Live."""
        session = self._sessions.get(voice_session_id)
        if session and session._session:
            try:
                # The Live API handles barge-in by sending new audio — just notify UI
                if self.ws_broadcast:
                    from ..ws_protocol import build_voice_event
                    await self.ws_broadcast(build_voice_event(
                        "voice_tts_stopped", voice_session_id, reason="barge_in",
                    ))
            except Exception:
                pass

    async def synthesize_session_tts(
        self, voice_session_id: str, text: str, task_id: str = ""
    ) -> None:
        """Send text to Gemini Live for TTS synthesis within the session."""
        session = self._sessions.get(voice_session_id)
        if session and session._session:
            try:
                from google.genai import types
                await session._session.send_client_content(
                    turns=[types.Content(role="user", parts=[types.Part(text=f"[speak] {text}")])],
                    turn_complete=True,
                )
            except Exception as e:
                logger.debug("TTS synthesis error: %s", e)

    # ── Legacy Bridge & Single-Session Duck-Type Compatibility ───────────────

    @property
    def is_active(self) -> bool:
        """True if any voice session is currently active."""
        return bool(self._sessions)

    async def start_session(
        self,
        session_id: str = "default",
        system_prompt: Optional[str] = None,
    ) -> bool:
        """Single-session starter for legacy callers (e.g. main.py / GeminiLiveBridge)."""
        await self.start_voice_session(session_id, conversation_id=session_id)
        return True

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send raw PCM bytes to the latest active voice session."""
        async with self._sessions_lock:
            sessions = list(self._sessions.values())
        if not sessions:
            return
        session = sessions[-1]
        if session.paused or not session._session:
            return
        try:
            from google.genai import types
            await session._session.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
            )
        except Exception as e:
            logger.debug("Error forwarding audio to Gemini Live: %s", e)

    async def stop_session(self) -> None:
        """Stop all active voice sessions."""
        async with self._sessions_lock:
            session_ids = list(self._sessions.keys())
        for sid in session_ids:
            await self.stop_voice_session(sid)

    async def handle_ptt_down(self) -> None:
        """No-op stub for push-to-talk key down."""
        pass

    async def handle_ptt_up(self, audio_bytes: bytes) -> None:
        """Push-to-talk key release: forward recorded audio bytes if present."""
        if audio_bytes:
            await self.send_audio(audio_bytes)

    # ── Gemini Live connection ────────────────────────────────────────────────

    def _build_tools(self) -> List[Any]:
        """Build Gemini FunctionDeclaration list dynamically from ToolRegistry."""
        from google.genai import types

        declarations: List[Any] = []

        # 1. Populate from ToolRegistry — prioritize essential daily OS & media tools first
        if self.tool_registry and hasattr(self.tool_registry, "list_tools"):
            all_tools = self.tool_registry.list_tools()
            priority_names = {
                "launch_app", "system_launch_app",
                "media_play", "media_pause", "media_resume", "media_next", "media_previous", "media_set_volume",
                "web_search", "take_screenshot", "system_take_screenshot",
                "get_system_stats", "system_get_system_stats",
                "set_volume", "system_set_volume",
                "show_notification", "system_show_notification",
                "clean_temp_files", "system_clean_temp_files",
                "set_reminder", "list_reminders",
                "browser_navigate",
            }
            sorted_tools = sorted(all_tools, key=lambda t: 0 if t.name in priority_names else 1)
            for tool_meta in sorted_tools[:_MAX_VOICE_TOOLS - 2]:  # -2 for execute_task + voice_confirm
                try:
                    schema = dict(tool_meta.schema) if tool_meta.schema else {}
                    if "type" in schema:
                        schema["type"] = str(schema["type"]).upper()
                    declarations.append(
                        types.FunctionDeclaration(
                            name=tool_meta.name,
                            description=tool_meta.description or f"Execute {tool_meta.name}",
                            parameters=schema if schema else None,
                        )
                    )
                except Exception as te:
                    logger.debug("Skipping tool %s: %s", tool_meta.name, te)

        # 2. Always include the universal delegator (catches anything not covered above)
        declarations.append(
            types.FunctionDeclaration(
                name=_DELEGATE_TOOL_DECL["name"],
                description=_DELEGATE_TOOL_DECL["description"],
                parameters=_DELEGATE_TOOL_DECL["parameters"],
            )
        )

        # 3. Spoken confirmation tool
        declarations.append(
            types.FunctionDeclaration(
                name=_CONFIRM_TOOL_DECL["name"],
                description=_CONFIRM_TOOL_DECL["description"],
                parameters=_CONFIRM_TOOL_DECL["parameters"],
            )
        )

        return [types.Tool(function_declarations=declarations)]

    async def _connect_and_stream(self, session: VoiceSession) -> None:
        """Connect to Gemini Live and stream responses back to the UI."""
        from google.genai import types
        from ..ai_handler import is_valid_api_key

        session_id = session.voice_session_id
        effective_key = (
            session.settings.get("api_key")
            or session.settings.get("client_api_key")
            or self.api_key
        )

        if not is_valid_api_key(effective_key):
            logger.warning("[VoiceEngine] No valid Gemini API key found for session %s", session_id)
            if self.ws_broadcast:
                from ..ws_protocol import build_voice_event
                await self.ws_broadcast(build_voice_event(
                    "voice_error", session_id,
                    message="Gemini Live requires a valid Gemini API key. Configure GEMINI_API_KEY in Settings → Models or use browser voice.",
                ))
            return

        try:
            client = self._get_client(api_key=effective_key)
        except Exception as ce:
            logger.error("Failed to initialize GenAI client: %s", ce)
            if self.ws_broadcast:
                from ..ws_protocol import build_voice_event
                await self.ws_broadcast(build_voice_event(
                    "voice_error", session_id,
                    message=f"Gemini client initialization failed: {ce}",
                ))
            return

        tools = self._build_tools()
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(parts=[types.Part(text=self.SYSTEM_PROMPT)]),
            tools=tools,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice_name
                    )
                )
            ),
        )

        try:
            session_cm = client.aio.live.connect(model=self.model, config=config)
            gemini_session = await session_cm.__aenter__()
            session._session = gemini_session
            session._session_cm = session_cm
        except Exception as e:
            logger.error("Failed to connect to Gemini Live: %s", e)
            if self.ws_broadcast:
                from ..ws_protocol import build_voice_event
                await self.ws_broadcast(build_voice_event(
                    "voice_error", session_id, message=f"Could not connect to Gemini Live: {e}",
                ))
            return

        logger.info("Gemini Live session connected: %s (model=%s)", session_id, self.model)
        if self.ws_broadcast:
            from ..ws_protocol import build_voice_event
            await self.ws_broadcast(build_voice_event(
                "voice_session_state", session_id,
                state="listening",
                requires_wake_word=False,
            ))

        try:
            await self._receive_loop(session)
        finally:
            await self._close_session(session)

    async def _receive_loop(self, session: VoiceSession) -> None:
        """Stream Gemini Live responses and dispatch tool calls."""
        session_id = session.voice_session_id
        gemini_session = session._session

        while session._active and gemini_session:
            try:
                async for response in gemini_session.receive():
                    if not session._active:
                        break

                    # 1. Barge-in / interruption
                    if (
                        response.server_content
                        and getattr(response.server_content, "interrupted", False)
                    ):
                        logger.info("Gemini Live barge-in for session %s", session_id)
                        if self.ws_broadcast:
                            from ..ws_protocol import build_voice_event
                            await self.ws_broadcast(build_voice_event(
                                "voice_tts_stopped", session_id, reason="barge_in",
                            ))

                    # 2. Audio from model
                    audio_bytes = response.data
                    if not audio_bytes and response.server_content and response.server_content.model_turn:
                        for part in response.server_content.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                audio_bytes = part.inline_data.data
                                break

                    if audio_bytes and self.ws_broadcast:
                        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                        from ..ws_protocol import build_voice_event
                        await self.ws_broadcast(build_voice_event(
                            "voice_tts_audio", session_id,
                            audio=audio_b64,
                            mime_type="audio/pcm;rate=24000",
                        ))

                    # 3. Text transcripts — also feed to approval gate
                    text_content = response.text
                    if not text_content and response.server_content and response.server_content.model_turn:
                        text_parts = [
                            p.text for p in response.server_content.model_turn.parts
                            if p.text and not getattr(p, "thought", False)
                        ]
                        if text_parts:
                            text_content = " ".join(text_parts).strip()

                    if text_content and self.ws_broadcast:
                        from ..ws_protocol import build_voice_event
                        await self.ws_broadcast(build_voice_event(
                            "voice_transcript_partial", session_id,
                            text=text_content, role="assistant",
                        ))

                    # 4. Tool calls
                    if response.tool_call:
                        await self._handle_tool_calls(response.tool_call, session)

                    # 5. Turn complete notification (for native overlay and chat UI)
                    if (
                        response.server_content
                        and getattr(response.server_content, "turn_complete", False)
                    ):
                        if self.ws_broadcast:
                            from ..ws_protocol import build_voice_event
                            await self.ws_broadcast(build_voice_event(
                                "voice_transcript_final", session_id,
                                text=text_content or "", role="assistant",
                            ))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not session._active:
                    break
                logger.debug("Receive loop iteration error for %s: %s", session_id, exc)
                await asyncio.sleep(0.05)

        logger.info("Gemini Live receive loop ended for session %s", session_id)

    async def _handle_tool_calls(self, tool_call: Any, session: VoiceSession) -> None:
        """Dispatch Gemini Live tool calls → ToolRegistry or OrchestrationEngine."""
        from google.genai import types

        session_id = session.voice_session_id
        function_responses: List[types.FunctionResponse] = []

        for fc in getattr(tool_call, "function_calls", []):
            call_id: str = fc.id
            call_name: str = fc.name
            call_args: Dict[str, Any] = fc.args or {}
            if isinstance(call_args, str):
                try:
                    call_args = json.loads(call_args)
                except Exception:
                    call_args = {}

            logger.info(
                "[VoiceEngine] Tool call: %s(args=%s, session=%s)",
                call_name, call_args, session_id,
            )

            result_str = ""
            task_id = f"voice_{session_id}_{call_id}"

            # Normalize common tool parameters across LLM hallucinations
            if call_name in ("launch_app", "system_launch_app"):
                if "app_path" not in call_args:
                    target = call_args.get("app_name") or call_args.get("name") or call_args.get("app") or call_args.get("target")
                    if target:
                        for word in ["please", "karo", "kholo", "fast", "app", "now"]:
                            target = target.replace(word, "").strip()
                        call_args["app_path"] = target

            try:
                # ── A. Spoken confirmation tool ────────────────────────────
                if call_name == "voice_confirm":
                    approved = bool(call_args.get("approved", False))
                    confirm_task_id = call_args.get("task_id", task_id)
                    try:
                        from ..agents.base_agent import BaseAgent
                        await BaseAgent.resolve_confirmation(confirm_task_id, approved)
                        result_str = f"Confirmation resolved: {'approved' if approved else 'rejected'}"
                    except Exception as e:
                        result_str = f"Confirmation resolve error: {e}"

                # ── B. Universal delegator → OrchestrationEngine ───────────
                elif call_name == "execute_task":
                    task_text = str(call_args.get("task", "")).strip()
                    lower_task = task_text.lower()
                    # Direct OS action fast-path (zero LLM latency, zero cloud dependency)
                    if (lower_task.startswith("open ") or lower_task.startswith("launch ")) and self.tool_registry:
                        target_app = task_text.split(maxsplit=1)[1].strip().rstrip(".!\"'")
                        for word in ["please", "karo", "kholo", "fast", "app", "now"]:
                            target_app = target_app.replace(word, "").strip()
                        try:
                            result_str = await self.tool_registry.execute_tool("launch_app", {"app_path": target_app})
                            logger.info("[VoiceEngine] Fast-path launched app '%s': %s", target_app, result_str)
                        except Exception as ae:
                            result_str = f"Application launch error: {ae}"
                    elif task_text and self.command_router:
                        t = asyncio.create_task(
                            self.command_router.handle_message(
                                task_id=task_id,
                                message=task_text,
                                context={
                                    "voice": True,
                                    "voice_session_id": session_id,
                                    "conversation_id": session.conversation_id,
                                },
                            ),
                            name=f"voice_task_{task_id}",
                        )
                        self._background_tasks.add(t)
                        t.add_done_callback(self._background_tasks.discard)
                        result_str = f"Task dispatched to agent swarm: {task_text[:80]}"
                    else:
                        result_str = "Task delegated."

                # ── C. Direct ToolRegistry execution ──────────────────────
                elif self.tool_registry and hasattr(self.tool_registry, "execute_tool"):
                    result_str = await self.tool_registry.execute_tool(call_name, call_args)

                # ── D. Fallback → OrchestrationEngine with task description ─
                elif self.command_router and hasattr(self.command_router, "handle_message"):
                    arg_str = json.dumps(call_args)
                    t = asyncio.create_task(
                        self.command_router.handle_message(
                            task_id=task_id,
                            message=f"Execute tool {call_name} with parameters: {arg_str}",
                            context={
                                "voice": True,
                                "voice_session_id": session_id,
                                "conversation_id": session.conversation_id,
                            },
                        ),
                        name=f"voice_task_{task_id}",
                    )
                    self._background_tasks.add(t)
                    t.add_done_callback(self._background_tasks.discard)
                    result_str = f"Tool {call_name} dispatched."

                else:
                    result_str = f"Tool {call_name} acknowledged."

            except Exception as e:
                logger.error("[VoiceEngine] Error executing tool %s: %s", call_name, e)
                result_str = f"Error executing {call_name}: {e}"

            function_responses.append(
                types.FunctionResponse(
                    id=call_id,
                    name=call_name,
                    response={"result": result_str},
                )
            )

        if function_responses and session._session:
            try:
                await session._session.send_tool_response(function_responses=function_responses)
            except Exception as te:
                logger.error("[VoiceEngine] Failed to send tool responses: %s", te)

    async def _close_session(self, session: VoiceSession) -> None:
        """Cleanly terminate a Gemini Live session and release resources."""
        session._active = False
        if session._receive_task and not session._receive_task.done():
            session._receive_task.cancel()
            try:
                await asyncio.wait_for(session._receive_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if session._session_cm:
            try:
                await session._session_cm.__aexit__(None, None, None)
            except Exception:
                pass

        session._session = None
        session._session_cm = None
