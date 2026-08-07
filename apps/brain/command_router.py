"""
Makima v7.1 ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â Command Router

Single entry point for every user message (text + voice).
Runs one intent classification LLM call ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ intent schema + confidence score.

Routing rules:
  - Confidence ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â°Ãƒâ€šÃ‚Â¥ 0.6 ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ dispatch to leaf agent
  - Confidence < 0.6 OR intent = multi_step ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ hand to CommanderAgent
  - Trivial commands (time, date) ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ short-circuited without LLM or agents
  - Volume/system commands ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ queued as SYSTEM_CONTROL at CRITICAL priority
  - Priority queue: critical > interactive > background

DEFINITIVE RULE (from architecture doc):
  - CommandRouter receives raw message ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ classifies intent ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ dispatches
  - CommanderAgent is called BY Router ONLY when intent is multi_step
  - They never call each other bidirectionally
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("makima.command_router")


class Priority(int, Enum):
    """Task priority levels. Higher = processed first."""
    BACKGROUND = 0
    INTERACTIVE = 1
    CRITICAL = 2


class Intent(str, Enum):
    """Recognized intent categories."""
    FAST_CHAT = "fast_chat"
    RESEARCH = "research"
    CODE = "code"
    CREATIVE = "creative"
    MEMORY_QUERY = "memory_query"
    MEMORY_FORGET = "memory_forget"
    SYSTEM_CONTROL = "system_control"
    MESSAGING = "messaging"
    MEDIA = "media"
    BROWSER = "browser"            # web browsing, page navigation, data extraction
    VOICE = "voice"                # TTS, wake word, speech settings
    AUTOMATION = "automation"
    DOCUMENT = "document"          # spreadsheets, word docs, pdf reports
    DATA_ANALYSIS = "data_analysis"  # data manipulation, stats, charts (Polars/Pandas)
    SECURITY = "security"           # code/dependency audits, secret scanning, port scanning
    DEVOPS = "devops"               # Docker/K8s, CI/CD pipelines, deployments, infra
    MULTI_STEP = "multi_step"
    TRIVIAL = "trivial"            # volume, time, simple math
    UNKNOWN = "unknown"


# Trivial commands that don't need agents or LLM routing
TRIVIAL_COMMANDS = {
    "time": ["what time is it", "current time", "what's the time", "time kya hai", "kya time ho raha hai", "kitne baje hain"],
    "date": ["what's the date", "today's date", "what date is it", "aaj kya date hai", "aaj ki tarikh", "date kya hai"],
}

# Intent ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ Agent mapping
INTENT_TO_AGENT = {
    Intent.FAST_CHAT: None,              # Direct LLM response, no agent
    Intent.RESEARCH: "research_agent",
    Intent.CODE: "code_agent",
    Intent.CREATIVE: "creative_agent",
    Intent.MEMORY_QUERY: "memory_agent",
    Intent.MEMORY_FORGET: "memory_agent",
    Intent.SYSTEM_CONTROL: "system_agent",
    Intent.MESSAGING: "messaging_agent",
    Intent.MEDIA: "media_agent",
    Intent.BROWSER: "browser_agent",
    Intent.VOICE: "voice_agent",
    Intent.AUTOMATION: "automation_agent",
    Intent.DOCUMENT: "document_agent",
    Intent.DATA_ANALYSIS: "data_analyst_agent",
    Intent.SECURITY: "security_agent",
    Intent.DEVOPS: "devops_agent",
    Intent.MULTI_STEP: None,  # Handled inline via EcosystemHub → direct LLM fallback
}


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: Intent
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""


@dataclass
class TaskItem:
    """A task in the priority queue."""
    task_id: str
    message: str
    intent_result: Optional[IntentResult] = None
    priority: Priority = Priority.INTERACTIVE
    created_at: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)
    
    def __lt__(self, other: "TaskItem") -> bool:
        """Higher priority first, then earlier timestamp."""
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.created_at < other.created_at


INTENT_CLASSIFICATION_PROMPT = """You are an intent classifier for a desktop AI assistant named Makima.
Given a user message (which may be in English, Hindi, or Hinglish) and recent conversation history, classify the intent and extract relevant entities.

Respond ONLY with valid JSON in this exact format:
{{
    "intent": "<one of: fast_chat, research, code, creative, memory_query, memory_forget, system_control, messaging, media, browser, voice, automation, document, data_analysis, security, devops, multi_step, trivial, unknown>",
    "confidence": <float 0.0-1.0>,
    "entities": {{<optional extracted entities like app_name, contact_name, url, etc.>}}
}}

Intent definitions:
- fast_chat: casual conversation, general knowledge, explanations, writing stories, essays, poems, or any pure text generation that doesn't need specialized UI tools (e.g. "tell me a story", "write an essay", "hi", "what is google")
- research: live web search for current news/real-time events or multi-source online research (e.g. "search latest news on X", "find current stock price of Y")
- code: code generation, debugging, review, refactoring
- creative: structured visual generation ONLY like UI Mockups (Tailwind/HTML), Diagrams (Mermaid), or Image Prompts (Midjourney)
- memory_query: asking what is remembered OR explicitly asking to remember/save a fact (e.g. "what do you remember about X", "do you know my Y", "remember that my X is Y", "save this: X", "note that X", "maine kya bola tha")
- memory_forget: "forget X", "delete memory of Y", "ye bhul jao"
- system_control: app launch/close, window management, file operations, OS settings (e.g. "open chrome", "chrome kholo", "close notepad", "close it pls", "isey band karo")
- messaging: send/read messages on WhatsApp/Telegram/Discord/Email (e.g. "send message to X", "X ko message bhejo")
- media: music/video control, Spotify, YouTube (e.g. "play song", "gaana chalao", "next track", "play music", "play X")
- browser: web browsing, opening URLs, navigating websites, filling forms, extracting web page data (e.g. "open google.com", "search on amazon", "fill this form", "scrape this page", "website kholo")
- voice: TTS, speech settings, wake word, read aloud (e.g. "read this aloud", "bol ke sunao", "change voice", "turn off wake word", "aawaz badlo")
- automation: workflows, macros, reminders, scheduled tasks (e.g. "remind me in 5 min", "set a reminder", "run my morning routine")
- document: reading/summarizing/creating spreadsheets, Word docs, PDF reports (e.g. "summarize this pdf", "make a report", "excel file banao")
- data_analysis: analyzing datasets, statistics, generating charts/plots from data (e.g. "analyze this csv", "plot a chart of X", "data ka summary nikaalo", "isme trend dikhao")
- security: code/dependency security audits, secret/vulnerability scanning, port scanning (e.g. "scan this code for vulnerabilities", "check for exposed secrets", "security audit karo", "is this port open")
- devops: Docker/Kubernetes, CI/CD pipelines, deployments, server/infra management (e.g. "deploy this container", "check the pipeline status", "docker restart karo", "kubernetes pods dikhao")
- multi_step: complex tasks requiring multiple agents (e.g. "research X then write code for Y", "google pe research karke pdf bnao", "do X and then Y")
- trivial: time, date, simple math, volume control (e.g. "time kya hai", "aawaz kam karo")
- unknown: can't determine intent

CRITICAL ROUTING RULES:
- DIRECT-LLM-FIRST / AGENT CONSERVATION: If a question (including general facts like 'what is google' or 'what is a quantum computer') can be answered directly by the LLM without physical tool execution or live web browsing, route to 'fast_chat' so specialized agents remain OFF by default.
- "open X.com" / "go to website" / "navigate to URL" -> browser (NOT system_control)
- "close it" / "close this" / "band karo" / "close app" -> system_control (NOT fast_chat)
- "read aloud" / "speak this" / "bol ke sunao" -> voice (NOT media)
- "play song" / "gaana chalao" / "next track" -> media (NOT browser)
- "set reminder" / "yaad dilana" -> automation (NOT system_control)
- The user will frequently use Hindi/Hinglish (e.g. "kholo" = open, "band karo" = close, "batao" = tell me, "chalao" = play/run). Map these to the correct intents.

Note: The user has the following local desktop apps installed:
[{installed_apps}]
If the user asks to open/launch any of these (even if misspelled), route to system_control.

[CRITICAL LEARNED RULES FROM PAST MISTAKES]
{learned_rules}

Conversation History (last 2 turns):
{history}

User message: {message}"""


# ─── Desktop App Pre-Routing ─────────────────────────────────────────────────
# Constants and helper class used by CommandRouter._extract_app_name() and
# InstalledAppChecker to pre-route "open X" commands without an LLM call.

_OPEN_APP_RE = re.compile(
    r"""
    ^(?:(?:please|bhai|yaar)\s+)?
    (?:can\s+you\s+)?
    (?:open|launch|start|run|kholo|chalao|
       (?:khol\s+do)|(?:chala\s+do)|(?:start\s+karo)|(?:open\s+up))
    \s+
    (.+?)
    (?:\s+(?:for\s+me|please|app|game|software|karo|bhai))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_URL_INDICATORS = frozenset({".com", ".org", ".net", ".io", ".co", ".in", ".gov", ".edu", "http", "www."})


class InstalledAppChecker:
    """
    Lightweight Windows app index for pre-classification routing.

    Builds a one-time name index from:
      1. Windows Registry App Paths (HKLM/HKCU) — exact match, fastest
      2. shutil.which — apps in PATH
      3. Start Menu .lnk shortcuts — fuzzy match via difflib (catches typos)

    Lets CommandRouter pre-route "open X" messages to system_control before
    the LLM even sees them, even when the user misspells the app name
    (e.g. "open robolox" → fuzzy-matches "roblox" in Start Menu → system_control).
    Degrades silently on non-Windows (is_likely_installed always returns False).
    """

    def __init__(self) -> None:
        self._start_menu_names: list[str] = []
        self._built = False
        self._build_lock: Optional[asyncio.Lock] = None  # lazy — no event loop at __init__ time

    def _get_lock(self) -> asyncio.Lock:
        if self._build_lock is None:
            self._build_lock = asyncio.Lock()
        return self._build_lock

    async def ensure_built(self) -> None:
        """Build the Start Menu name index once (runs blocking I/O in executor)."""
        if self._built:
            return
        async with self._get_lock():
            if self._built:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._build_index)
            self._built = True

    def _build_index(self) -> None:
        """Collect app display-names from Start Menu .lnk shortcuts."""
        import glob as _glob
        names: list[str] = []
        start_menu_dirs = [
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
        ]
        for base in start_menu_dirs:
            if os.path.isdir(base):
                for lnk in _glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
                    name = os.path.splitext(os.path.basename(lnk))[0].lower().strip()
                    if name:
                        names.append(name)
        self._start_menu_names = names
        logger.info("[InstalledAppChecker] Index built: %d Start Menu entries", len(names))

    def is_likely_installed(self, name: str) -> bool:
        """
        Return True if *name* (possibly misspelled) matches an installed desktop app.
        Returns False when unsure — LLM then handles routing normally.
        """
        import difflib
        name_lower = name.lower().strip()
        if not name_lower or len(name_lower) < 2:
            return False

        # 1. Windows Registry App Paths (HKLM + HKCU)
        if self._check_registry(name_lower):
            return True

        # 2. PATH resolution via shutil.which
        import shutil as _shutil
        if _shutil.which(name_lower) or _shutil.which(name_lower + ".exe"):
            return True

        # 3. Fuzzy match against Start Menu index (cutoff 0.72 catches typos)
        if self._start_menu_names:
            matches = difflib.get_close_matches(
                name_lower, self._start_menu_names, n=1, cutoff=0.72
            )
            if matches:
                logger.info("[InstalledAppChecker] Fuzzy match: '%s' → '%s'", name, matches[0])
                return True

        return False

    def _check_registry(self, name: str) -> bool:
        """Check HKLM/HKCU App Paths registry for the given app name."""
        try:
            import winreg
        except ImportError:
            return False
        for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for cand in (name, name + ".exe"):
                try:
                    with winreg.OpenKey(
                        root_key,
                        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{cand}",
                        0,
                        winreg.KEY_READ,
                    ) as key:
                        val, _ = winreg.QueryValueEx(key, "")
                        if val:
                            return True
                except OSError:
                    continue
        return False


class CommandRouter:
    """
    Central message routing pipeline.
    Every user message flows through here before reaching any agent.
    """
    
    def __init__(self, ai_handler, agent_orchestrator, eternal_memory=None,
                 context_budget=None, screen_reader=None, clipboard_handler=None,
                 ws_broadcast=None, personality=None, entity_extractor=None):
        # Class constant — how many past turns to keep in the follow-up ring buffer
        self._FOLLOWUP_HISTORY_SIZE = 6
        self.ai_handler = ai_handler
        self.orchestrator = agent_orchestrator
        self.memory = eternal_memory
        self.context_budget = context_budget
        self.screen_reader = screen_reader
        self.clipboard = clipboard_handler
        self.ws_broadcast = ws_broadcast
        self.entity_extractor = entity_extractor
        
        # Personality engine — gives Makima her soul
        if personality is None:
            from .personality import PersonalityEngine
            self.personality = PersonalityEngine()
        else:
            self.personality = personality
        
        # Follow-up ring buffer — tracks last N (message, intent, reply) turns
        # PER CONVERSATION for context continuity. Partitioned by conversation_id
        # so concurrent requests from different sessions never see each other's
        # turns (Bug: previously a single list shared across all sessions).
        self._followup_ring_buffer: dict[str, list[dict]] = {}

        # Installed app checker — pre-routes "open X" to system_control without LLM (Bug 2 fix)
        self._app_checker = InstalledAppChecker()

        # State tracking for Reflexion / Self-Learning feedback loop — keyed by
        # conversation_id so negative-feedback ("wrong agent") for session A is
        # never attributed to session B's context.
        self._last_task_context: dict[str, dict[str, Any]] = {}
        # LearningCoordinator — injected by main.py after startup
        self._learning_coordinator: Any = None

        # Priority Queue for async task processing
        self._task_queue: asyncio.PriorityQueue[TaskItem] = asyncio.PriorityQueue()
        self._active_tasks: set[str] = set()
        self._cancelled_tasks: set[str] = set()

        # Semantic intent cache — avoids a redundant LLM classification call for
        # verbatim repeats within a short TTL. Keyed by a normalized message hash.
        # Only caches messages >= 8 chars to avoid caching trivial follow-ups
        # ("yes", "ok", "next") that are contextually ambiguous.
        self._intent_cache: dict[str, tuple[float, IntentResult]] = {}
        self._INTENT_CACHE_TTL = 30.0

        # Start queue processor
        self._queue_task: Optional[asyncio.Task] = None

    def _record_followup_turn(self, message: str, intent: Intent,
                              reply_summary: str = "", conversation_id: str | None = None) -> None:
        """Add a turn to the follow-up ring buffer. Called after every non-trivial exchange.
        Also detects regen signals and fires a style-learning signal to LearningCoordinator.

        Turns are stored per-conversation so concurrent sessions don't leak
        history into each other's classification prompts.
        """
        cv_id = conversation_id or "_default"
        _REGEN_PHRASES = {"dobara karo", "phir se karo", "aur achha", "better likho",
                          "improve karo", "rewrite", "rephrase", "again", "redo", "naya likho"}
        msg_lower = message.lower().strip()
        is_regen = any(p in msg_lower for p in _REGEN_PHRASES)
        buf = self._followup_ring_buffer.get(cv_id, [])
        if is_regen and buf:
            prev = buf[-1]
            lc = getattr(self, "_learning_coordinator", None)
            if lc and hasattr(lc, "on_regen_request"):
                try:
                    asyncio.ensure_future(lc.on_regen_request(
                        user_message=prev.get("message", ""),
                        original_response=prev.get("reply_summary", ""),
                        regen_phrase=message,
                    ))
                except Exception:
                    pass

        buf.append({
            "message": message,
            "intent": intent,
            "reply_summary": reply_summary[:120],
        })
        if len(buf) > self._FOLLOWUP_HISTORY_SIZE:
            buf = buf[-self._FOLLOWUP_HISTORY_SIZE:]
        self._followup_ring_buffer[cv_id] = buf
    
    async def start(self) -> None:
        """Start the queue processing loop."""
        self._queue_task = asyncio.create_task(self._process_queue())
        logger.info("CommandRouter started")
    
    async def stop(self) -> None:
        """Stop the queue processing loop and drain in-flight tasks."""
        if self._queue_task:
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass

        # Gracefully wait for any concurrent task coroutines still running
        if self._active_tasks:
            logger.info(f"Waiting for {len(self._active_tasks)} in-flight tasks to finish...")
            # Give tasks up to 5s to finish cleanly before hard-stopping
            deadline = asyncio.get_running_loop().time() + 5.0
            while self._active_tasks and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.1)
            if self._active_tasks:
                logger.warning(f"{len(self._active_tasks)} tasks did not finish within shutdown window.")

        logger.info("CommandRouter stopped")

    def cleanup_conversation(self, conversation_id: str) -> None:
        """Drop per-conversation router state when a conversation is deleted.

        Removes the follow-up ring-buffer history and the feedback-loop context
        for this conversation.  The intent cache is keyed by normalized message
        text (not conversation) and is TTL-managed, so it is intentionally not
        touched here.
        """
        self._followup_ring_buffer.pop(conversation_id, None)
        self._last_task_context.pop(conversation_id, None)

    async def handle_message(self, task_id: str, message: str,
                              context: dict[str, Any] | None = None,
                              is_offline_queued: bool = False) -> None:
        """
        Entry point for all user messages. Classifies intent, then routes.
        
        Args:
            task_id: Unique task correlation ID
            message: Raw user message text
            context: Optional context (attached files, screen, clipboard)
            is_offline_queued: If True, skip stale screen context (P19)
        """
        ctx = context or {}
        
        # Edge case: empty / whitespace-only / non-string input. Never burn an
        # LLM call on silence — acknowledge gracefully and stop.
        if not isinstance(message, str) or not message.strip():
            if self.ws_broadcast:
                try:
                    from . import ws_protocol
                except ImportError:
                    import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(
                        task_id,
                        "I didn't catch any words. What would you like me to do?",
                        is_final=True,
                    )
                )
            return
        
        # Handle offline-queued messages: stale screen context — None (P19)
        if is_offline_queued:
            ctx["screen_context"] = None
            ctx["offline_queued"] = True
        
        # Step 1: Check for trivial commands (no LLM needed)
        trivial_result = self._check_trivial(message)
        if trivial_result:
            await self._handle_trivial(task_id, trivial_result, message)
            return

        # Feed real conversation turns to the background entity extractor.
        # This is fire-and-forget: submit_turn() just pushes onto a bounded
        # queue.Queue() consumed by a daemon thread — it never blocks the
        # routing pipeline and never raises into it.
        if self.entity_extractor:
            try:
                self.entity_extractor.submit_turn(message, ctx)
            except Exception as e:
                logger.debug(f"entity_extractor.submit_turn failed: {e}")
        
        # Step 1.2: Check for negative feedback / user correction (Reflexion loop)
        msg_lower = message.lower().strip()
        feedback_triggers = ["nhi hua", "nahi hua", "wrong agent", "galat agent", "galat hai", "wrong", "didnt work", "didn't work", "fail ho gaya"]
        # One logical conversation must resolve to exactly one state key
        # throughout this flow.  Normalize once, then inject the normalized
        # value so downstream callers (_build_context, memory saves,
        # _record_followup_turn) read the same key instead of re-deriving it.
        cv_id = ctx.get("conversation_id") or "_default"
        ctx["conversation_id"] = cv_id
        last_ctx = self._last_task_context.get(cv_id)
        if last_ctx and any(trigger in msg_lower for trigger in feedback_triggers):
            logger.info("[router] Negative feedback detected: %r. Triggering _learn_from_mistake...", message)
            await self._learn_from_mistake(last_ctx, message)
            if self.ws_broadcast:
                try:
                    from . import ws_protocol
                except ImportError:
                    import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task_id, "Got it, I've updated my routing rules based on your feedback.", is_final=True)
                )
            return

        # Step 1.4: 100% PURE LLM INTENT CLASSIFICATION & ROUTING
        intent_result = await self._classify_intent(message, conversation_id=cv_id)
        logger.info(f"[router] Pure LLM Intent Classified: {intent_result.intent} (confidence: {intent_result.confidence:.2f}, entities: {intent_result.entities})")

        # Update last executed task context for Reflexion feedback tracking
        # (keyed per conversation so feedback is never attributed to another
        # session's context).
        self._last_task_context[cv_id] = {
            "query": message,
            "intent": intent_result.intent.value if hasattr(intent_result.intent, "value") else str(intent_result.intent)
        }
        
        
        # Step 3: Route based on confidence and intent
        task_item = TaskItem(
            task_id=task_id,
            message=message,
            intent_result=intent_result,
            context=ctx,
        )
        
        # Low confidence on complex intent ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ Commander
        if (intent_result.confidence < 0.6 and 
            intent_result.intent not in (Intent.FAST_CHAT, Intent.TRIVIAL)):
            intent_result.intent = Intent.MULTI_STEP
            task_item.intent_result = intent_result
        
        # Set priority
        if intent_result.intent in (Intent.SYSTEM_CONTROL, Intent.MESSAGING):
            task_item.priority = Priority.CRITICAL
        elif intent_result.intent in (Intent.RESEARCH, Intent.AUTOMATION):
            task_item.priority = Priority.BACKGROUND
        
        await self._task_queue.put(task_item)
    
    def _check_trivial(self, message: str) -> Optional[str]:
        """Check if message is a trivial command that doesn't need an agent."""
        msg_lower = message.lower().strip()
        
        # Word count limit: complex queries shouldn't match simple trivial patterns
        if len(msg_lower.split()) > 6:
            return None
            
        for cmd_type, patterns in TRIVIAL_COMMANDS.items():
            for pattern in patterns:
                if pattern in msg_lower:
                    return cmd_type
        return None
    
    async def _handle_trivial(self, task_id: str, cmd_type: str, message: str) -> None:
        """Handle trivial commands without touching agents (< 1s response)."""
        try:
            from . import ws_protocol
        except ImportError:
            import ws_protocol
        
        if cmd_type == "time":
            from datetime import datetime
            response = f"It's {datetime.now().strftime('%I:%M %p')}"
        elif cmd_type == "date":
            from datetime import datetime
            response = f"Today is {datetime.now().strftime('%A, %B %d, %Y')}"
        else:
            response = "I'm not sure how to handle that."
        
        if self.ws_broadcast:
            await self.ws_broadcast(ws_protocol.build_ai_chunk(task_id, response, is_final=True))
    
    async def _classify_intent(self, message: str,
                               conversation_id: str | None = None) -> IntentResult:
        """
        Classify user intent via single LLM call.
        Uses fast backend (Groq) for speed. Target: < 100ms.

        Memoized by normalized message for _INTENT_CACHE_TTL seconds so verbatim
        repeats skip the LLM call entirely. Trivial follow-ups (< 8 chars) are
        not cached — their semantics depend on conversation context.

        ``conversation_id`` scopes the follow-up ring-buffer lookup so different
        sessions never share recent-turn history.
        """
        # --- Semantic intent cache (avoid redundant LLM classification) ---
        cache_key = None
        norm = message.strip().lower()
        if len(norm) >= 8:
            cache_key = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]
            cached = self._intent_cache.get(cache_key)
            if cached is not None:
                ts, result = cached
                if time.time() - ts < self._INTENT_CACHE_TTL:
                    # Return a *copy* so callers (which mutate intent for the
                    # low-confidence rewrite at the MULTI_STEP path) cannot
                    # corrupt the cached object and leak mutations into other
                    # concurrent sessions.
                    return IntentResult(
                        intent=result.intent,
                        confidence=result.confidence,
                        entities=dict(result.entities or {}),
                        raw_response=result.raw_response,
                    )
                # expired
                self._intent_cache.pop(cache_key, None)

        # Ensure app index is built
        await self._app_checker.ensure_built()
        
        # Get installed apps string to inject into prompt (dynamic local context)
        apps = self._app_checker._start_menu_names[:150]
        installed_apps_str = ", ".join(apps) if apps else "None detected"

        # Search for learned rules via RAG
        learned_rules_list: list[str] = []
        if self.memory and hasattr(self.memory, "search_rules"):
            try:
                learned_rules_list = await self.memory.search_rules(message, top_k=3)
            except Exception as e:
                logger.warning("[router] search_rules failed: %s", e)
        learned_rules_str = "\n".join([f"- {r}" for r in learned_rules_list]) if learned_rules_list else "None"

        # Format the last 2 turns of conversation history for the LLM.
        # Use the per-conversation ring buffer (most recent turns), fallback to
        # nothing.  This is scoped by conversation_id so one session's turns
        # never appear in another session's classification prompt.
        history_str = "None"
        cv_id = conversation_id or "_default"
        buf = self._followup_ring_buffer.get(cv_id, [])
        if buf:
            turns = []
            for t in buf[-2:]:
                turns.append(f"User: {t['message']}\nMakima: {t.get('reply_summary', '...')}")
            history_str = "\n---\n".join(turns)

        prompt = INTENT_CLASSIFICATION_PROMPT.format(
            history=history_str,
            installed_apps=installed_apps_str,
            learned_rules=learned_rules_str,
            message=message
        )
        
        try:
            response = await asyncio.wait_for(
                self.ai_handler.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task="intent_classification",
                    require_json=True,
                    temperature=0.1,
                    max_tokens=120,
                ),
                timeout=4.0,
            )
            
            # Parse JSON response
            parsed = self.ai_handler.try_parse_json(response.text)
            if parsed:
                intent_str = parsed.get("intent", "unknown")
                try:
                    intent = Intent(intent_str)
                except ValueError:
                    intent = Intent.UNKNOWN
                
                result = IntentResult(
                    intent=intent,
                    confidence=float(parsed.get("confidence", 0.5)),
                    entities=parsed.get("entities") or {},
                    raw_response=response.text,
                )
                if cache_key is not None:
                    self._intent_cache[cache_key] = (time.time(), result)
                return result
            
        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
        
        # Fallback: treat as fast_chat with low confidence
        return IntentResult(
            intent=Intent.FAST_CHAT,
            confidence=0.3,
        )

    async def _learn_from_mistake(self, last_context: dict[str, Any], feedback: str) -> None:
        """
        Closed-Loop Reflexion: Route correction signal to LearningCoordinator which
        uses a fast LLM to extract an actionable routing rule and stores it.
        The rule is injected into future classification prompts via search_behavior_rules().
        """
        last_query = last_context.get("query", "")
        last_intent = last_context.get("intent", "")

        # Use LearningCoordinator if available (preferred — structured + categorised)
        lc = getattr(self, "_learning_coordinator", None)
        if lc and hasattr(lc, "on_routing_correction"):
            try:
                await lc.on_routing_correction(
                    user_message=last_query,
                    wrong_intent=last_intent,
                    correct_intent=feedback,
                )
                logger.info("[router] Routing correction handed to LearningCoordinator")
                return
            except Exception as e:
                logger.warning("[router] LearningCoordinator.on_routing_correction failed: %s", e)

        # Fallback: legacy eternal_memory save_rule
        try:
            prompt = (
                f"The user previously asked '{last_query}' and we routed it to intent '{last_intent}'.\n"
                f"The user corrected us with: '{feedback}'.\n"
                "Write a strict, clear 1-sentence routing rule.\n"
                'Respond ONLY with JSON: {"rule": "...", "keywords": ["w1","w2"]}'
            )
            response = await asyncio.wait_for(
                self.ai_handler.generate(
                    messages=[{"role": "user", "content": prompt}],
                    task="intent_classification",
                    require_json=True,
                    temperature=0.2,
                    max_tokens=100,
                ),
                timeout=5.0,
            )
            parsed = self.ai_handler.try_parse_json(response.text)
            if parsed and parsed.get("rule") and self.memory and hasattr(self.memory, "save_rule"):
                await self.memory.save_rule(parsed["rule"], parsed.get("keywords", []))
                logger.info("[router] Fallback reflexion rule saved: %r", parsed["rule"])
        except Exception as e:
            logger.error("[router] _learn_from_mistake fallback failed: %s", e)
    
    async def _process_queue(self) -> None:
        """Process tasks from the priority queue."""
        while True:
            try:
                task = await self._task_queue.get()
                
                # Skip cancelled tasks (T6)
                if task.task_id in self._cancelled_tasks:
                    self._cancelled_tasks.discard(task.task_id)
                    self._task_queue.task_done()
                    continue
                
                self._active_tasks.add(task.task_id)
                
                # Run concurrently to avoid head-of-line blocking
                asyncio.create_task(self._run_task_and_cleanup(task))
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                await asyncio.sleep(1)

    async def _run_task_and_cleanup(self, task: TaskItem) -> None:
        """Wrapper to execute task and ensure cleanup."""
        try:
            await self._execute_task(task)
        except Exception as e:
            logger.error(f"Task {task.task_id} failed: {e}")
            if self.ws_broadcast:
                try:
                    from . import ws_protocol
                except ImportError:
                    import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_ai_error(task.task_id, str(e))
                )
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task.task_id, "", is_final=True)
                )
        finally:
            self._active_tasks.discard(task.task_id)
            self._task_queue.task_done()
    
    async def _execute_task(self, task: TaskItem) -> None:
        """Execute a classified task by routing to the appropriate agent."""
        intent = task.intent_result.intent if task.intent_result else Intent.FAST_CHAT
        
        # Build context for the agent
        context = await self._build_context(task)
        
        if intent == Intent.FAST_CHAT:
            # Direct LLM response, no agent needed
            reply_text = await self._direct_response(task, context)  # Bug 1 fix: capture reply
            # Record for follow-up ring buffer — include reply so next turn has context
            self._record_followup_turn(task.message, intent, reply_summary=reply_text,
                                       conversation_id=context.get("conversation_id"))
        elif intent == Intent.MULTI_STEP:
            # [MIGRATION]: Route compound intents to Ecosystem Hub if available,
            # otherwise fall back to direct LLM response (elite_coordinator is deprecated).
            reply_text = None
            try:
                from .main import _modules
                ecosystem_hub = _modules.get("ecosystem_hub")
            except Exception:
                ecosystem_hub = None

            if ecosystem_hub and hasattr(ecosystem_hub, "execute"):
                try:
                    reply_text = await ecosystem_hub.execute(
                        task_id=task.task_id,
                        message=task.message,
                        context=context,
                        entities=task.intent_result.entities if task.intent_result else {}
                    )
                except Exception as eco_err:
                    logger.error(f"[router] EcosystemHub execution error: {eco_err}")

            if not reply_text:
                # EcosystemHub unavailable — fall back to direct LLM response.
                # elite_coordinator is deprecated and not registered; dispatching to it
                # would crash the queue worker. Direct response is the safe fallback.
                logger.info("[router] MULTI_STEP: EcosystemHub unavailable, using direct LLM fallback")
                await self._direct_response(task, context)
                return  # _direct_response handles ws_broadcast + memory save internally

            if self.ws_broadcast and reply_text:
                try:
                    from . import ws_protocol
                except ImportError:
                    import ws_protocol
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task.task_id, reply_text, is_final=False)
                )
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task.task_id, "", is_final=True)
                )
            if self.memory and reply_text:
                try:
                    await self.memory.save_turn(task.message, "user", context.get("conversation_id"))
                    await self.memory.save_turn(reply_text, "assistant", context.get("conversation_id"))
                except Exception as e:
                    logger.warning(f"Failed to save turn from multi-step execution: {e}")
        else:
            # Route to agent via orchestrator
            agent_name = INTENT_TO_AGENT.get(intent)
            if agent_name:
                # Follow-up message reconstruction: "dobara karo" alone gives
                # the agent zero context. Prepend the previous message so the
                # agent knows what to retry/repeat/elaborate on.
                entities = task.intent_result.entities if task.intent_result else {}
                dispatch_message = task.message
                if entities.get("is_followup") and entities.get("previous_user_message"):
                    prev = entities["previous_user_message"]
                    cat  = entities.get("followup_category", "repeat")
                    dispatch_message = (
                        f"[FOLLOWUP:{cat}] The user previously asked: '{prev}'. "
                        f"Now they said: '{task.message}'. "
                        f"Act accordingly (retry/repeat/elaborate as needed)."
                    )
                if self.ws_broadcast:
                    try:
                        from . import ws_protocol
                    except ImportError:
                        import ws_protocol
                    await self.ws_broadcast(
                        ws_protocol.build_ai_chunk(
                            task.task_id,
                            f"[⚡ {agent_name.upper()}]: Processing '{task.message}'...\n",
                            is_final=False,
                        )
                    )
                reply_text = None
                try:
                    res = await self.orchestrator.dispatch(
                        task_id=task.task_id,
                        agent_name=agent_name,
                        message=dispatch_message,
                        context=context,
                        entities=entities,
                    )
                    if res:
                        raw_result = res.result if res.success else (res.error or "I encountered an error executing that task.")
                        # Polish successful agent output through the LLM for clean formatting.
                        # Error strings are passed through unmodified.
                        if res.success and raw_result:
                            reply_text = await self._polish_agent_response(
                                raw_result, task.message, agent_name
                            )
                        else:
                            reply_text = raw_result
                        if self.ws_broadcast and reply_text:
                            try:
                                from . import ws_protocol
                            except ImportError:
                                import ws_protocol
                            await self.ws_broadcast(
                                ws_protocol.build_ai_chunk(task.task_id, reply_text, is_final=False)
                            )
                except Exception as dispatch_err:
                    logger.error(f"[router] Agent dispatch error for {agent_name}: {dispatch_err}")
                    if self.ws_broadcast:
                        try:
                            from . import ws_protocol
                        except ImportError:
                            import ws_protocol
                        await self.ws_broadcast(
                            ws_protocol.build_ai_chunk(task.task_id, f"Error executing {agent_name}: {dispatch_err}", is_final=False)
                        )
                finally:
                    if self.ws_broadcast:
                        try:
                            from . import ws_protocol
                        except ImportError:
                            import ws_protocol
                        await self.ws_broadcast(
                            ws_protocol.build_ai_chunk(task.task_id, "", is_final=True)
                        )
                
                if self.memory and reply_text:
                    try:
                        await self.memory.save_turn(task.message, "user", context.get("conversation_id"))
                        await self.memory.save_turn(reply_text, "assistant", context.get("conversation_id"))
                    except Exception as e:
                        logger.warning(f"Failed to save turn from agent: {e}")
                # Record for follow-up ring buffer so next "dobara karo" can
                # reconstruct the correct context for this agent.
                self._record_followup_turn(task.message, intent, reply_summary=reply_text or "",
                                           conversation_id=context.get("conversation_id"))
            else:
                await self._direct_response(task, context)
    
    async def _build_context(self, task: TaskItem) -> dict[str, Any]:
        """
        Build rich context for the task using context_budget allocator.
        Includes: history, memory search, graph triples, screen, clipboard.
        """
        context = dict(task.context)

        async def _get_memory_search():
            if not self.memory:
                return []
            try:
                # EternalMemory.search() is itself an `async def` that already
                # offloads its blocking SQLite work to an executor internally ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â
                # it must be awaited directly, never re-wrapped in
                # run_in_executor() (which would just hand back an
                # un-awaited coroutine object instead of a result list).
                return await asyncio.wait_for(
                    self.memory.search(task.message, 5),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Memory search failed: {e}")
                return []

        async def _get_history():
            if not self.memory:
                return []
            try:
                # Fetch 20 turns, scoped strictly to the current conversation ID
                return await asyncio.wait_for(
                    self.memory.get_history(20, conversation_id=context.get("conversation_id")),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"History fetch failed: {e}")
                return []

        async def _get_screen():
            if self.screen_reader and not context.get("offline_queued"):
                try:
                    return await asyncio.wait_for(
                        self.screen_reader.get_context(),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, ImportError, OSError, NotImplementedError) as dep_err:
                    logger.warning(f"[router] Screen reader unavailable or timed out: {dep_err}")
                    return None
                except Exception as e:
                    logger.error(f"[router] Screen reader runtime failure: {e}", exc_info=True)
                    return None
            return None

        async def _get_clipboard():
            clipboard_keywords = ["clipboard", "copied", "copy", "paste", "just copied"]
            if self.clipboard and any(
                kw in task.message.lower() for kw in clipboard_keywords
            ):
                try:
                    return await self.clipboard.get_clipboard()
                except Exception as e:
                    logger.error(f"[router] Clipboard runtime failure: {e}", exc_info=True)
                    return None
            return None

        results = await asyncio.gather(
            _get_memory_search(),
            _get_history(),
            _get_screen(),
            _get_clipboard(),
            return_exceptions=True,
        )

        defaults = [[], [], None, None]
        results = [
            default if isinstance(r, BaseException) else r
            for r, default in zip(results, defaults)
        ]

        context["memory_results"] = results[0]
        context["history"] = results[1]
        context["screen_context"] = results[2]
        context["clipboard"] = results[3]
        return context

    # ──────────────────────────────────────────────────────────────
    # RESPONSE POLISH — formats raw agent output before user sees it
    # ──────────────────────────────────────────────────────────────

    _POLISH_SYSTEM_PROMPT = (
        "You are a response formatter for an AI assistant called Makima. "
        "An agent has produced the raw output below. Your job is to reformat "
        "it into a clean, well-structured response for the user. Rules:\n"
        "1. PRESERVE every fact, number, and finding — never change meaning.\n"
        "2. Remove any leftover JSON keys, internal tags, agent labels, debug "
        "   text, or repeated boilerplate (e.g. '[research] Initiating...').\n"
        "3. Use clear markdown: headers (##) for sections, bullet points for "
        "   lists, bold for key terms, code blocks for code.\n"
        "4. Keep a natural, confident tone — no filler phrases like "
        "   'Certainly!' or 'Great question!'.\n"
        "5. If the raw output is already clean prose, leave it as-is — "
        "   do NOT add structure where none is needed.\n"
        "6. Never mention that you reformatted anything."
    )

    async def _polish_agent_response(
        self,
        raw_output: str,
        user_message: str,
        agent_name: str,
    ) -> str:
        """
        Send the agent's raw output to the LLM for formatting/cleanup.
        Falls back to raw_output on any error or timeout.
        """
        if not raw_output or not raw_output.strip():
            return raw_output
        # Skip polishing for very short outputs — already clean
        if len(raw_output.strip()) < 80:
            return raw_output
        try:
            messages = [
                {"role": "system", "content": self._POLISH_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"User asked: {user_message}\n\n"
                        f"Raw agent output from {agent_name}:\n{raw_output}"
                    ),
                },
            ]
            polished = await asyncio.wait_for(
                self.ai_handler.chat_complete(
                    messages,
                    max_tokens=2048,
                    temperature=0.3,  # Low temp → faithful, not creative
                ),
                timeout=8.0,  # Generous but bounded — agent already did the heavy work
            )
            if polished and polished.strip():
                logger.debug(f"[polish] {agent_name} output polished ({len(raw_output)}→{len(polished)} chars)")
                return polished.strip()
        except asyncio.TimeoutError:
            logger.warning(f"[polish] Timeout polishing {agent_name} response — using raw output")
        except Exception as e:
            logger.warning(f"[polish] Failed to polish {agent_name} response: {e} — using raw output")
        return raw_output

    async def _direct_response(self, task: TaskItem, context: dict) -> str:  # Bug 1 fix: was None
        """Generate a direct LLM response without an agent (fast_chat)."""
        try:
            from . import ws_protocol
        except ImportError:
            import ws_protocol
        
        messages = self._build_messages(task.message, context)
        full_response = []
        
        try:
            if self.ws_broadcast:
                try:
                    async for chunk in self.ai_handler.generate_stream(messages, task="fast_chat"):
                        if task.task_id in self._cancelled_tasks:
                            break
                        if chunk:
                            full_response.append(chunk)
                            await self.ws_broadcast(
                                ws_protocol.build_ai_chunk(task.task_id, chunk, is_final=False)
                            )
                except Exception as stream_err:
                    logger.warning(f"Streaming failed in _direct_response: {stream_err}")
                
                if not full_response:
                    try:
                        fallback_text = await asyncio.wait_for(
                            self.ai_handler.chat_complete(messages, max_tokens=256, temperature=0.7),
                            timeout=5.0
                        )
                        if fallback_text:
                            full_response.append(fallback_text)
                            await self.ws_broadcast(
                                ws_protocol.build_ai_chunk(task.task_id, fallback_text, is_final=False)
                            )
                    except Exception as fallback_err:
                        logger.error(f"Fallback chat_complete error in _direct_response: {fallback_err}")

                # TRUE FAILURE FALLBACK ONLY: Triggered ONLY when both stream & fallback API calls fail completely
                if not full_response:
                    default_greeting = "⚠️ [SYSTEM WARNING: Cloud LLM Endpoint Slow / Unreachable]\nHello! Main local system level par bilkul active hoon. Bataiye aaj kya kaam karna hai?"
                    full_response.append(default_greeting)
                    await self.ws_broadcast(
                        ws_protocol.build_ai_chunk(task.task_id, default_greeting, is_final=False)
                    )
            else:
                try:
                    async for chunk in self.ai_handler.generate_stream(messages, task="fast_chat"):
                        if task.task_id in self._cancelled_tasks:
                            break
                        if chunk:
                            full_response.append(chunk)
                except Exception:
                    try:
                        fallback_text = await asyncio.wait_for(
                            self.ai_handler.chat_complete(messages, max_tokens=256, temperature=0.7),
                            timeout=5.0
                        )
                        if fallback_text:
                            full_response.append(fallback_text)
                    except Exception:
                        full_response.append("\u26a0\ufe0f [SYSTEM WARNING: Cloud LLM Endpoint Slow / Unreachable]\nHello! Main local system level par bilkul active hoon.")

        finally:
            if self.ws_broadcast:
                await self.ws_broadcast(
                    ws_protocol.build_ai_chunk(task.task_id, "", is_final=True)
                )

        ai_text = "".join(full_response)
        if self.memory:
            try:
                await self.memory.save_turn(task.message, "user", context.get("conversation_id"))
                if ai_text:
                    await self.memory.save_turn(ai_text, "assistant", context.get("conversation_id"))
            except Exception as e:
                logger.warning(f"Failed to save turn: {e}")
        return ai_text

    def _build_messages(self, user_message: str, context: dict) -> list[dict]:
        """Build LLM message list with dynamic personality + context + user message."""
        # Process emotion state based on message
        intent_str = context.get("intent", "fast_chat")
        if hasattr(self.personality, "process_turn"):
            self.personality.process_turn(user_message, {"intent": intent_str})

        # Build extra context string for personality engine
        extra_parts = []
        if context.get("memory_results"):
            extra_parts.append("\n[MEMORY CONTEXT]")
            for result in context["memory_results"][:5]:
                extra_parts.append(f"- {result}")
        if context.get("screen_context"):
            extra_parts.append(f"\n[SCREEN CONTEXT]\n{context['screen_context']}")
        if context.get("clipboard"):
            extra_parts.append(f"\n[CLIPBOARD]\n{context['clipboard']}")

        # Always-on: tell the LLM to use conversation history for context
        # No hardcoded word lists - the LLM infers context from history
        extra_parts.append(
            "\n[RULE] Always read the full conversation history before replying. "
            "Short or ambiguous messages should be answered using context from "
            "the previous turn - never ask what the user wants if history shows it. "
            "Match the energy of short messages with short replies (1-3 sentences). "
            "No filler openers like Certainly or Great question."
        )

        # Get the full dynamic system prompt from personality engine
        if hasattr(self.personality, "build_system_prompt"):
            system_prompt = self.personality.build_system_prompt(
                extra_context="\n".join(extra_parts)
            )
        else:
            system_prompt = "You are Makima, a helpful AI assistant."

        if self.ws_broadcast and hasattr(self.personality, "get_emotion_for_ws"):
            try:
                try:
                    from . import ws_protocol
                except ImportError:
                    import ws_protocol
                asyncio.get_running_loop().create_task(
                    self.ws_broadcast(ws_protocol.WSMessage(
                        v=ws_protocol.PROTOCOL_VERSION,
                        type="emotion_update",
                        payload=self.personality.get_emotion_for_ws(),
                    ))
                )
            except RuntimeError:
                # No running loop — skip emotion broadcast (edge case: sync test context)
                pass
            except Exception:
                pass

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (last 20 turns, most recent)
        for turn in context.get("history", [])[-20:]:
            if isinstance(turn, dict):
                messages.append(turn)

        messages.append({"role": "user", "content": user_message})

        return messages

    async def cancel_task(self, task_id: str) -> None:
        """Cancel a running or queued task."""
        self._cancelled_tasks.add(task_id)
        if task_id in self._active_tasks:
            await self.orchestrator.cancel_task(task_id)
        logger.info(f"Task {task_id} cancelled")
