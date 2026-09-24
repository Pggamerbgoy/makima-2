"""
Makima Brain Entry Point

FastAPI application with:
- ASGI lifespan: init all modules via AppBootstrap on startup, graceful shutdown
- REST: GET /health, GET /status, /settings, /media, /auth, /conversations
- WebSocket: ws://127.0.0.1:8080/ws — bidirectional WS protocol v1
- All agent and user execution flows through OrchestrationEngine
"""

from __future__ import annotations

import asyncio
import base64
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

try:
    import dotenv
    _env_path = Path(__file__).resolve().parents[2] / ".env"
    if _env_path.exists():
        dotenv.load_dotenv(dotenv_path=_env_path)
    else:
        dotenv.load_dotenv()
except ImportError:
    pass


repo_root = str(Path(__file__).resolve().parents[2])
brain_dir = str(Path(__file__).resolve().parent)
# Remove brain_dir if Python prepended it to sys.path, ensuring 'agents' imports openai-agents SDK
while brain_dir in sys.path:
    sys.path.remove(brain_dir)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

if __name__ == "__main__" and not __package__:
    __package__ = "apps.brain"

from .media_store import MediaNotFoundError, MediaStore, MediaValidationError
from .ws_protocol import (
    PROTOCOL_VERSION,
    ClientMessageType,
    ServerMessageType,
    WSMessage,
    build_ai_chunk,
    build_pong,
    build_voice_event,
    generate_task_id,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Windows Console QuickEdit Disabler
# ---------------------------------------------------------------------------
def disable_windows_quick_edit() -> None:
    """
    Disable Windows Console QuickEdit Mode.
    When QuickEdit Mode is enabled, clicking inside the console window activates
    Mark (selection) mode. In Mark mode, the Windows kernel console subsystem (conhost)
    suspends all stdout/stderr writes, freezing the single-threaded asyncio event loop
    in WriteConsoleW until the user presses Ctrl+C, Enter, or Esc.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_INPUT_HANDLE = -10 (0xFFFFFFF6)
            h_stdin = kernel32.GetStdHandle(-10)
            if h_stdin and h_stdin != -1:
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
                    ENABLE_QUICK_EDIT_MODE = 0x0040
                    ENABLE_EXTENDED_FLAGS = 0x0080
                    new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
                    kernel32.SetConsoleMode(h_stdin, new_mode)
                    logger.info("[boot] Windows Console QuickEdit Mode disabled (mouse clicks will not freeze server).")
        except Exception as e:
            logger.debug("Could not disable QuickEdit mode: %s", e)


_LOG_DIR = Path.home() / ".makima" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _compute_boot_fingerprint() -> str:
    """Short git fingerprint so stale processes are identifiable in logs."""
    try:
        root = Path(__file__).resolve().parents[2]
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root, capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        fp = commit or "nogit"
        return f"{fp}{'-dirty' if dirty else '-clean'}"
    except Exception:
        return "unknown"


BOOT_FINGERPRINT: str = _compute_boot_fingerprint()
BOOT_INFO: dict = {"fingerprint": BOOT_FINGERPRINT, "started_at": None}


class _BootFingerprintFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.boot_fp = BOOT_FINGERPRINT
        return True


_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s [boot:%(boot_fp)s]: %(message)s"
_fp_filter = _BootFingerprintFilter()
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(_LOG_FMT))
_console_handler.addFilter(_fp_filter)

# Test/benchmark runs must NOT pollute the production rotating log — their
# fault-injection noise ("Mock OS error" etc.) made prod triage misleading.
# Set MAKIMA_LOG_TO_FILE=0 (tests do this via conftest.py) to skip the file.
_IN_TEST_RUN = (
    os.environ.get("MAKIMA_LOG_TO_FILE", "").strip().lower() in ("0", "false", "no")
    or "PYTEST_CURRENT_TEST" in os.environ
    or "pytest" in sys.modules
    or any("unittest" in a or a.startswith("tests/") or "\\tests\\" in a for a in sys.argv[:3])
)
_handlers: list[logging.Handler] = [_console_handler]
if not _IN_TEST_RUN:
    _file_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "makima.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _file_handler.setFormatter(logging.Formatter(_LOG_FMT))
    _file_handler.addFilter(_fp_filter)
    _handlers.append(_file_handler)

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FMT,
    handlers=_handlers,
)
logger = logging.getLogger("makima.main")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "default.yaml"

def _load_config() -> dict:
    # 1. Load .env file from project root if present
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip("'\"")
            logger.info(f"Loaded environment variables from {env_file}")
        except Exception as e:
            logger.warning(f"Failed to read .env file: {e}")

    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        logger.warning(f"Config not found at {_CONFIG_PATH}, using defaults.")
        cfg = {}

    # Environment variable overrides
    env_overrides = {
        "MAKIMA_GROQ_KEY": [("llm", "backends", "groq", "api_key")],
        "GROQ_API_KEY": [("llm", "backends", "groq", "api_key")],
        "MAKIMA_GEMINI_KEY": [("llm", "backends", "gemini", "api_key")],
        "GEMINI_API_KEY": [("llm", "backends", "gemini", "api_key")],
        "MAKIMA_OPENAI_KEY": [("llm", "backends", "openai", "api_key")],
        "OPENAI_API_KEY": [("llm", "backends", "openai", "api_key")],
        "MAKIMA_OPENROUTER_KEY": [
            ("llm", "backends", "openrouter", "api_key"),
            ("llm", "backends", "claude", "api_key"),
        ],
        "OPENROUTER_API_KEY": [
            ("llm", "backends", "openrouter", "api_key"),
            ("llm", "backends", "claude", "api_key"),
        ],
        "MAKIMA_DASHSCOPE_KEY": [
            ("llm", "backends", "qwen", "api_key"),
            ("llm", "backends", "qwen_flash", "api_key"),
        ],
        "DASHSCOPE_API_KEY": [
            ("llm", "backends", "qwen", "api_key"),
            ("llm", "backends", "qwen_flash", "api_key"),
        ],
        "MAKIMA_CEREBRAS_KEY": [("llm", "backends", "cerebras", "api_key")],
    }
    for env_var, paths in env_overrides.items():
        val = os.environ.get(env_var)
        if val:
            for path in paths:
                d = cfg
                for key in path[:-1]:
                    d = d.setdefault(key, {})
                d[path[-1]] = val

    # Ensure active_provider from default.yaml synchronizes to default_provider
    if cfg.get("llm", {}).get("active_provider") and not cfg.get("llm", {}).get("default_provider"):
        cfg["llm"]["default_provider"] = cfg["llm"]["active_provider"]

    return cfg


CONFIG: dict = {}

# ---------------------------------------------------------------------------
# WebSocket connection registry (multi-user & multi-device isolated routing)
# ---------------------------------------------------------------------------
_ws_clients: set[WebSocket] = set()
_active_ws_tasks: set[asyncio.Task] = set()
_task_to_ws: dict[str, WebSocket] = {}


async def ws_broadcast(msg: Any) -> None:
    """Send task-scoped messages to originating client or broadcast system events."""
    target_task_id = None
    is_done = False

    if isinstance(msg, WSMessage):
        data = msg.to_json()
        target_task_id = getattr(msg, "task_id", None)
        msg_type = getattr(msg, "type", "")
        if hasattr(msg_type, "value"):
            msg_type = msg_type.value
        payload = getattr(msg, "payload", {}) or {}
        is_done = (
            bool(getattr(msg, "is_final", False))
            or (isinstance(payload, dict) and bool(payload.get("is_final")))
            or msg_type in ("ai_response_done", "ai_error", "voice_tts_stopped")
        )
    else:
        import json
        if isinstance(msg, dict):
            data = json.dumps(msg)
            target_task_id = msg.get("task_id")
            m_type = msg.get("type", "")
            payload = msg.get("payload", {})
            is_done = (
                bool(msg.get("is_final"))
                or (isinstance(payload, dict) and bool(payload.get("is_final")))
                or m_type in ("ai_response_done", "ai_error", "voice_tts_stopped")
            )
        else:
            data = str(msg)

    # Targeted delivery: If this task originated from a specific connected WebSocket client, send ONLY to that client
    if target_task_id and target_task_id in _task_to_ws:
        target_ws = _task_to_ws[target_task_id]
        try:
            await target_ws.send_text(data)
            if is_done:
                _task_to_ws.pop(target_task_id, None)
            return
        except Exception:
            _task_to_ws.pop(target_task_id, None)
            _ws_clients.discard(target_ws)

    # Global broadcast (pings, global notifications, alerts)
    dead: set[WebSocket] = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    if dead:
        _ws_clients.difference_update(dead)


# ---------------------------------------------------------------------------
# Module instances (populated during lifespan startup)
# ---------------------------------------------------------------------------
_modules: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """ASGI lifespan: startup and graceful shutdown powered by AppBootstrap & OrchestrationEngine."""
    global CONFIG

    disable_windows_quick_edit()

    logger.info("=" * 60)
    logger.info("  Makima Brain — Starting Up (AppBootstrap + OrchestrationEngine)")
    logger.info("=" * 60)
    BOOT_INFO["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Boot fingerprint: %s @ %s (compare against newest source edit to detect stale processes)", BOOT_FINGERPRINT, BOOT_INFO["started_at"])

    CONFIG = _load_config()

    from .core.app_bootstrap import AppBootstrap
    bootstrap = AppBootstrap(config=CONFIG, ws_broadcast=ws_broadcast)
    services = await bootstrap.initialize_services()
    await bootstrap.start_background_services()

    # Populate _modules dictionary from service registry
    for name, instance in services.items():
        _modules[name] = instance

    # Wire aliases for backwards compatibility
    orchestration_engine = services.get("orchestration_engine")
    _modules["router"] = orchestration_engine
    _modules["command_router"] = orchestration_engine
    _modules["orchestration_engine"] = orchestration_engine
    _modules["orchestrator"] = services.get("orchestrator")
    _modules["speech"] = services.get("voice") or services.get("speech")
    _modules["voice"] = _modules["speech"]
    _modules["voice_pipeline"] = _modules["speech"]
    _modules["memory"] = services.get("memory") or services.get("eternal_memory")
    _modules["settings_store"] = services.get("settings_store")
    _modules["media_store"] = services.get("media_store")

    # On startup: inject persisted integration credentials from integrations.json → os.environ
    # This ensures tools work immediately without requiring a UI save after restart
    _startup_store = _modules.get("settings_store")
    if _startup_store and hasattr(_startup_store, "list_integrations"):
        _STARTUP_ENV_MAP: dict[str, list[tuple[str, str]]] = {
            "telegram":  [("bot_token", "TELEGRAM_BOT_TOKEN")],
            "discord":   [("bot_token", "DISCORD_BOT_TOKEN")],
            "github":    [("api_key", "GITHUB_TOKEN"), ("api_key", "GH_TOKEN")],
            "gmail":     [("app_password", "GMAIL_APP_PASSWORD")],
            "youtube":   [("api_key", "YOUTUBE_API_KEY")],
            "notion":    [("api_token", "NOTION_API_TOKEN")],
            "figma":     [("personal_token", "FIGMA_PERSONAL_TOKEN")],
            "slack":     [("webhook_url", "SLACK_WEBHOOK_URL")],
            "gdrive":    [("client_token", "GDRIVE_CLIENT_TOKEN")],
            "spotify":   [("client_id", "SPOTIFY_CLIENT_ID")],
        }
        injected_count = 0
        for int_id in _startup_store.list_integrations():
            env_pairs = _STARTUP_ENV_MAP.get(int_id, [])
            if not env_pairs:
                continue
            fields = _startup_store.get_integration_fields(int_id)
            for field_name, env_var in env_pairs:
                val = fields.get(field_name, "")
                if val and not os.environ.get(env_var):  # don't overwrite .env values
                    os.environ[env_var] = val
                    injected_count += 1
        if injected_count:
            logger.info("[startup] Injected %d connector credential(s) from integrations.json into env.", injected_count)
    _modules["multimodal"] = services.get("multimodal")
    _modules["health"] = services.get("health")
    _modules["focus_profiles"] = services.get("focus_profiles")
    _modules["memory_forget"] = services.get("memory_forget")
    _modules["skill_library"] = services.get("skill_library")
    proactive = services.get("proactive_orchestrator")
    if proactive and hasattr(proactive, "start"):
        try:
            await proactive.start()
            logger.info("ProactiveOrchestrator active in background (autonomous mode=%s).", getattr(proactive, "mode", "unknown"))
        except Exception as pe:
            logger.warning("Failed to start ProactiveOrchestrator: %s", pe)

    app.state.modules = _modules
    app.state.services = services

    logger.info("All modules started. Brain is ready.")
    logger.info("   UI:   http://127.0.0.1:8080/")
    logger.info("   REST: http://127.0.0.1:8080/health")
    logger.info("   WS:   ws://127.0.0.1:8080/ws")

    # Run environment capability scan so Makima knows what's available on this machine
    # (WhatsApp Desktop? Chrome? Telegram token? etc.) — tools use this to auto-select strategy
    try:
        from .core.capability_probe import run_startup_probe
        run_startup_probe()
    except Exception as _cap_err:
        logger.debug("[startup] CapabilityProbe scan failed (non-fatal): %s", _cap_err)

    try:
        yield
    finally:
        logger.info("Makima Brain shutting down...")
        if proactive and hasattr(proactive, "stop"):
            try:
                await proactive.stop()
            except Exception as stop_err:
                logger.debug("ProactiveOrchestrator stop failed: %s", stop_err)
        await bootstrap.shutdown_services()
        # Close shared HTTP client pool from web_search_tool
        try:
            from .web_search_tool import close_client as _close_search_client
            await _close_search_client()
        except Exception as close_err:
            logger.debug("web_search_tool client close failed: %s", close_err)
        logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Makima Brain",
    version="8.2.0",
    description="Makima AI Desktop Operating Assistant",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — restrict to known trusted origins (Workstation UI, Vite dev, Tauri)
# ---------------------------------------------------------------------------
_CORS_ORIGINS: list[str] = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "tauri://localhost",
    "https://tauri.localhost",
]
# Allow additional origins from env (e.g. for custom dev setups)
_extra_origins = os.environ.get("MAKIMA_EXTRA_CORS_ORIGINS", "").strip()
if _extra_origins:
    _CORS_ORIGINS.extend([o.strip() for o in _extra_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,   # credentials are passed per-request in WS payload, not via cookies
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


# ---------------------------------------------------------------------------
# Conversational Workspace UI
# ---------------------------------------------------------------------------
_WEB_DIR = Path(__file__).resolve().parent / "web"
_WEB_DIR.mkdir(parents=True, exist_ok=True)
_ASSETS_DIR = _WEB_DIR / "assets"
if _ASSETS_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def serve_workstation_ui():
    """Serve the conversational workspace UI directly from Makima Brain."""
    index_file = _WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    # Fallback to apps/chat_ui if present
    chat_ui_index = Path(__file__).resolve().parents[1] / "chat_ui" / "index.html"
    if chat_ui_index.exists():
        return FileResponse(str(chat_ui_index), media_type="text/html")
    return HTMLResponse(
        "<html><body style='background:#131314;color:#e3e3e3;font-family:sans-serif;padding:40px;'>"
        "<h2>Makima Brain Online</h2><p>Conversational workspace interface loading...</p>"
        "</body></html>"
    )



@app.get("/health")
async def health_check():
    """Quick health check endpoint."""
    health: Any = _modules.get("health")
    if health:
        ok = health.is_critical_path_ok()
        return {"status": "ok" if ok else "degraded", "version": "8.2.0"}
    return {"status": "starting", "version": "8.2.0"}


_gpu_cache: dict[str, Any] = {"time": 0.0, "data": None}


def _get_gpu_metrics() -> dict[str, Any]:
    now = time.time()
    if now - _gpu_cache["time"] < 2.5 and _gpu_cache["data"] is not None:
        return _gpu_cache["data"]
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=1.5,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        parts = [p.strip() for p in out.split(",")]
        data = {
            "available": True,
            "name": parts[0],
            "gpu_percent": round(float(parts[1]), 1),
            "mem_used_mb": round(float(parts[2]), 1),
            "mem_total_mb": round(float(parts[3]), 1),
            "temperature_c": round(float(parts[4]), 1),
        }
        _gpu_cache["time"] = now
        _gpu_cache["data"] = data
        return data
    except Exception:
        fallback = {"available": False, "gpu_percent": 0.0, "name": "CPU/Unified"}
        _gpu_cache["time"] = now
        _gpu_cache["data"] = fallback
        return fallback


@app.get("/status")
async def full_status():
    """Full service health snapshot with real hardware metrics."""
    health: Any = _modules.get("health")
    orchestrator: Any = _modules.get("orchestrator")
    ollama: Any = _modules.get("ollama")

    snapshot = {}
    snapshot["boot"] = dict(BOOT_INFO)
    if health:
        snapshot["services"] = {
            k: {"status": v.status, "error": v.error}
            for k, v in health.get_snapshot().items()
        }
    if orchestrator:
        getter = getattr(orchestrator, "get_status", None)
        if callable(getter):
            try:
                snapshot["agents"] = getter()
            except Exception as e:
                snapshot["agents"] = {"error": str(e)}
        else:
            snapshot["agents"] = {"type": type(orchestrator).__name__, "get_status": "unavailable"}
    ai_handler: Any = _modules.get("ai_handler")
    if ai_handler is not None and hasattr(ai_handler, "backends"):
        snapshot["llm_backends"] = {
            name: {
                "enabled": p.enabled,
                "model": p.model,
                "supports_tools": p.supports_tools,
                "circuit_breaker": getattr(p.circuit_breaker, "state", "unknown"),
            }
            for name, p in ai_handler.backends.items()
        }
    if ollama and hasattr(ollama, "list_models"):
        snapshot["local_models"] = await ollama.list_models()

    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=None)
        if cpu_pct == 0.0:
            cpu_pct = psutil.cpu_percent(interval=0.05)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        gpu = _get_gpu_metrics()
        snapshot["system_metrics"] = {
            "cpu_percent": round(cpu_pct, 1),
            "ram_percent": round(mem.percent, 1),
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "disk_percent": round(disk.percent, 1),
            "processes_count": len(psutil.pids()),
            "gpu": gpu,
            "gpu_percent": gpu.get("gpu_percent", 0.0),
        }
    except Exception as e:
        snapshot["system_metrics"] = {
            "cpu_percent": 0.0,
            "ram_percent": 0.0,
            "error": str(e),
        }

    store: Any = _modules.get("settings_store")
    active_pid = getattr(ai_handler, "default_provider", None) or (store.get_settings().get("default_llm_backend") if store else None) or "groq"
    active_model = ""
    if ai_handler and hasattr(ai_handler, "backends"):
        target_prof = ai_handler.backends.get(active_pid)
        if target_prof and target_prof.model:
            active_model = target_prof.model
    snapshot["active_llm"] = {
        "provider": active_pid,
        "model": active_model or "llama-3.3-70b-versatile",
    }

    return JSONResponse(content=snapshot)



@app.get("/api/audit/events")
async def list_audit_events(limit: int = 50):
    """Fetch real kernel events, tool calls, and agent activity from EventStore."""
    orchestrator: Any = _modules.get("orchestrator")
    event_store = getattr(orchestrator, "_event_store", None)
    if not event_store:
        try:
            from .core.persistence import EventStore
            event_store = EventStore("~/.makima/kernel_events.db")
        except Exception as store_err:
            logger.debug("[audit] EventStore open failed: %s", store_err)
            event_store = None

    if event_store and hasattr(event_store, "get_recent_events"):
        events = await event_store.get_recent_events(limit=limit)
        total_count = 0
        try:
            if hasattr(event_store, "_conn") and event_store._conn:
                total_count = event_store._conn.execute("SELECT COUNT(*) FROM kernel_events").fetchone()[0]
        except Exception:
            total_count = len(events)
        return {
            "total_count": total_count,
            "events": [
                {
                    "id": ev.id,
                    "timestamp": ev.timestamp,
                    "time_str": time.strftime("%H:%M:%S", time.localtime(ev.timestamp)),
                    "event_type": ev.event_type,
                    "task_id": ev.task_id or "",
                    "agent_name": ev.agent_name or "kernel",
                    "payload": ev.payload,
                }
                for ev in events
            ]
        }
    return {"total_count": 0, "events": []}


@app.get("/api/automation/routines")
async def list_automation_routines():
    """Fetch real pending reminders/routines from DurableTaskEngine."""
    schedules = []
    macros: list[dict[str, Any]] = []
    try:
        dte = _modules.get("durable_task_engine")
        if dte and hasattr(dte, "list_pending_tasks"):
            pending = await dte.list_pending_tasks()
            now = time.time()
            for cp in pending or []:
                ra = getattr(cp, "resume_after", None)
                if not ra:
                    continue
                ctx = getattr(cp, "context_snapshot", None) or {}
                rep = ctx.get("repeat_interval_s") if isinstance(ctx, dict) else None
                schedules.append({
                    "id": cp.task_id,
                    "name": getattr(cp, "task_name", cp.task_id),
                    "text": getattr(cp, "original_prompt", ""),
                    "fires_in_s": max(0, int(ra - now)),
                    "repeat_s": rep,
                    "status": "active",
                })
    except Exception as sched_err:
        logger.debug("[automation] list routines failed: %s", sched_err)

    return {
        "schedules": schedules,
        "macros": macros,
        "active_count": len(schedules) + len(macros),
    }


@app.post("/api/automation/schedule")
async def create_automation_schedule(request: Request):
    """Schedule a real reminder/routine via DurableTaskEngine."""
    try:
        data = await request.json()
        name = str(data.get("name", "Custom Routine")).strip()
        prompt = str(data.get("prompt", "")).strip()
        delay_s = data.get("delay_s", data.get("delay_min", 0))
        try:
            delay_s = float(delay_s) * (60.0 if "delay_min" in data and "delay_s" not in data else 1.0)
        except (TypeError, ValueError):
            delay_s = 0.0
        try:
            repeat_s = float(data.get("repeat_s", 0) or 0)
        except (TypeError, ValueError):
            repeat_s = 0.0
        if not name or not prompt:
            return {"ok": False, "error": "Name and prompt are required"}
        if delay_s <= 0:
            return {"ok": False, "error": "delay_s (or delay_min) must be positive seconds"}
        if data.get("expr") and "delay_s" not in data and "delay_min" not in data:
            return {"ok": False, "error": "cron expressions are not supported yet — send delay_s instead"}

        dte = _modules.get("durable_task_engine")
        if dte is None or not hasattr(dte, "checkpoint_task"):
            return {"ok": False, "error": "Scheduler unavailable (DurableTaskEngine not running)"}
        task_id = f"rem_{uuid.uuid4().hex[:8]}"
        await dte.checkpoint_task(
            task_id=task_id,
            prompt=prompt,
            task_name=name,
            context={"reminder": True, "repeat_interval_s": repeat_s or None, "conversation_id": "default_session"},
            resume_after_seconds=delay_s,
            status="active",
            original_prompt=prompt,
        )
        return {"ok": True, "id": task_id, "message": f"Routine '{name}' scheduled in {delay_s:.0f}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/automation/macro/play")
async def play_automation_macro(request: Request):
    """Dispatch a saved macro or automation workflow for execution."""
    try:
        data = await request.json()
        macro_name = data.get("name") or data.get("macro") or "System Diagnostic"
        router = _modules.get("router") or _modules.get("orchestration_engine")
        task_id = f"macro_exec_{int(time.time() * 1000)}"
        if router and hasattr(router, "handle_text_command"):
            asyncio.create_task(router.handle_text_command(
                text=f"Execute automated macro: {macro_name}",
                session_id="macro_session",
                task_id=task_id,
            ))
            return {"ok": True, "task_id": task_id, "message": f"Macro '{macro_name}' dispatched"}
        return {"ok": True, "task_id": task_id, "message": f"Macro '{macro_name}' executed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/security/audit")
async def run_security_audit():
    """Count configured integrations; do not invent isolation/sandbox claims."""
    try:
        store = _modules.get("settings_store")
        configured: list[str] = []
        if store:
            for k in ("telegram", "whatsapp", "github", "gdrive", "gmail", "spotify", "slack", "notion", "discord", "youtube", "figma"):
                try:
                    if store.has_integration_configured(k):
                        configured.append(k)
                except Exception:
                    continue
        findings: list[str] = []
        if not configured:
            findings.append("No integrations configured — nothing external to audit.")
        status = "PASS" if not findings else "ATTENTION"
        return {
            "ok": True,
            "status": status,
            "scanned_integrations": len(configured),
            "configured_integrations": configured,
            "privilege_leaks": None,
            "isolation_level": None,
            "sandbox_status": None,
            "findings": findings,
            "note": "Integrations inventory only; privilege/isolation checks are not implemented.",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}



@app.get("/conversations")
async def list_conversations():
    memory = _modules.get("memory")
    return {"conversations": await memory.list_conversations() if memory else []}


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    memory = _modules.get("memory")
    return {"messages": await memory.get_conversation(conversation_id) if memory else []}


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    memory = _modules.get("memory")
    if not memory:
        return {"status": "error", "message": "Memory offline"}
    success = await memory.delete_conversation(conversation_id)
    router = _modules.get("router")
    if router and hasattr(router, "cleanup_conversation"):
        router.cleanup_conversation(conversation_id)
    return {"status": "ok" if success else "error"}


@app.get("/integrations")
async def integrations_status():
    health = _modules.get("health")
    store = _modules.get("settings_store")
    services = health.get_snapshot() if health else {}
    result = []
    for name, value in services.items():
        entry = {"id": name, "status": value.status, "error": value.error}
        if store:
            entry["configured"] = store.has_integration_configured(name)
            entry["fields"] = store.get_integration_fields(name)
        result.append(entry)
    return {"integrations": result}


@app.post("/integrations/{integration_id}")
async def save_integration(integration_id: str, request: Request):
    store = _modules.get("settings_store")
    if not store:
        return {"ok": False, "error": "Settings store not available."}
    payload = await request.json()
    fields = payload.get("fields", {})
    if not isinstance(fields, dict):
        return {"ok": False, "error": "'fields' must be an object."}
    store.save_integration_fields(integration_id, fields)
    return {
        "ok": True,
        "message": "Saved. Restart the Makima brain for this to take effect.",
        "fields": store.get_integration_fields(integration_id),
    }


@app.post("/integrations/{integration_id}/test")
async def test_integration(integration_id: str):
    store = _modules.get("settings_store")
    if not store:
        return {"ok": False, "message": "Settings store not available."}

    raw = store.get_integration_raw(integration_id)

    if integration_id == "telegram":
        token = raw.get("bot_token", "")
        if not token:
            return {"ok": False, "message": "No bot token saved yet."}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot_name = data.get("result", {}).get("username", "unknown")
                return {"ok": True, "message": f"Connected as @{bot_name}"}
            return {"ok": False, "message": data.get("description", "Telegram rejected the token.")}
        except Exception as e:
            return {"ok": False, "message": f"Connection test failed: {e}"}

    if integration_id == "github":
        token = raw.get("api_key", "")
        if not token:
            return {"ok": False, "message": "No GitHub token saved yet."}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}) as client:
                resp = await client.get("https://api.github.com/user")
            if resp.status_code == 200:
                login = resp.json().get("login", "unknown")
                return {"ok": True, "message": f"Connected as @{login}"}
            if resp.status_code == 401:
                return {"ok": False, "message": "GitHub token is invalid or expired."}
            return {"ok": False, "message": f"GitHub returned HTTP {resp.status_code}."}
        except Exception as e:
            return {"ok": False, "message": f"Connection test failed: {e}"}

    if integration_id == "slack":
        webhook = raw.get("webhook_url", "")
        if not webhook:
            return {"ok": False, "message": "No Slack webhook URL saved yet."}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook, json={"text": "Makima connectivity test ✅"})
            if resp.status_code in (200, 201):
                return {"ok": True, "message": "Slack webhook accepted the test message."}
            return {"ok": False, "message": f"Slack webhook returned HTTP {resp.status_code}."}
        except Exception as e:
            return {"ok": False, "message": f"Connection test failed: {e}"}

    if not raw or not any(raw.values()):
        return {"ok": False, "message": "No credentials saved yet for this integration."}

    # Credentials are saved. Connectivity not auto-verified for this provider yet,
    # but they will be used when this integration is activated.
    _INTEGRATION_HINTS: dict[str, str] = {
        "whatsapp": "WhatsApp phone saved. Makima uses it as the default sender identity when messaging.",
        "discord": "Discord bot token saved. Makima will use it when sending Discord messages.",
        "gmail": "Gmail app password saved. Makima will use it when sending emails.",
        "notion": "Notion API token saved. Makima will use it for Notion operations.",
        "youtube": "YouTube API key saved. Makima will use it for YouTube Data API operations.",
        "figma": "Figma personal token saved. Makima will use it for Figma file operations.",
        "google": "Google OAuth saved. Makima will use it for Calendar/Drive access.",
    }
    hint = _INTEGRATION_HINTS.get(integration_id, f"Credentials saved for '{integration_id}'.")
    return {"ok": True, "message": hint, "note": "Auto-connectivity test not yet available for this provider — credentials are active."}


# ---------------------------------------------------------------------------
# Native File Launcher & Download Endpoints
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Path Safety Helper — prevents directory traversal on file endpoints
# ---------------------------------------------------------------------------
_ALLOWED_OPEN_BASES: list[Path] = [
    Path(os.path.expanduser("~/.makima")).resolve(),
    Path("makima_workspace").resolve(),
    Path("output").resolve(),
]


def _resolve_safe_path(raw_path: str) -> Path | None:
    """Resolve raw_path to an absolute Path, ensuring it stays within allowed bases.

    Returns None if the path resolves outside all allowed base directories
    (i.e. a directory-traversal or arbitrary-file-read attempt).
    """
    clean = re.sub(r"^file:[/\\]+", "", raw_path)
    if sys.platform == "win32" and re.match(r"^/?[a-zA-Z]:", clean):
        clean = clean.lstrip("/")

    candidate = Path(clean)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        # Try known workspace sub-dirs first, then cwd-relative
        for base in [
            Path("makima_workspace/output/temp"),
            Path("makima_workspace/output"),
            Path("makima_workspace"),
        ]:
            cand = (base / clean).resolve()
            if cand.exists():
                resolved = cand
                break
        else:
            resolved = Path(clean).resolve()

    # Re-evaluate allowed bases at call time so they pick up cwd correctly
    live_allowed = [
        Path(os.path.expanduser("~/.makima")).resolve(),
        Path("makima_workspace").resolve(),
        Path("output").resolve(),
    ]
    for allowed in live_allowed:
        try:
            resolved.relative_to(allowed)
            return resolved  # inside an allowed base — safe
        except ValueError:
            continue
    return None  # outside all allowed bases — reject


@app.post("/api/open-file")
async def open_local_file(request: Request):
    """Opens a document natively on the host OS (Excel, Word, PDF, Explorer)."""
    payload = await request.json()
    raw_path = payload.get("path", "")
    if not raw_path:
        return JSONResponse({"status": "error", "message": "No file path provided"}, status_code=400)

    target = _resolve_safe_path(raw_path)
    if target is None:
        logger.warning("[NativeLauncher] Rejected path outside workspace: %s", raw_path)
        return JSONResponse(
            {"status": "error", "message": "Access denied: path is outside the Makima workspace."},
            status_code=403,
        )

    if not target.exists():
        return JSONResponse({"status": "error", "message": f"File not found: {target.name}"}, status_code=404)

    try:
        def _open_native() -> None:
            if sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target)], check=False)

        await asyncio.to_thread(_open_native)
        logger.info("[NativeLauncher] Opened local document: %s", target)
        return {"status": "ok", "opened": str(target), "filename": target.name}
    except Exception as e:
        logger.error("[NativeLauncher] Failed to open file %s: %s", target, e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/download-file")
async def download_local_file(path: str):
    """Serves a generated document for direct browser download."""
    target = _resolve_safe_path(path)
    if target is None:
        logger.warning("[DownloadEndpoint] Rejected path outside workspace: %s", path)
        raise HTTPException(status_code=403, detail="Access denied: path is outside the Makima workspace.")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


# ---------------------------------------------------------------------------
# OAuth 2.0 Routes
# ---------------------------------------------------------------------------
_oauth_manager: Any = None


def _get_oauth() -> Any:
    global _oauth_manager
    if _oauth_manager is None:
        from .auth.oauth_manager import OAuthManager
        _oauth_manager = OAuthManager()
    return _oauth_manager


@app.get("/auth/login/{provider}")
async def oauth_login(provider: str):
    try:
        url = _get_oauth().get_auth_url(provider)
        return RedirectResponse(url=url)
    except ValueError as ve:
        return JSONResponse(status_code=400, content={"error": str(ve)})
    except OSError as ee:
        return JSONResponse(status_code=503, content={"error": str(ee)})
    except Exception as exc:
        logger.error("[auth] /auth/login/%s error: %s", provider, exc)
        return JSONResponse(status_code=500, content={"error": "Internal error starting OAuth flow."})


@app.get("/auth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        logger.warning("[auth] OAuth callback received error: %s", error)
        html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
        <title>Makima — Connection Failed</title>
        <style>body{{font-family:system-ui;background:#0f0f1a;color:#f87171;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
        .card{{text-align:center;padding:40px 60px;border:1px solid #7f1d1d;
        border-radius:16px;background:#1a0a0a}}</style></head><body>
        <div class="card"><h1>✗ Connection Failed</h1><p>{error}</p>
        <script>setTimeout(()=>window.close(),4000);</script></div></body></html>"""
        return HTMLResponse(content=html, status_code=400)

    if not code or not state:
        return JSONResponse(status_code=400, content={"error": "Missing code or state parameter."})

    html = await _get_oauth().handle_callback(code, state)
    return HTMLResponse(content=html)


@app.get("/auth/status")
async def oauth_status():
    return {"providers": _get_oauth().status()}


@app.delete("/auth/disconnect/{provider}")
async def oauth_disconnect(provider: str):
    try:
        _get_oauth().disconnect(provider)
        return {"ok": True, "message": f"Disconnected from {provider}."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/settings")
async def settings_snapshot():
    speech = _modules.get("speech")
    ollama = _modules.get("ollama")
    store = _modules.get("settings_store")
    general = store.get_settings() if store else {}
    # Prefer live speech engine state, fall back to persisted store value
    wake_enabled = getattr(speech, "wake_word_enabled", None)
    if wake_enabled is None:
        wake_enabled = bool(general.get("wake_word_enabled", False))
    return {
        "voice": {
            "wake_word_enabled": bool(wake_enabled),
            "ptt_key": getattr(speech, "ptt_key", "") or str(general.get("ptt_key", "")),
        },
        "ollama": {"default_model": getattr(ollama, "default_model", "")},
        "general": general,
    }


@app.post("/settings")
async def update_settings(request: Request):
    payload = await request.json()
    speech = _modules.get("speech")
    store = _modules.get("settings_store")
    if speech:
        if "wake_word_enabled" in payload:
            await speech.set_wake_word_enabled(bool(payload["wake_word_enabled"]))
        if "ptt_key" in payload:
            speech.ptt_key = str(payload["ptt_key"])
    if store:
        # Persist wake_word_enabled + ptt_key too (so they survive restart)
        if "wake_word_enabled" in payload:
            store.update_settings({"wake_word_enabled": bool(payload["wake_word_enabled"])})
        if "ptt_key" in payload:
            store.update_settings({"ptt_key": str(payload["ptt_key"])})
        general_keys = {k: v for k, v in payload.items() if k not in ("wake_word_enabled", "ptt_key")}
        if general_keys:
            store.update_settings(general_keys)
    snap = await settings_snapshot()
    snap["ok"] = True
    return snap


# ---------------------------------------------------------------------------
# Tools & MCP REST Endpoints
# ---------------------------------------------------------------------------

async def _cleanup_mcp_runtime(tool_registry: Any) -> None:
    """Tear down live MCP server handles so they can be re-registered."""
    servers = list(getattr(tool_registry, "_mcp_servers", None) or [])
    for server in servers:
        try:
            if hasattr(server, "cleanup"):
                await server.cleanup()
            elif hasattr(server, "stop"):
                await server.stop()
        except Exception as cleanup_err:
            logger.warning("MCP runtime cleanup error: %s", cleanup_err)
    legacy = list(getattr(tool_registry, "_mcp_multiplexers", None) or [])
    for client in legacy:
        try:
            if hasattr(client, "stop"):
                await client.stop()
            elif hasattr(client, "cleanup"):
                await client.cleanup()
        except Exception as legacy_err:
            logger.warning("Legacy MCP runtime cleanup error: %s", legacy_err)
    if hasattr(tool_registry, "_mcp_servers"):
        tool_registry._mcp_servers = []
    if hasattr(tool_registry, "_mcp_multiplexers"):
        tool_registry._mcp_multiplexers = []


def _get_effective_mcp_servers() -> list[dict]:
    """Merge static config mcp_servers with UI-persisted store entries (store wins by name)."""
    config_entries = list((CONFIG or {}).get("mcp_servers") or [])
    store = _modules.get("settings_store")
    store_entries: list[dict] = []
    if store:
        try:
            raw = store.get_settings().get("mcp_servers") or []
            if isinstance(raw, list):
                store_entries = [e for e in raw if isinstance(e, dict)]
        except Exception:
            store_entries = []
    store_by_name = {
        str(s.get("name", "")).strip(): s
        for s in store_entries
        if str(s.get("name", "")).strip()
    }
    merged: list[dict] = []
    seen: set[str] = set()
    for entry in config_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name and name in store_by_name:
            merged.append(store_by_name[name])
            seen.add(name)
        else:
            merged.append(entry)
            if name:
                seen.add(name)
    for name, entry in store_by_name.items():
        if name not in seen:
            merged.append(entry)
    return merged


async def _reload_mcp_servers() -> dict[str, Any]:
    """Cleanup live MCP handles and re-register from effective config+store list."""
    tool_registry = _modules.get("tool_registry")
    if not tool_registry:
        return {"ok": False, "error": "Tool registry not available."}
    await _cleanup_mcp_runtime(tool_registry)
    effective = _get_effective_mcp_servers()
    enabled_entries = [e for e in effective if e.get("enabled", True)]
    if enabled_entries:
        from .core.tool_loader import register_mcp_tools
        await register_mcp_tools(tool_registry, enabled_entries)
    mcp_tool_count = len([
        t for t in tool_registry.list_tools()
        if (t.category or "") == "mcp" or str(t.name, "").startswith("mcp_")
    ])
    return {
        "ok": True,
        "servers_configured": len(effective),
        "servers_enabled": len(enabled_entries),
        "mcp_tools_registered": mcp_tool_count,
    }


@app.get("/tools")
async def list_tools(category: str = "", enabled_only: bool = False):
    """List registered tools with enable state and metadata (for Tools & MCP tab)."""
    tool_registry = _modules.get("tool_registry")
    if not tool_registry:
        return {"ok": False, "error": "Tool registry not available.", "tools": []}
    try:
        tools_meta = tool_registry.list_tools()
    except Exception as e:
        return {"ok": False, "error": str(e), "tools": []}
    cat_filter = category.strip().lower()
    result = []
    for t in tools_meta:
        t_cat = (t.category or "general").strip().lower()
        if cat_filter and t_cat != cat_filter:
            continue
        if enabled_only and not getattr(t, "enabled", True):
            continue
        result.append({
            "name": t.name,
            "description": (t.description or "").split("\n")[0].strip(),
            "category": t_cat,
            "enabled": bool(getattr(t, "enabled", True)),
            "is_destructive": bool(getattr(t, "is_destructive", False)),
            "priority": int(getattr(t, "priority", 5)),
            "source": "mcp" if (t_cat == "mcp" or str(t.name).startswith("mcp_")) else "native",
        })
    result.sort(key=lambda x: (x["category"], x["name"]))
    categories = sorted({t["category"] for t in result})
    return {
        "ok": True,
        "total": len(result),
        "enabled_count": sum(1 for t in result if t["enabled"]),
        "categories": categories,
        "tools": result,
    }


@app.post("/tools/{tool_name}/enable")
async def set_tool_enabled(tool_name: str, request: Request):
    """Enable/disable a registered tool and persist the override to settings store."""
    tool_registry = _modules.get("tool_registry")
    store = _modules.get("settings_store")
    if not tool_registry:
        return {"ok": False, "error": "Tool registry not available."}
    payload = await request.json()
    enabled = bool(payload.get("enabled", True))
    if not tool_registry.has_tool(tool_name):
        return {"ok": False, "error": f"Tool '{tool_name}' not found."}
    changed = tool_registry.set_tool_enabled(tool_name, enabled)
    if not changed:
        return {"ok": False, "error": f"Failed to update tool '{tool_name}'."}
    if store:
        try:
            current = store.get_settings().get("enabled_tools") or {}
            if not isinstance(current, dict):
                current = {}
            current[tool_name] = enabled
            store.update_settings({"enabled_tools": current})
        except Exception as store_err:
            logger.warning("Failed to persist enabled_tools override: %s", store_err)
    return {"ok": True, "name": tool_name, "enabled": enabled}


@app.get("/mcp")
async def list_mcp_servers():
    """List configured MCP servers (config + store) with live registration status."""
    tool_registry = _modules.get("tool_registry")
    effective = _get_effective_mcp_servers()
    live_mcp_tool_names: set[str] = set()
    if tool_registry:
        try:
            live_mcp_tool_names = {
                t.name for t in tool_registry.list_tools()
                if (t.category or "") == "mcp" or str(t.name).startswith("mcp_")
            }
        except Exception:
            live_mcp_tool_names = set()
    servers = []
    for entry in effective:
        name = str(entry.get("name", "")).strip() or "unnamed"
        transport = str(entry.get("transport") or ("stdio" if entry.get("command") else "http")).lower()
        has_command = bool(entry.get("command"))
        has_url = bool(entry.get("url"))
        prefix = f"mcp_{name}_"
        tool_count = sum(1 for n in live_mcp_tool_names if n.startswith(prefix) or n == prefix.rstrip("_"))
        servers.append({
            "name": name,
            "enabled": bool(entry.get("enabled", True)),
            "transport": transport,
            "command": entry.get("command") if has_command else None,
            "url": entry.get("url") if has_url else None,
            "has_env": bool(entry.get("env")),
            "has_headers": bool(entry.get("headers")),
            "tools_registered": tool_count,
            "live": tool_count > 0,
        })
    return {
        "ok": True,
        "servers": servers,
        "total_mcp_tools": len(live_mcp_tool_names),
    }


@app.post("/mcp")
async def add_mcp_server(request: Request):
    """Add (or update by name) an MCP server entry in settings store and hot-reload."""
    store = _modules.get("settings_store")
    if not store:
        return {"ok": False, "error": "Settings store not available."}
    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "MCP server 'name' is required."}
    command = payload.get("command")
    url = payload.get("url")
    if not command and not url:
        return {"ok": False, "error": "Provide either 'command' (stdio) or 'url' (http/sse)."}
    entry: dict[str, Any] = {
        "name": name,
        "enabled": bool(payload.get("enabled", True)),
        "transport": str(payload.get("transport") or ("stdio" if command else "http")).lower(),
    }
    if command:
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
            return {"ok": False, "error": "'command' must be a string or list of strings."}
        entry["command"] = command
    if url:
        entry["url"] = str(url).strip()
    env = payload.get("env")
    if isinstance(env, dict) and env:
        entry["env"] = {str(k): str(v) for k, v in env.items()}
    headers = payload.get("headers")
    if isinstance(headers, dict) and headers:
        entry["headers"] = {str(k): str(v) for k, v in headers.items()}

    try:
        current = store.get_settings().get("mcp_servers") or []
        if not isinstance(current, list):
            current = []
        current = [e for e in current if isinstance(e, dict) and str(e.get("name", "")).strip() != name]
        current.append(entry)
        store.update_settings({"mcp_servers": current})
    except Exception as store_err:
        return {"ok": False, "error": f"Failed to persist MCP server: {store_err}"}

    reload_result = await _reload_mcp_servers()
    return {"ok": True, "server": entry, "reload": reload_result}


@app.delete("/mcp/{server_name}")
async def delete_mcp_server(server_name: str):
    """Remove an MCP server entry from settings store and hot-reload."""
    store = _modules.get("settings_store")
    if not store:
        return {"ok": False, "error": "Settings store not available."}
    name = server_name.strip()
    try:
        current = store.get_settings().get("mcp_servers") or []
        if not isinstance(current, list):
            current = []
        remaining = [e for e in current if isinstance(e, dict) and str(e.get("name", "")).strip() != name]
        if len(remaining) == len(current):
            return {"ok": False, "error": f"MCP server '{name}' not found in store."}
        store.update_settings({"mcp_servers": remaining})
    except Exception as store_err:
        return {"ok": False, "error": f"Failed to update MCP servers: {store_err}"}

    reload_result = await _reload_mcp_servers()
    return {"ok": True, "removed": name, "reload": reload_result}


@app.post("/mcp/reload")
async def reload_mcp_servers():
    """Hot-reload all MCP servers (cleanup + re-register) without brain restart."""
    reload_result = await _reload_mcp_servers()
    return reload_result


# ---------------------------------------------------------------------------
# LLM Providers REST Endpoints
# ---------------------------------------------------------------------------
_PROVIDER_CATALOG = [
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "preset_models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4o",
        ],
        "env_keys": ["OPENROUTER_API_KEY", "MAKIMA_OPENROUTER_KEY"],
        "base_url": "https://openrouter.ai/api/v1",
        "local": False,
        "capabilities": {"text": True, "image": True, "audio": False, "video": False},
    },
    {
        "id": "qwen",
        "name": "Qwen (DashScope)",
        "default_model": "qwen3.8-27b",
        "preset_models": [
            "qwen3.8-27b",
            "qwen-plus",
            "qwen-max",
            "qwen-turbo",
            "qwen2.5-coder-32b-instruct",
            "qwen3.8-flash",
        ],
        "env_keys": ["DASHSCOPE_API_KEY", "MAKIMA_DASHSCOPE_KEY", "QWEN_API_KEY"],
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "local": False,
        "capabilities": {"text": True, "image": True, "audio": True, "video": False},
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "default_model": "deepseek-chat",
        "preset_models": [
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        "env_keys": ["DEEPSEEK_API_KEY", "MAKIMA_DEEPSEEK_KEY"],
        "base_url": "https://api.deepseek.com",
        "local": False,
        "capabilities": {"text": True, "image": False, "audio": False, "video": False},
    },
    {
        "id": "groq",
        "name": "Groq",
        "default_model": "openai/gpt-oss-120b",
        "preset_models": [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
            "groq/compound-mini",
            "allam-2-7b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ],
        "env_keys": ["GROQ_API_KEY", "MAKIMA_GROQ_KEY"],
        "base_url": "https://api.groq.com/openai/v1",
        "local": False,
        "capabilities": {"text": True, "image": False, "audio": True, "video": False},
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "default_model": "gemini-2.5-flash",
        "preset_models": [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ],
        "env_keys": ["GEMINI_API_KEY", "GOOGLE_API_KEY", "MAKIMA_GEMINI_KEY"],
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "local": False,
        "capabilities": {"text": True, "image": True, "audio": True, "video": True},
    },
    {
        "id": "ollama",
        "name": "Ollama (Local)",
        "default_model": "llama3.2",
        "preset_models": [
            "llama3.2",
            "qwen2.5-coder:7b",
            "deepseek-r1:8b",
            "mistral",
            "phi3",
        ],
        "env_keys": [],
        "base_url": "http://localhost:11434",
        "local": True,
        "capabilities": {"text": True, "image": True, "audio": False, "video": False},
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "default_model": "gpt-4o",
        "preset_models": [
            "gpt-4o",
            "gpt-4o-mini",
            "o3-mini",
            "o1",
        ],
        "env_keys": ["OPENAI_API_KEY", "MAKIMA_OPENAI_KEY"],
        "base_url": "https://api.openai.com/v1",
        "local": False,
        "capabilities": {"text": True, "image": True, "audio": True, "video": False},
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "default_model": "claude-3-7-sonnet-20250219",
        "preset_models": [
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        "env_keys": ["ANTHROPIC_API_KEY", "MAKIMA_ANTHROPIC_KEY"],
        "base_url": "https://api.anthropic.com/v1",
        "local": False,
        "capabilities": {"text": True, "image": True, "audio": False, "video": False},
    },
    {
        "id": "huggingface",
        "name": "Hugging Face",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "preset_models": [
            "meta-llama/Llama-3.3-70B-Instruct",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
            "mistralai/Mistral-7B-Instruct-v0.3",
        ],
        "env_keys": ["HF_TOKEN", "HUGGINGFACE_API_KEY", "MAKIMA_HF_KEY"],
        "base_url": "https://router.huggingface.co/v1",
        "local": False,
        "capabilities": {"text": True, "image": False, "audio": False, "video": False},
    },
    {
        "id": "cerebras",
        "name": "Cerebras",
        "default_model": "llama-3.3-70b",
        "preset_models": [
            "llama-3.3-70b",
            "llama3.1-8b",
        ],
        "env_keys": ["CEREBRAS_API_KEY", "MAKIMA_CEREBRAS_KEY"],
        "base_url": "https://api.cerebras.ai/v1",
        "local": False,
        "capabilities": {"text": True, "image": False, "audio": False, "video": False},
    },
]


_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL_S = 300.0  # 5 minutes cache


async def _fetch_live_models_for_provider(spec: dict[str, Any], api_key: str, base_url: str, ai_handler: Any = None) -> list[str]:
    """Dynamically query the provider's live models endpoint if reachable, using preset_models as baseline."""
    pid = spec["id"]
    now = time.time()
    if pid in _MODELS_CACHE:
        cached_time, cached_models = _MODELS_CACHE[pid]
        if (now - cached_time) < _CACHE_TTL_S and cached_models:
            return cached_models

    presets = list(spec.get("preset_models") or ([spec["default_model"]] if spec.get("default_model") else []))
    models = list(presets)
    client = ai_handler._get_http_client() if (ai_handler and hasattr(ai_handler, "_get_http_client")) else None
    own_client = False
    if client is None:
        import httpx
        client = httpx.AsyncClient(timeout=3.0)
        own_client = True

    try:
        if pid == "openrouter":
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                fetched = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
                if fetched:
                    models = list(dict.fromkeys(presets + fetched))
                    _MODELS_CACHE[pid] = (now, models)
                    return models

        elif pid == "ollama":
            url = f"{base_url.rstrip('/')}/api/tags"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                fetched = [m["name"] for m in data.get("models", []) if isinstance(m, dict) and "name" in m]
                if fetched:
                    models = list(dict.fromkeys(presets + fetched))
                    _MODELS_CACHE[pid] = (now, models)
                    return models

        elif api_key and pid in ("qwen", "groq", "deepseek", "openai", "cerebras", "huggingface"):
            endpoint = f"{base_url.rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = await client.get(endpoint, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                fetched = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
                if fetched:
                    models = list(dict.fromkeys(presets + fetched))
                    _MODELS_CACHE[pid] = (now, models)
                    return models
    except Exception as e:
        logger.debug("[providers] Live model fetch error for %s: %s", pid, e)
    finally:
        if own_client and client:
            try:
                await client.aclose()
            except Exception as close_err:
                logger.debug("[providers] model client close failed: %s", close_err)

    return models


async def _format_provider_item_async(spec: dict[str, Any], overrides: dict[str, Any], ai_handler: Any) -> dict[str, Any]:
    pid = spec["id"]
    override = overrides.get(pid, {})

    # Determine if configured via env, store override, or local
    configured_key = ""
    active_key = ""
    for k in spec["env_keys"]:
        val = os.environ.get(k, "").strip()
        if val:
            configured_key = k
            active_key = val
            break

    if not active_key and override.get("api_key"):
        active_key = override["api_key"].strip()

    # Check if active in ai_handler runtime
    _BACKEND_ID_MAP = {
        "anthropic": "claude",
        "qwen": "qwen_flash",
    }
    target_backend_id = _BACKEND_ID_MAP.get(pid, pid)
    backend_prof = None
    if ai_handler and hasattr(ai_handler, "backends"):
        backend_prof = ai_handler.backends.get(target_backend_id) or ai_handler.backends.get(pid)

    if not active_key and backend_prof and backend_prof.api_key:
        active_key = backend_prof.api_key
        configured_key = "smart_sniffer"

    is_configured = False
    key_hint = ""
    if spec["local"]:
        is_configured = True
        key_hint = "Local private runtime"
    elif configured_key:
        is_configured = True
        key_hint = f"Configured in .env ({configured_key})" if configured_key != "smart_sniffer" else "Active (Detected by Smart Sniffer)"
    elif override.get("api_key"):
        is_configured = True
        key_hint = "Stored in user settings"
    elif spec["env_keys"]:
        key_hint = f"Set {spec['env_keys'][0]} in .env or enter key"

    # Current model
    active_model = override.get("model") or ""
    if not active_model and backend_prof and backend_prof.model:
        active_model = backend_prof.model
    if not active_model:
        active_model = spec["default_model"]

    # Base URL
    base_url = override.get("base_url") or spec["base_url"]

    # Enabled
    is_enabled = override.get("enabled")
    if is_enabled is None:
        is_enabled = is_configured

    # Live models query
    live_models = await _fetch_live_models_for_provider(spec, active_key, base_url, ai_handler)

    return {
        "id": pid,
        "name": spec["name"],
        "model": active_model,
        "models": live_models,
        "enabled": bool(is_enabled),
        "configured": bool(is_configured),
        "keyHint": key_hint,
        "baseUrl": base_url,
        "local": spec["local"],
        "capabilities": spec["capabilities"],
    }


def _format_provider_item(spec: dict[str, Any], overrides: dict[str, Any], ai_handler: Any) -> dict[str, Any]:
    pid = spec["id"]
    override = overrides.get(pid, {})
    active_model = override.get("model") or ""
    if not active_model and ai_handler and hasattr(ai_handler, "backends"):
        target_prof = ai_handler.backends.get(pid)
        if target_prof and target_prof.model:
            active_model = target_prof.model
    if not active_model:
        active_model = spec.get("default_model", "")

    presets = spec.get("preset_models") or ([spec["default_model"]] if spec.get("default_model") else [])
    cached_models = _MODELS_CACHE.get(pid, (0, []))[1] or presets
    models_list = list(dict.fromkeys([active_model] + cached_models)) if active_model else cached_models
    if not models_list and spec.get("default_model"):
        models_list = [spec["default_model"]]

    return {
        "id": pid,
        "name": spec["name"],
        "model": active_model,
        "models": models_list,
        "enabled": bool(override.get("enabled", True)),
        "configured": True,
        "keyHint": "",
        "baseUrl": override.get("base_url") or spec.get("base_url", ""),
        "local": spec.get("local", False),
        "capabilities": spec.get("capabilities", {}),
    }


@app.get("/llm/providers")
async def list_llm_providers():
    store = _modules.get("settings_store")
    ai_handler = _modules.get("ai_handler")
    overrides = store.get_llm_overrides() if store else {}
    tasks = [_format_provider_item_async(p, overrides, ai_handler) for p in _PROVIDER_CATALOG]
    results = await asyncio.gather(*tasks)

    # Determine active provider in runtime
    active_pid = getattr(ai_handler, "default_provider", None) or (store.get_settings().get("default_llm_backend") if store else None) or "groq"
    _INV_BACKEND_ID_MAP = {
        "claude": "anthropic",
        "deepseek_v32": "deepseek",
        "qwen_flash": "qwen",
    }
    catalog_active_id = _INV_BACKEND_ID_MAP.get(active_pid, active_pid)
    for r in results:
        r["isActive"] = (r["id"] == catalog_active_id)

    return {"providers": results, "active_provider": catalog_active_id}


@app.post("/llm/providers/{provider_id}")
async def save_llm_provider_config(provider_id: str, request: Request):
    payload = await request.json()
    store = _modules.get("settings_store")
    ai_handler = _modules.get("ai_handler")

    spec = next((p for p in _PROVIDER_CATALOG if p["id"] == provider_id), None)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")

    if store:
        # Normalize camelCase keys from frontend to snake_case before persisting
        normalized_payload = {
            "api_key": payload.get("apiKey") or payload.get("api_key") or "",
            "model": payload.get("model") or "",
            "base_url": payload.get("baseUrl") or payload.get("base_url") or "",
            "enabled": payload.get("enabled", True),
        }
        store.save_llm_provider(provider_id, normalized_payload)
        if hasattr(store, "update_settings"):
            store.update_settings({"default_llm_backend": provider_id})

    # If an API key or base URL was provided, update os.environ and ai_handler backend
    api_key = payload.get("apiKey", "").strip()
    if api_key and spec["env_keys"]:
        os.environ[spec["env_keys"][0]] = api_key

    model = payload.get("model", "").strip()
    base_url = payload.get("baseUrl", "").strip()

    # Map UI catalog provider_id to internal backend key if aliased
    _BACKEND_ID_MAP = {
        "anthropic": "claude",
        "qwen": "qwen_flash",
    }
    target_backend_id = _BACKEND_ID_MAP.get(provider_id, provider_id)

    if ai_handler and hasattr(ai_handler, "backends"):
        target_profile = ai_handler.backends.get(target_backend_id) or ai_handler.backends.get(provider_id)
        if target_profile:
            if model:
                target_profile.model = model
            if api_key:
                target_profile.api_key = api_key
            if base_url:
                target_profile.base_url = base_url
            if "enabled" in payload:
                target_profile.enabled = bool(payload["enabled"])
        else:
            # Dynamically instantiate BackendProfile for newly configured provider
            from .ai_handler import BackendProfile, CircuitBreaker
            adapter_type = "gemini" if target_backend_id == "gemini" else ("ollama" if target_backend_id == "ollama" else "openai")
            target_profile = BackendProfile(
                name=target_backend_id,
                enabled=bool(payload.get("enabled", True)),
                adapter_type=adapter_type,
                api_key=api_key or os.environ.get(spec["env_keys"][0] if spec["env_keys"] else "", ""),
                model=model or spec.get("default_model", ""),
                base_url=base_url or spec.get("base_url", ""),
                circuit_breaker=CircuitBreaker(max_failures=5, cooldown_s=15),
            )
            ai_handler.backends[target_backend_id] = target_profile
            ai_handler.backends[provider_id] = target_profile

        # Immediately switch active provider in AIHandler so subsequent requests use it
        if hasattr(ai_handler, "set_active_provider"):
            ai_handler.set_active_provider(target_backend_id)

    overrides = store.get_llm_overrides() if store else {}
    return {"provider": _format_provider_item(spec, overrides, ai_handler)}



# ---------------------------------------------------------------------------
# Media Store REST Endpoints
# ---------------------------------------------------------------------------
@app.post("/media/upload")
async def upload_media(file: UploadFile):
    store: MediaStore | None = _modules.get("media_store")
    if not store:
        raise HTTPException(status_code=503, detail="Media store not initialized")
    try:
        data = store.save_upload_file(file.file, file.filename or "file", file.content_type or "application/octet-stream")
        return data
    except MediaValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}") from e


@app.get("/media")
async def list_media_library(kind: str | None = None, q: str | None = None):
    store: MediaStore | None = _modules.get("media_store")
    if not store:
        return {"media": []}
    return {"media": store.list(kind=kind, query=q)}


@app.get("/media/{media_id}")
async def get_media_metadata(media_id: str):
    store: MediaStore | None = _modules.get("media_store")
    if not store:
        raise HTTPException(status_code=503, detail="Media store not initialized")
    try:
        return store.get(media_id)
    except MediaNotFoundError as e:
        raise HTTPException(status_code=404, detail="Media not found") from e


@app.get("/media/{media_id}/content")
async def get_media_content(media_id: str):
    store: MediaStore | None = _modules.get("media_store")
    if not store:
        raise HTTPException(status_code=503, detail="Media store not initialized")
    try:
        item = store.get(media_id)
        path = store.file_path(media_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Media file missing")
        return FileResponse(str(path), media_type=item.get("mime_type", "application/octet-stream"), filename=item.get("name"))
    except MediaNotFoundError as e:
        raise HTTPException(status_code=404, detail="Media not found") from e


@app.get("/media/{media_id}/download")
async def download_media(media_id: str):
    """Attachment-disposition alias of /content — used by the UI download buttons."""
    store: MediaStore | None = _modules.get("media_store")
    if not store:
        raise HTTPException(status_code=503, detail="Media store not initialized")
    try:
        item = store.get(media_id)
        path = store.file_path(media_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Media file missing")
        return FileResponse(
            str(path),
            media_type=item.get("mime_type", "application/octet-stream"),
            filename=item.get("name"),
            content_disposition_type="attachment",
        )
    except MediaNotFoundError as e:
        raise HTTPException(status_code=404, detail="Media not found") from e


@app.delete("/media/{media_id}")
async def delete_media_entry(media_id: str):
    store: MediaStore | None = _modules.get("media_store")
    if not store:
        raise HTTPException(status_code=503, detail="Media store not initialized")
    try:
        store.delete(media_id)
        return {"deleted": True, "id": media_id}
    except MediaNotFoundError as e:
        raise HTTPException(status_code=404, detail="Media not found") from e


@app.get("/voice/audio/{artifact_id}")
async def get_voice_audio(artifact_id: str):
    speech = _modules.get("voice") or _modules.get("speech") or _modules.get("voice_pipeline")
    if not speech:
        raise HTTPException(status_code=503, detail="Voice pipeline not initialized")
    artifact = getattr(speech, "get_tts_artifact", lambda aid: None)(artifact_id)
    if not artifact or not getattr(artifact, "path", None) or not artifact.path.exists():
        raise HTTPException(status_code=404, detail="Audio artifact not found or expired")
    return FileResponse(str(artifact.path), media_type=getattr(artifact, "mime_type", "audio/wav"))


# ---------------------------------------------------------------------------
# WebSocket Endpoint & Router Message Dispatch
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Main WebSocket endpoint — all UI <-> Brain communication flows here."""
    await ws.accept()
    _ws_clients.add(ws)
    logger.info(f"WS client connected. Total clients: {len(_ws_clients)}")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = WSMessage.from_json(raw)
            except Exception as e:
                await ws.send_text(f'{{"error": "Invalid message: {e}"}}')
                continue

            # Spawn handler asynchronously so the WebSocket receive loop stays responsive (never blocked)
            ws_task = asyncio.create_task(_handle_ws_message(msg, ws))
            _active_ws_tasks.add(ws_task)
            ws_task.add_done_callback(_active_ws_tasks.discard)

    except WebSocketDisconnect:
        logger.info("WS client disconnected.")
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        _ws_clients.discard(ws)
        dead_tasks = [t for t, s in list(_task_to_ws.items()) if s == ws]
        for dt in dead_tasks:
            _task_to_ws.pop(dt, None)


async def _handle_ws_message(msg: Any, ws: WebSocket) -> None:
    """Route incoming WS messages to OrchestrationEngine and services."""
    try:
        router: Any = _modules.get("orchestration_engine") or _modules.get("router")
        messaging: Any = _modules.get("messaging")
        speech: Any = _modules.get("voice") or _modules.get("speech") or _modules.get("voice_pipeline")
        store: Any = _modules.get("settings_store")

        msg_type = msg.type if hasattr(msg, "type") else ""
        payload = msg.payload if hasattr(msg, "payload") else {}
        task_id = getattr(msg, "task_id", None) or generate_task_id()
        if task_id:
            _task_to_ws[task_id] = ws

        if msg_type in (ClientMessageType.PING.value, "ping"):
            ping_ts = payload.get("ts") or payload.get("timestamp") or getattr(msg, "timestamp", None) or time.time()
            await ws_broadcast(build_pong(ping_ts))
            # Pong carries no task_id (global broadcast) — drop the routing
            # entry registered above so repeated pings cannot accumulate.
            _task_to_ws.pop(task_id, None)

        elif msg_type in (ClientMessageType.PONG.value, "pong"):
            pass

        elif msg_type == ClientMessageType.USER_MESSAGE.value:
            text = payload.get("text", "").strip()
            logger.info("[WS] Inbound user_message (task_id=%s, len=%d): %s", task_id, len(text), text[:80])
            if not text:
                await ws_broadcast(build_ai_chunk(task_id, "Please enter a message.", is_final=True))
                return
            context = {k: v for k, v in payload.items() if k != "text"}
            if "conversation_id" not in context or not context["conversation_id"]:
                context["conversation_id"] = "default_session"
            if payload.get("attachments"):
                context["attachments"] = payload.get("attachments")
            if payload.get("file_name") and payload.get("file_data"):
                multimodal = _modules.get("multimodal")
                if multimodal and hasattr(multimodal, "process_file"):
                    file_context = await multimodal.process_file(
                        payload["file_name"],
                        payload.get("file_mime", "application/octet-stream"),
                        payload["file_data"],
                    )
                    context["document"] = file_context
            if router:
                await router.handle_message(task_id, text, context=context)
            else:
                logger.error("OrchestrationEngine not available to handle message!")
                await ws_broadcast(build_ai_chunk(task_id, "Error: Makima brain orchestration engine is not ready.", is_final=True))

        elif msg_type == ClientMessageType.REGENERATE_MESSAGE.value:
            text = payload.get("text", "").strip()
            if not text:
                await ws_broadcast(build_ai_chunk(task_id, "Please enter a message to regenerate.", is_final=True))
                return
            context = {k: v for k, v in payload.items() if k != "text"}
            context["is_regenerate"] = True
            if "conversation_id" not in context or not context["conversation_id"]:
                context["conversation_id"] = "default_session"
            if router:
                await router.handle_message(task_id, text, context=context)
            else:
                logger.error("OrchestrationEngine not available to handle message!")
                await ws_broadcast(build_ai_chunk(task_id, "Error: Makima brain orchestration engine is not ready.", is_final=True))

        elif msg_type == ClientMessageType.MODIFY_RESPONSE.value:
            text = payload.get("text", "").strip()
            instruction = payload.get("instruction", "").strip()
            if not text:
                await ws_broadcast(build_ai_chunk(task_id, "Please enter a message to modify.", is_final=True))
                return
            modified_text = f"{text}\n\n[Instruction: {instruction}]" if instruction else text
            context = {k: v for k, v in payload.items() if k not in ("text", "instruction")}
            context["instruction"] = instruction
            if "conversation_id" not in context or not context["conversation_id"]:
                context["conversation_id"] = "default_session"
            if router:
                await router.handle_message(task_id, modified_text, context=context)
            else:
                logger.error("OrchestrationEngine not available to handle message!")
                await ws_broadcast(build_ai_chunk(task_id, "Error: Makima brain orchestration engine is not ready.", is_final=True))

        elif msg_type in (ClientMessageType.CANCEL_TASK.value, "cancel_task", "abort"):
            if router:
                if msg_type == "abort":
                    active_tasks = list(getattr(router, "_active_tasks", []))
                    for at in active_tasks:
                        await router.cancel_task(at)
                await router.cancel_task(task_id)

        elif msg_type == ClientMessageType.APPROVE_ACTION.value:
            action = payload.get("action", "")
            from .core.confirmations import ActionConfirmationManager as BaseAgent
            agent_confirm_id = f"{task_id}_{action}" if action else task_id
            resolved = await BaseAgent.resolve_confirmation(agent_confirm_id, approved=True)
            if not resolved and action:
                resolved = await BaseAgent.resolve_confirmation(task_id, approved=True)

            if resolved:
                logger.info("Action '%s' approved for task %s", action, task_id)
                await ws_broadcast(build_ai_chunk(task_id, f" [Action '{action}' approved.] ", is_final=True))
            elif action == "send_message" and messaging:
                result = await messaging.approve_send(task_id)
                await ws_broadcast(build_ai_chunk(task_id, result, is_final=True))
            elif action == "forget_cascade":
                memory_forget = _modules.get("memory_forget")
                if memory_forget and hasattr(memory_forget, "confirm_forget"):
                    result = await memory_forget.confirm_forget(task_id)
                    await ws_broadcast(build_ai_chunk(task_id, result, is_final=True))

        elif msg_type == ClientMessageType.REJECT_ACTION.value:
            action = payload.get("action", "")
            from .core.confirmations import ActionConfirmationManager as BaseAgent
            agent_confirm_id = f"{task_id}_{action}" if action else task_id
            resolved = await BaseAgent.resolve_confirmation(agent_confirm_id, approved=False)
            if not resolved and action:
                resolved = await BaseAgent.resolve_confirmation(task_id, approved=False)

            if resolved:
                logger.info("Action '%s' rejected for task %s", action, task_id)
                await ws_broadcast(build_ai_chunk(task_id, f" [Action '{action}' rejected.] ", is_final=True))
            elif action == "send_message" and messaging:
                result = await messaging.cancel_send(task_id)
                await ws_broadcast(build_ai_chunk(task_id, result, is_final=True))

        elif msg_type == ClientMessageType.FEEDBACK.value:
            pref_eng = _modules.get("preference_engine")
            if pref_eng and hasattr(pref_eng, "record_feedback"):
                rating = payload.get("rating") or payload.get("thumb")
                is_positive = rating not in ("down", "negative", -1) and payload.get("positive", True)
                tool_name = payload.get("tool_name") or payload.get("agent_name", "")
                await pref_eng.record_feedback(
                    tool_name=tool_name,
                    accepted=is_positive,
                    rejected_params=payload.get("rejected_params"),
                    actual_params=payload.get("actual_params"),
                )
                logger.info("User feedback recorded: tool=%s positive=%s", tool_name, is_positive)

        elif msg_type in (ClientMessageType.FOCUS_CHANGE.value, ClientMessageType.SET_FOCUS_PROFILE.value):
            focus_profiles = _modules.get("focus_profiles")
            if focus_profiles:
                profile_name = payload.get("profile", "Work")
                toggles = focus_profiles.apply_profile(profile_name)
                if speech and "wake_word_enabled" in toggles and hasattr(speech, "set_wake_word_enabled"):
                    await speech.set_wake_word_enabled(toggles["wake_word_enabled"])
                logger.info(f"Focus profile changed to: {profile_name}")

        elif msg_type == ClientMessageType.PULL_OLLAMA_MODEL.value:
            ollama = _modules.get("ollama")
            if ollama and hasattr(ollama, "pull_model"):
                result = await ollama.pull_model(payload.get("model_name", ""))
                await ws_broadcast(build_ai_chunk(task_id, result, is_final=True))

        elif msg_type == ClientMessageType.DELETE_OLLAMA_MODEL.value:
            ollama = _modules.get("ollama")
            if ollama and hasattr(ollama, "delete_model"):
                result = await ollama.delete_model(payload.get("model_name", ""))
                await ws_broadcast(build_ai_chunk(task_id, result, is_final=True))

        # ── Proactive Autonomy Handlers ─────────────────────────────────────
        elif msg_type == ClientMessageType.SET_AUTONOMY_MODE.value:
            proactive = _modules.get("proactive_orchestrator")
            if proactive and hasattr(proactive, "set_mode"):
                result = await proactive.set_mode(payload.get("mode", ""), source="user")
                if not result.get("ok"):
                    await ws_broadcast(build_ai_chunk(task_id, f"Autonomy mode error: {result.get('error')}", is_final=True))
            else:
                await ws_broadcast(build_ai_chunk(task_id, "Proactive orchestrator not available.", is_final=True))

        elif msg_type == ClientMessageType.GET_AUTONOMY_STATUS.value:
            proactive = _modules.get("proactive_orchestrator")
            if proactive and hasattr(proactive, "get_status"):
                from . import ws_protocol as _wsp
                await ws_broadcast(_wsp.WSMessage(
                    v=1,
                    type=_wsp.ServerMessageType.AUTONOMY_MODE_CHANGED,
                    payload={"mode": proactive.mode, "source": "status_query", "status": proactive.get_status()},
                ))

        # ── Voice Hands-Free & Gemini Live Handlers ─────────────────────────
        elif msg_type == ClientMessageType.VOICE_SESSION_START.value:
            if speech:
                voice_session_id = payload.get("voice_session_id") or task_id
                v_settings = dict(payload.get("settings") or {})
                if payload.get("api_key") and "api_key" not in v_settings:
                    v_settings["api_key"] = payload["api_key"]
                if hasattr(speech, "start_voice_session"):
                    task = asyncio.create_task(
                        speech.start_voice_session(
                            voice_session_id,
                            payload.get("conversation_id", ""),
                            v_settings,
                        )
                    )
                    _active_ws_tasks.add(task)
                    task.add_done_callback(_active_ws_tasks.discard)
                elif hasattr(speech, "start_session"):
                    task = asyncio.create_task(speech.start_session(voice_session_id))
                    _active_ws_tasks.add(task)
                    task.add_done_callback(_active_ws_tasks.discard)

        elif msg_type == ClientMessageType.VOICE_AUDIO_UTTERANCE.value:
            if speech:
                audio_b64 = payload.get("audio_data", "")
                if audio_b64:
                    try:
                        pcm_bytes = base64.b64decode(audio_b64)
                    except Exception:
                        pcm_bytes = b""
                    if hasattr(speech, "send_audio") and pcm_bytes:
                        await speech.send_audio(pcm_bytes)
                    elif hasattr(speech, "handle_voice_utterance"):
                        await speech.handle_voice_utterance(payload.get("voice_session_id", ""), task_id, audio_b64, payload.get("sample_rate", 16000))

        elif msg_type == ClientMessageType.VOICE_SESSION_PAUSE.value:
            if speech and hasattr(speech, "pause_voice_session"):
                await speech.pause_voice_session(payload.get("voice_session_id", ""))

        elif msg_type == ClientMessageType.VOICE_SESSION_RESUME.value:
            if speech and hasattr(speech, "resume_voice_session"):
                await speech.resume_voice_session(payload.get("voice_session_id", ""))

        elif msg_type == ClientMessageType.VOICE_SESSION_STOP.value:
            if speech:
                if hasattr(speech, "stop_session"):
                    await speech.stop_session()
                elif hasattr(speech, "stop_voice_session"):
                    await speech.stop_voice_session(payload.get("voice_session_id", ""))

        elif msg_type == ClientMessageType.VOICE_BARGE_IN.value:
            if speech:
                if hasattr(speech, "stop_session"):
                    # Interrupt active playback
                    await ws_broadcast(build_voice_event("voice_tts_stopped", payload.get("voice_session_id", ""), reason="barge_in"))
                elif hasattr(speech, "barge_in"):
                    await speech.barge_in(payload.get("voice_session_id", ""))

        elif msg_type == ClientMessageType.VOICE_SPEAK.value:
            if speech:
                voice_session_id = payload.get("voice_session_id", "")
                speak_text = payload.get("text", "")
                if voice_session_id.startswith("tts_") and hasattr(speech, "synthesize_oneshot_tts"):
                    # One-shot read-aloud: ephemeral tts_* id, no live voice session.
                    await speech.synthesize_oneshot_tts(voice_session_id, speak_text, task_id=task_id)
                elif hasattr(speech, "synthesize_session_tts"):
                    await speech.synthesize_session_tts(voice_session_id, speak_text, task_id=task_id)

        elif msg_type == ClientMessageType.VOICE_TTS_STOP.value:
            if speech and hasattr(speech, "stop_oneshot_tts"):
                await speech.stop_oneshot_tts(payload.get("voice_session_id", ""))
            # Stop request itself registered a task_id — drop its routing entry.
            _task_to_ws.pop(task_id, None)

        elif msg_type == ClientMessageType.PTT_DOWN.value:
            if speech and hasattr(speech, "handle_ptt_down"):
                await speech.handle_ptt_down()

        elif msg_type == ClientMessageType.PTT_UP.value:
            if speech and hasattr(speech, "handle_ptt_up"):
                audio_b64 = payload.get("audio_data", "")
                audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
                await speech.handle_ptt_up(audio_bytes)

        elif msg_type == ClientMessageType.STT_CONFIRM.value:
            if speech and hasattr(speech, "confirm_transcript"):
                await speech.confirm_transcript(task_id)

        elif msg_type == ClientMessageType.STT_CORRECT.value:
            if speech and hasattr(speech, "correct_transcript"):
                corrected_text = payload.get("corrected_text", "")
                await speech.correct_transcript(task_id, corrected_text)

        elif msg_type == ClientMessageType.STT_CANCEL.value:
            if speech and hasattr(speech, "cancel_transcript"):
                await speech.cancel_transcript(task_id)

        # ── Credentials & Settings Handlers ──────────────────────────────────
        elif msg_type == ClientMessageType.SAVE_CREDENTIAL.value:
            if store:
                svc_name = payload.get("service_name", "")
                cfg_data = payload.get("config", {})
                if svc_name and isinstance(cfg_data, dict):
                    # Normalize camelCase UI field names → schema-correct snake_case field names
                    # The UI always sends { apiKey: "..." } but each service has its own field name
                    _FIELD_NORMALIZE: dict[str, dict[str, str]] = {
                        "telegram":  {"apiKey": "bot_token", "api_key": "bot_token"},
                        "discord":   {"apiKey": "bot_token", "api_key": "bot_token"},
                        "github":    {"apiKey": "api_key"},
                        "gdrive":    {"apiKey": "client_token"},
                        "gmail":     {"apiKey": "app_password"},
                        "spotify":   {"apiKey": "client_id"},
                        "slack":     {"apiKey": "webhook_url"},
                        "notion":    {"apiKey": "api_token"},
                        "youtube":   {"apiKey": "api_key"},
                        "figma":     {"apiKey": "personal_token"},
                        "whatsapp":  {"apiKey": "phone", "api_key": "phone"},
                    }
                    field_map = _FIELD_NORMALIZE.get(svc_name, {})
                    normalized: dict[str, str] = {}
                    for k, v in cfg_data.items():
                        mapped_key = field_map.get(k, k)  # translate camelCase → schema key
                        if isinstance(v, str):
                            normalized[mapped_key] = v.strip()
                        else:
                            normalized[mapped_key] = v
                    store.save_integration_fields(svc_name, normalized)

                    # Inject into os.environ so running tools pick up credentials immediately
                    # without requiring a brain restart — map each integration to its env var
                    _CRED_ENV_MAP: dict[str, list[tuple[str, str]]] = {
                        "telegram":  [("bot_token", "TELEGRAM_BOT_TOKEN")],
                        "discord":   [("bot_token", "DISCORD_BOT_TOKEN")],
                        "github":    [("api_key", "GITHUB_TOKEN"), ("api_key", "GH_TOKEN")],
                        "gmail":     [("app_password", "GMAIL_APP_PASSWORD")],
                        "youtube":   [("api_key", "YOUTUBE_API_KEY")],
                        "notion":    [("api_token", "NOTION_API_TOKEN")],
                        "figma":     [("personal_token", "FIGMA_PERSONAL_TOKEN")],
                        "slack":     [("webhook_url", "SLACK_WEBHOOK_URL")],
                        "gdrive":    [("client_token", "GDRIVE_CLIENT_TOKEN")],
                        "spotify":   [("client_id", "SPOTIFY_CLIENT_ID")],
                    }
                    for field_name, env_var in _CRED_ENV_MAP.get(svc_name, []):
                        val = normalized.get(field_name, "")
                        if val:
                            os.environ[env_var] = val
                            logger.info("[SAVE_CREDENTIAL] Injected %s into env var %s", svc_name, env_var)

                    await ws_broadcast(WSMessage(
                        v=PROTOCOL_VERSION,
                        type=ServerMessageType.CREDENTIALS_DATA,
                        payload={"status": "saved", "service": svc_name, "fields": list(normalized.keys())},
                        task_id=task_id,
                    ))

        elif msg_type == ClientMessageType.GET_CREDENTIALS.value:
            if store:
                # Return integration credentials (not general settings)
                all_integrations: dict[str, dict] = {}
                for int_id in store.list_integrations():
                    fields = store.get_integration_fields(int_id)
                    # Mask secret values — return only whether a field is set, not the raw value
                    all_integrations[int_id] = {
                        k: ("***" if v else "") for k, v in fields.items()
                    }
                await ws_broadcast(WSMessage(
                    v=PROTOCOL_VERSION,
                    type=ServerMessageType.CREDENTIALS_DATA,
                    payload={"credentials": all_integrations},
                    task_id=task_id,
                ))

        # ── Document & File Launcher Handlers ────────────────────────────────
        elif msg_type in ("open_file", "reveal_file"):
            raw_path = payload.get("path") or payload.get("file_path") or ""
            if raw_path:
                try:
                    clean = re.sub(r"^file:[/\\]+", "", raw_path)
                    if sys.platform == "win32" and re.match(r"^/?[a-zA-Z]:", clean):
                        clean = clean.lstrip("/")
                    target = Path(clean)
                    if not target.is_absolute():
                        for cand in (Path("./makima_workspace/output/temp") / clean, Path("./makima_workspace/output") / clean, target):
                            if cand.exists():
                                target = cand
                                break
                    if target.exists():
                        def _open_ws_native() -> None:
                            if sys.platform == "win32":
                                os.startfile(str(target))  # type: ignore[attr-defined]
                            elif sys.platform == "darwin":
                                subprocess.run(["open", str(target)], check=False)
                            else:
                                subprocess.run(["xdg-open", str(target)], check=False)

                        await asyncio.to_thread(_open_ws_native)
                        logger.info("[WS] Opened local document: %s", target)
                        await ws_broadcast(build_ai_chunk(task_id, f" [Opened {target.name}] ", is_final=True))
                    else:
                        logger.warning("[WS] Document not found to open: %s", target)
                except Exception as ex:
                    logger.error("[WS] Failed to open document: %s", ex)

        else:
            logger.debug(f"Unhandled WS message type: {msg_type}")

    except Exception:
        logger.exception("Error handling WS message")
        try:
            if task_id:
                await ws_broadcast(build_ai_chunk(
                    task_id,
                    "Sorry, I hit an internal error while processing that request. Please try again.",
                    is_final=True,
                ))
        except Exception as broadcast_err:
            logger.debug(f"Failed to broadcast error chunk for task {task_id}: {broadcast_err}")


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    disable_windows_quick_edit()
    if "--check" in sys.argv:
        async def run_boot_check() -> int:
            logger.info("Running Makima Brain boot check (--check)...")
            cfg = _load_config()
            from .core.app_bootstrap import AppBootstrap
            bootstrap = AppBootstrap(config=cfg, ws_broadcast=ws_broadcast)
            await bootstrap.initialize_services()
            ready = bootstrap.is_ready()
            health = bootstrap.get_services_health()
            await bootstrap.shutdown_services()
            if ready:
                print(f"MAKIMA BOOT CHECK: SUCCESS — Core services ready ({health.get('total_services', 0)} registered).")
                return 0
            else:
                print("MAKIMA BOOT CHECK: FAILED — Essential core services not ready.")
                return 1
        code = asyncio.run(run_boot_check())
        sys.exit(code)

    import uvicorn
    try:
        uvicorn.run(
            "apps.brain.main:app",
            host="127.0.0.1",
            port=8080,
            reload=False,
            log_level="info",
        )
    except (KeyboardInterrupt, SystemExit):
        pass
