"""
Makima v7.2 — Elite Enterprise WebSocket Protocol v1 Engine

Defines and validates all WebSocket message schemas for protocol v1.
Features:
- Real-time bi-directional event bridge (WebSocketEventBridge)
- Auto-heartbeat ping/pong keepalive with latency tracking
- Zero-drop event buffering with high-water mark telemetry
- High-speed serialization (orjson + MessagePack fallbacks)
- Client session telemetry (throughput, latency, connection state)
- Strict protocol v1 type validation and structural enforcement

Server→Client and Client→Server message types.
JSON for UI events, MessagePack support for bulk payloads.
Rejects unknown 'v' field with version_mismatch error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Literal, Optional

# ─── High-Speed Serialization Engines ────────────────────────────────────────

try:
    import orjson
    def _json_dumps(obj: Any) -> str:
        return orjson.dumps(obj).decode("utf-8")
    def _json_dumps_bytes(obj: Any) -> bytes:
        return orjson.dumps(obj)
    def _json_loads(data: str | bytes) -> Any:
        return orjson.loads(data)
except ImportError:
    def _json_dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"))
    def _json_dumps_bytes(obj: Any) -> bytes:
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")
    def _json_loads(data: str | bytes) -> Any:
        return json.loads(data)

try:
    import msgpack
    HAS_MSGPACK = True
    def _msgpack_dumps(obj: Any) -> bytes:
        return msgpack.packb(obj, use_bin_type=True)
    def _msgpack_loads(data: bytes) -> Any:
        return msgpack.unpackb(data, raw=False)
except ImportError:
    HAS_MSGPACK = False
    def _msgpack_dumps(obj: Any) -> bytes:
        raise RuntimeError("msgpack is not installed")
    def _msgpack_loads(data: bytes) -> Any:
        raise RuntimeError("msgpack is not installed")

logger = logging.getLogger("makima.ws_protocol")

# ─── Protocol Version ────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1

# ─── Message Types ───────────────────────────────────────────────────────────

class ServerMessageType(str, Enum):
    """Server → Client message types."""
    # Heartbeat
    PING = "ping"
    PONG = "pong"
    
    # Chat
    AI_CHUNK = "ai_chunk"
    AI_RESPONSE_DONE = "ai_response_done"
    AI_ERROR = "ai_error"
    THINKING_STATUS = "thinking_status"
    
    # Agent lifecycle
    AGENT_STARTED = "agent_started"
    AGENT_DONE = "agent_done"
    AGENT_ERROR = "agent_error"
    AGENT_PROGRESS = "agent_progress"
    AGENT_GUARDRAIL_HIT = "agent_guardrail_hit"
    
    # Action confirmation
    ACTION_CONFIRM_REQUEST = "action_confirm_request"
    MESSAGE_PREVIEW = "message_preview"
    
    # Service health
    SERVICE_HEALTH = "service_health"
    SERVICE_CRASHED = "service_crashed"
    SERVICE_RESTARTING = "service_restarting"
    SERVICE_RECOVERED = "service_recovered"
    WATCHDOG_GIVE_UP = "watchdog_give_up"
    
    # Resource warnings
    BRAIN_MEMORY_CRITICAL = "brain_memory_critical"
    DISK_WARNING = "disk_warning"
    DISK_CRITICAL = "disk_critical"
    
    # LLM / cost
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    ALL_BACKENDS_DOWN = "all_backends_down"
    COST_UPDATE = "cost_update"
    COST_BUDGET_HIT = "cost_budget_hit"
    
    # Index
    INDEX_REBUILDING = "index_rebuilding"
    
    # Clipboard & notifications
    CLIPBOARD_CHANGED = "clipboard_changed"
    NOTIFICATION_RECEIVED = "notification_received"
    
    # Graph
    GRAPH_CONFLICT = "graph_conflict"
    
    # Ollama
    OLLAMA_UNAVAILABLE = "ollama_unavailable"
    OLLAMA_PULL_PROGRESS = "ollama_pull_progress"
    
    # Messaging safety
    WRONG_CONTACT_DETECTED = "wrong_contact_detected"
    
    # Config
    CONFIG_PARSE_ERROR = "config_parse_error"
    
    # Voice
    STATUS_LISTENING = "status_listening"
    STATUS_PROCESSING = "status_processing"
    STATUS_IDLE = "status_idle"
    TTS_ERROR = "tts_error"
    STT_OFFLINE = "stt_offline"
    STT_TRANSCRIPT_PREVIEW = "stt_transcript_preview"
    STT_LOW_CONFIDENCE = "stt_low_confidence"
    STT_CONFIRMED = "stt_confirmed"
    
    # Browser
    CAPTCHA_DETECTED = "captcha_detected"
    BROWSER_SESSION_UPDATE = "browser_session_update"
    
    # Tasks
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_COMPLETED = "task_completed"
    TASK_DELETED = "task_deleted"
    
    # Summaries
    CONVERSATION_SUMMARY = "conversation_summary"
    
    # Skills/Marketplace
    SKILL_INSTALLED = "skill_installed"
    SKILL_UNINSTALLED = "skill_uninstalled"
    SKILL_ENABLED = "skill_enabled"
    SKILL_DISABLED = "skill_disabled"
    
    # Export/Import
    EXPORT_COMPLETE = "export_complete"
    IMPORT_COMPLETE = "import_complete"
    
    # Cost analytics
    BUDGET_WARNING = "budget_warning"
    
    # Workflows
    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_UPDATED = "workflow_updated"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_DELETED = "workflow_deleted"
    
    # Multi-agent progress
    MULTI_AGENT_PROGRESS = "multi_agent_progress"
    
    # Daily briefing
    DAILY_BRIEFING = "daily_briefing"
    
    # Macros
    MACRO_RECORDED = "macro_recorded"
    MACRO_PLAYED = "macro_played"
    
    # App usage
    APP_USAGE_UPDATE = "app_usage_update"
    
    # Misc
    VERSION_MISMATCH = "version_mismatch"
    NATIVE_LAYER_DOWN = "native_layer_down"


class ClientMessageType(str, Enum):
    """Client → Server message types."""
    # Heartbeat
    PING = "ping"
    PONG = "pong"
    
    # Chat
    USER_MESSAGE = "user_message"
    CANCEL_TASK = "cancel_task"
    
    # Confirmations
    CONFIRM_ACTION = "confirm_action"
    APPROVE_ACTION = "approve_action"
    APPROVE_MESSAGE = "approve_message"
    REJECT_ACTION = "reject_action"
    
    # Feedback
    FEEDBACK = "feedback"
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    
    # Focus
    SET_FOCUS_PROFILE = "set_focus_profile"
    FOCUS_CHANGE = "focus_change"
    
    # Voice (Push-to-talk)
    PTT_DOWN = "ptt_down"
    PTT_UP = "ptt_up"
    STT_CONFIRM = "stt_confirm"
    STT_CORRECT = "stt_correct"
    STT_CANCEL = "stt_cancel"
    
    # Memory
    FORGET_ENTITY = "forget_entity"
    EXPORT_MEMORY = "export_memory"
    IMPORT_MEMORY = "import_memory"
    
    # Ollama
    PULL_OLLAMA_MODEL = "pull_ollama_model"
    DELETE_OLLAMA_MODEL = "delete_ollama_model"
    
    # Notifications
    SEND_NOTIFICATION = "send_notification"
    
    # Files
    ATTACH_FILE = "attach_file"
    
    # Settings
    UPDATE_CONFIG = "update_config"
    
    # Privacy
    TOGGLE_PRIVACY = "toggle_privacy"
    
    # Tasks
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    COMPLETE_TASK = "complete_task"
    DELETE_TASK = "delete_task"
    LIST_TASKS = "list_tasks"
    
    # Summarize
    SUMMARIZE_CONVERSATION = "summarize_conversation"
    
    # Skills
    INSTALL_SKILL = "install_skill"
    UNINSTALL_SKILL = "uninstall_skill"
    ENABLE_SKILL = "enable_skill"
    DISABLE_SKILL = "disable_skill"
    LIST_SKILLS = "list_skills"
    
    # Export/Import
    EXPORT_CONVERSATION = "export_conversation"
    IMPORT_CONVERSATION = "import_conversation"
    
    # Workflows
    CREATE_WORKFLOW = "create_workflow"
    RUN_WORKFLOW = "run_workflow"
    LIST_WORKFLOWS = "list_workflows"
    DELETE_WORKFLOW = "delete_workflow"
    
    # Macros
    START_MACRO_RECORDING = "start_macro_recording"
    STOP_MACRO_RECORDING = "stop_macro_recording"
    PLAY_MACRO = "play_macro"
    LIST_MACROS = "list_macros"
    
    # Daily briefing
    REQUEST_BRIEFING = "request_briefing"
    
    # Cost
    GET_COST_SUMMARY = "get_cost_summary"
    GET_BUDGET_STATUS = "get_budget_status"


# ─── Exceptions ──────────────────────────────────────────────────────────────

class ProtocolError(Exception):
    """Base exception for WebSocket protocol errors."""
    pass

class VersionMismatchError(ProtocolError):
    """Raised when protocol version doesn't match."""
    pass

class ValidationError(ProtocolError):
    """Raised when message structure fails strict validation."""
    pass


# ─── Client Session Telemetry ────────────────────────────────────────────────

@dataclass
class ClientSessionTelemetry:
    """Tracks real-time metrics for a connected client session."""
    session_id: str
    connected_at: float = field(default_factory=time.time)
    messages_rx: int = 0
    messages_tx: int = 0
    bytes_rx: int = 0
    bytes_tx: int = 0
    last_ping: float = 0.0
    last_pong: float = 0.0
    latency_ms: float = 0.0
    dropped_messages: int = 0  # Strictly monitored; should remain 0

    def record_rx(self, size: int) -> None:
        self.messages_rx += 1
        self.bytes_rx += size

    def record_tx(self, size: int) -> None:
        self.messages_tx += 1
        self.bytes_tx += size

    def record_pong(self, ping_ts: float) -> None:
        self.last_pong = time.time()
        self.latency_ms = (self.last_pong - ping_ts) * 1000


# ─── Zero-Drop Event Buffer ──────────────────────────────────────────────────

class ZeroDropEventBuffer:
    """
    Unbounded async queue ensuring zero message drops.
    Monitors high-water marks for backpressure telemetry.
    """
    def __init__(self, high_water_mark: int = 10000):
        self._queue: asyncio.Queue[WSMessage] = asyncio.Queue(maxsize=0)  # Unbounded
        self._high_water_mark = high_water_mark
        self._logger = logging.getLogger("makima.ws.buffer")

    async def put(self, msg: WSMessage) -> None:
        await self._queue.put(msg)
        qsize = self._queue.qsize()
        if qsize > self._high_water_mark and qsize % 1000 == 0:
            self._logger.warning(f"Buffer high water mark reached: {qsize} messages queued.")

    async def get(self) -> WSMessage:
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()


# ─── Message Dataclass ───────────────────────────────────────────────────────

@dataclass
class WSMessage:
    """
    WebSocket message wrapper. All messages have:
    - v: protocol version (must be 1)
    - type: message type string
    - payload: message-specific data
    - task_id: optional correlation ID for request/response matching
    - timestamp: Unix timestamp in seconds
    - msg_id: unique message identifier for deduplication/tracing
    """
    v: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    
    def to_dict(self) -> dict[str, Any]:
        data = {
            "v": self.v,
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "msg_id": self.msg_id
        }
        if self.task_id:
            data["task_id"] = self.task_id
        return data

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return _json_dumps(self.to_dict())
        
    def to_json_bytes(self) -> bytes:
        """Serialize to JSON bytes."""
        return _json_dumps_bytes(self.to_dict())
        
    def to_msgpack(self) -> bytes:
        """Serialize to MessagePack bytes."""
        if not HAS_MSGPACK:
            raise RuntimeError("msgpack library is not available")
        return _msgpack_dumps(self.to_dict())
    
    @classmethod
    def from_raw(cls, raw: str | bytes) -> "WSMessage":
        """
        Deserialize from raw JSON (str/bytes) or MessagePack (bytes).
        Raises ProtocolError on invalid data or version mismatch.
        """
        try:
            if isinstance(raw, bytes):
                try:
                    data = _msgpack_loads(raw) if HAS_MSGPACK else None
                    if not isinstance(data, dict):
                        data = _json_loads(raw.decode("utf-8"))
                except Exception:
                    data = _json_loads(raw.decode("utf-8"))
            else:
                data = _json_loads(raw)
        except Exception as e:
            raise ValidationError(f"Deserialization failed: {e}")

        return cls._from_dict(data)

    @classmethod
    def from_json(cls, raw: str) -> "WSMessage":
        """Backward-compatible JSON deserializer."""
        return cls.from_raw(raw)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "WSMessage":
        if not isinstance(data, dict):
            raise ValidationError("Message must be a JSON/MessagePack object")
            
        version = data.get("v")
        if version != PROTOCOL_VERSION:
            raise VersionMismatchError(f"Expected protocol v{PROTOCOL_VERSION}, got {version}")
            
        msg_type = data.get("type")
        if not msg_type or not isinstance(msg_type, str):
            raise ValidationError("Missing or invalid 'type' field")
            
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise ValidationError("'payload' must be a dictionary")
            
        return cls(
            v=version,
            type=msg_type,
            payload=payload,
            task_id=data.get("task_id"),
            timestamp=data.get("timestamp", time.time()),
            msg_id=data.get("msg_id", uuid.uuid4().hex[:16])
        )
    
    def validate_strict(self, direction: Literal["client", "server"]) -> bool:
        """Strictly validate message type against protocol enums."""
        try:
            if direction == "client":
                ClientMessageType(self.type)
            else:
                ServerMessageType(self.type)
            return True
        except ValueError:
            return False


# ─── Real-Time Bi-Directional Event Bridge ───────────────────────────────────

class WebSocketEventBridge:
    """
    Elite enterprise WebSocket manager handling auto-heartbeats, 
    zero-drop buffering, telemetry, and bi-directional streaming.
    """
    def __init__(self, ws: Any, session_id: str, ping_interval: float = 15.0, ping_timeout: float = 5.0):
        self.ws = ws
        self.session_id = session_id
        self.telemetry = ClientSessionTelemetry(session_id=session_id)
        self.out_buffer = ZeroDropEventBuffer()
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._logger = logging.getLogger(f"makima.ws.bridge.{session_id[:8]}")
        self._pong_event = asyncio.Event()

    async def start(self) -> None:
        """Start background sender and heartbeat loops."""
        self._running = True
        self._tasks.append(asyncio.create_task(self._sender_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

    async def stop(self) -> None:
        """Gracefully stop bridge and close connection."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        try:
            if hasattr(self.ws, "close"):
                await self.ws.close()
        except Exception:
            pass

    async def send(self, msg: WSMessage) -> None:
        """Queue a message for zero-drop delivery."""
        await self.out_buffer.put(msg)

    async def _sender_loop(self) -> None:
        """Background loop flushing the zero-drop buffer to the WebSocket."""
        while self._running:
            try:
                msg = await self.out_buffer.get()
                
                # Use MessagePack for large payloads (>10KB), otherwise JSON
                if HAS_MSGPACK and len(msg.payload) > 10000:
                    raw_bytes = msg.to_msgpack()
                    if hasattr(self.ws, "send_bytes"):
                        await self.ws.send_bytes(raw_bytes)
                    elif hasattr(self.ws, "send"):
                        await self.ws.send(raw_bytes)
                    self.telemetry.record_tx(len(raw_bytes))
                else:
                    raw_str = msg.to_json()
                    if hasattr(self.ws, "send_text"):
                        await self.ws.send_text(raw_str)
                    elif hasattr(self.ws, "send"):
                        await self.ws.send(raw_str)
                    self.telemetry.record_tx(len(raw_str.encode("utf-8")))
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Sender loop error: {e}")
                break

    async def _heartbeat_loop(self) -> None:
        """Auto-heartbeat ping/pong keepalive."""
        while self._running:
            await asyncio.sleep(self.ping_interval)
            if not self._running:
                break
            
            ping_ts = time.time()
            self.telemetry.last_ping = ping_ts
            self._pong_event.clear()
            
            ping_msg = WSMessage(v=PROTOCOL_VERSION, type=ServerMessageType.PING, payload={"ts": ping_ts})
            await self.send(ping_msg)
            
            try:
                await asyncio.wait_for(self._pong_event.wait(), timeout=self.ping_timeout)
            except asyncio.TimeoutError:
                self._logger.warning(f"Client {self.session_id} ping timeout. Closing connection.")
                await self.stop()
                break

    def handle_pong(self, msg: WSMessage) -> None:
        """Process incoming pong and update latency telemetry."""
        self.telemetry.record_pong(self.telemetry.last_ping)
        self._pong_event.set()

    async def receive_stream(self) -> AsyncIterator[WSMessage]:
        """Async generator yielding validated incoming client messages."""
        while self._running:
            try:
                raw = None
                # Robust interface detection (Starlette/FastAPI vs websockets)
                if hasattr(self.ws, "receive"):
                    asgi_msg = await self.ws.receive()
                    if asgi_msg.get("type") == "websocket.disconnect":
                        break
                    raw = asgi_msg.get("text")
                    if raw is None:
                        raw = asgi_msg.get("bytes")
                elif hasattr(self.ws, "recv"):
                    raw = await self.ws.recv()
                else:
                    self._logger.error("Unsupported WebSocket interface")
                    break
                    
                if not raw:
                    continue

                size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
                self.telemetry.record_rx(size)
                
                msg = WSMessage.from_raw(raw)
                
                if not msg.validate_strict("client"):
                    self._logger.warning(f"Invalid client message type: {msg.type}")
                    continue
                    
                if msg.type == ClientMessageType.PONG:
                    self.handle_pong(msg)
                    continue
                    
                yield msg
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e).lower()
                if "disconnect" in err_str or "closed" in err_str or "close" in err_str:
                    break
                self._logger.error(f"Receive stream error: {e}")
                break


# ─── Message Builders (Server → Client) ──────────────────────────────────────

def build_ping(ts: float) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=ServerMessageType.PING, payload={"ts": ts})

def build_pong(ts: float) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=ServerMessageType.PONG, payload={"ts": ts})

def build_ai_chunk(task_id: str, text: str, is_final: bool = False) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.AI_CHUNK,
        payload={"text": text, "is_final": is_final}, task_id=task_id,
    )

def build_ai_error(task_id: str, error: str, code: str = "unknown") -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.AI_ERROR,
        payload={"error": error, "code": code}, task_id=task_id,
    )

def build_agent_started(task_id: str, agent: str, subtask: str = "") -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.AGENT_STARTED,
        payload={"agent": agent, "subtask": subtask}, task_id=task_id,
    )

def build_agent_done(task_id: str, agent: str, result_summary: str = "") -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.AGENT_DONE,
        payload={"agent": agent, "result_summary": result_summary}, task_id=task_id,
    )

def build_service_health(snapshot: dict[str, Any]) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=ServerMessageType.SERVICE_HEALTH, payload=snapshot)

def build_service_crashed(service: str, reason: str) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.SERVICE_CRASHED,
        payload={"service": service, "reason": reason},
    )

def build_service_restarting(service: str, attempt: int, backoff_s: float) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.SERVICE_RESTARTING,
        payload={"service": service, "attempt": attempt, "backoff_s": backoff_s},
    )

def build_service_recovered(service: str) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.SERVICE_RECOVERED,
        payload={"service": service},
    )

def build_watchdog_give_up(service: str, attempts: int) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.WATCHDOG_GIVE_UP,
        payload={"service": service, "attempts": attempts},
    )

def build_disk_warning(used_pct: float, path: str) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.DISK_WARNING,
        payload={"used_pct": used_pct, "path": path},
    )

def build_disk_critical(used_pct: float) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.DISK_CRITICAL,
        payload={"used_pct": used_pct},
    )

def build_cost_update(provider: str, tokens: int, cost_usd: float, total_usd: float) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.COST_UPDATE,
        payload={"provider": provider, "tokens": tokens, "cost_usd": cost_usd, "total_usd": total_usd},
    )

def build_action_confirm(task_id: str, action: str, description: str, risk_level: str = "medium") -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.ACTION_CONFIRM_REQUEST,
        payload={"action": action, "description": description, "risk_level": risk_level}, task_id=task_id,
    )

def build_message_preview(task_id: str, contact: str, platform: str, draft_text: str, draft_id: str) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.MESSAGE_PREVIEW,
        payload={"contact": contact, "platform": platform, "draft_text": draft_text, "draft_id": draft_id}, task_id=task_id,
    )

def build_version_mismatch(got: int) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.VERSION_MISMATCH,
        payload={"expected": PROTOCOL_VERSION, "got": got},
    )

def build_stt_transcript_preview(task_id: str, transcript: str, confidence: float, language: str) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.STT_TRANSCRIPT_PREVIEW,
        payload={"transcript": transcript, "confidence": confidence, "language": language}, task_id=task_id,
    )

def build_stt_confirmed(task_id: str, final_transcript: str, was_corrected: bool = False) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.STT_CONFIRMED,
        payload={"final_transcript": final_transcript, "was_corrected": was_corrected}, task_id=task_id,
    )

def build_task_event(event_type: str, task_data: dict) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=event_type, payload=task_data)

def build_daily_briefing(briefing: dict) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=ServerMessageType.DAILY_BRIEFING, payload=briefing)

def build_multi_agent_progress(task_id: str, agents: list[dict]) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.MULTI_AGENT_PROGRESS,
        payload={"agents": agents}, task_id=task_id,
    )

def build_browser_session_update(session_id: str, status: str, url: str = "") -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.BROWSER_SESSION_UPDATE,
        payload={"session_id": session_id, "status": status, "url": url},
    )

def build_budget_warning(spent: float, budget: float, scope: str) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.BUDGET_WARNING,
        payload={"spent": spent, "budget": budget, "scope": scope},
    )

def build_workflow_event(event_type: str, workflow_data: dict) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=event_type, payload=workflow_data)

def build_conversation_summary(conversation_id: str, summary: str, word_count: int) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.CONVERSATION_SUMMARY,
        payload={"conversation_id": conversation_id, "summary": summary, "word_count": word_count},
    )

def build_skill_event(event_type: str, skill_data: dict) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=event_type, payload=skill_data)

def build_export_complete(conversation_id: str, file: str, format: str, message_count: int) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.EXPORT_COMPLETE,
        payload={"conversation_id": conversation_id, "file": file, "format": format, "message_count": message_count},
    )

def build_import_complete(conversation_id: str, message_count: int) -> WSMessage:
    return WSMessage(
        v=PROTOCOL_VERSION, type=ServerMessageType.IMPORT_COMPLETE,
        payload={"conversation_id": conversation_id, "message_count": message_count},
    )

def build_macro_event(event_type: str, macro_data: dict) -> WSMessage:
    return WSMessage(v=PROTOCOL_VERSION, type=event_type, payload=macro_data)


# ─── Task ID Generator ───────────────────────────────────────────────────────

def generate_task_id() -> str:
    """Generate a unique task ID for request/response correlation."""
    return f"task_{uuid.uuid4().hex[:12]}"
