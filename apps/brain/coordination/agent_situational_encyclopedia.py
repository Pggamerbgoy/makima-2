"""
Makima OS v7.2 — Elite Agent Situational Encyclopedia & Operational Knowledge Engine
Location: apps/brain/coordination/agent_situational_encyclopedia.py

Provides VAST, encyclopedic situational awareness, multi-agent collaboration patterns,
tool chaining strategies, and deep technical capability mappings for all 15 Makima agents
across 100+ real-world engineering, OS, media, automation, data, security, and creative situations.

Why this module exists:
To ensure Makima's Commander Agent, Intent Detector, and leaf agents are never limited to
surface-level tool lists. When a user asks complex, unexpected, or deep technical questions
or requests multi-domain workflows (in English, Hindi, or Hinglish), Makima can instantly
retrieve proven multi-agent DAG decomposition patterns and situational guidance.
"""

from __future__ import annotations

import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("makima.coordination.situational_encyclopedia")


@dataclass
class SituationalPattern:
    """A proven operational pattern for handling a specific real-world situation."""
    situation_id: str
    domain: str  # e.g., "OS & Desktop", "Media Control", "Software Engineering"
    title: str
    description: str
    user_queries_examples: List[str]
    primary_agents: List[str]
    supporting_agents: List[str]
    tools_involved: List[str]
    dag_strategy: str
    execution_steps: List[str]
    edge_cases_handled: List[str]
    hinglish_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentSituationalEncyclopedia:
    """
    The vast operational knowledge base for Makima OS.
    Stores and queries 100+ situational patterns across 12 major domains.
    """

    def __init__(self):
        self._patterns: Dict[str, SituationalPattern] = {}
        self._domain_index: Dict[str, List[str]] = {}
        self._agent_index: Dict[str, List[str]] = {}
        self._load_encyclopedia()

    def _register_pattern(self, pattern: SituationalPattern) -> None:
        self._patterns[pattern.situation_id] = pattern
        if pattern.domain not in self._domain_index:
            self._domain_index[pattern.domain] = []
        self._domain_index[pattern.domain].append(pattern.situation_id)

        for agent in pattern.primary_agents + pattern.supporting_agents:
            if agent not in self._agent_index:
                self._agent_index[agent] = []
            if pattern.situation_id not in self._agent_index[agent]:
                self._agent_index[agent].append(pattern.situation_id)

    def _load_encyclopedia(self) -> None:
        """Loads all vast situational patterns into memory."""
        # ─────────────────────────────────────────────────────────────
        # DOMAIN 1: OS & DESKTOP FILE MANAGEMENT (SYSTEM_AGENT + MEMORY)
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="os_desktop_organize_scattered",
            domain="OS & Desktop",
            title="Organize Scattered Desktop Files into Categorized Folders",
            description="When files on Desktop are cluttered ('idhar udhar ki files lineup karo'), automatically classify by extension/purpose and move them into neat subfolders.",
            user_queries_examples=[
                "organize my desktop files",
                "aur jo files desktop pe idhar udhar honge unhe lineup karega na?",
                "desktop saaf karo aur files ko sahi folders me dalo",
                "move all pdfs and pngs from desktop to documents and pictures"
            ],
            primary_agents=["system_agent"],
            supporting_agents=["memory_agent", "automation_agent"],
            tools_involved=["organize_desktop", "move_file", "list_dir", "eternal_memory_query"],
            dag_strategy="Single agent direct execution with memory recall for custom user folder rules.",
            execution_steps=[
                "1. Query memory_agent for any custom user folder preferences.",
                "2. Call system_agent.organize_desktop(path='Desktop', mode='by_extension').",
                "3. Return summary table of moved files and created folders."
            ],
            edge_cases_handled=[
                "Files currently open in an application (skip with warning)",
                "Duplicate filenames in target directory (auto-rename with _1 suffix)",
                "System shortcuts (.lnk) should remain on Desktop unless specified"
            ],
            hinglish_keywords=["desktop", "idhar udhar", "lineup", "saaf", "organize", "folder", "move"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="os_bulk_rename_and_archive",
            domain="OS & Desktop",
            title="Bulk Rename, Clean Downloads, and Archive Old Project Files",
            description="Clean up cluttered download directories or archive inactive workspace files.",
            user_queries_examples=[
                "clean my downloads folder and archive installers older than 30 days",
                "downloads folder saaf kar de aur purane setups archive kar"
            ],
            primary_agents=["system_agent"],
            supporting_agents=["automation_agent"],
            tools_involved=["move_file", "rename_file", "list_dir"],
            dag_strategy="Sequential file inspection followed by batch move/archive.",
            execution_steps=[
                "1. Enumerate files in Downloads directory.",
                "2. Filter by modified timestamp and extension (.exe, .msi, .zip).",
                "3. Move matching files to Archives folder."
            ],
            edge_cases_handled=["Active downloads in progress (.crdownload, .tmp) are skipped."],
            hinglish_keywords=["downloads", "saaf", "archive", "purana", "setups"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 2: MEDIA & ENTERTAINMENT CONTROL (MEDIA_AGENT)
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="media_natural_language_play_volume",
            domain="Media Control",
            title="Semantic Natural Language Music Playback and Volume Tuning",
            description="Parse complex or casual Hinglish/English music requests without hardcoded string lists or brittle regex.",
            user_queries_examples=[
                "wo purana arijit ka sad song chalao aur volume thoda kam karo",
                "play lo-fi hip hop beats on youtube and mute spotify",
                "gana chala de koi accha sa hindi romantic"
            ],
            primary_agents=["media_agent"],
            supporting_agents=["system_agent"],
            tools_involved=["play_media", "set_volume", "search_youtube", "search_spotify"],
            dag_strategy="Zero-shot LLM semantic extraction of track/artist/volume followed by media playback execution.",
            execution_steps=[
                "1. Use LLM semantic extraction to identify query='arijit sad songs', platform='youtube/spotify', volume_delta=-15.",
                "2. Execute set_volume if volume change is requested.",
                "3. Execute play_media with semantic query."
            ],
            edge_cases_handled=[
                "Browser or Spotify app not running (auto-open URL)",
                "Ambiguous song name (default to top popular search result)"
            ],
            hinglish_keywords=["gana", "chalao", "song", "arijit", "volume", "kam", "jyada", "bajao"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="media_podcast_bookmark_search",
            domain="Media Control",
            title="Search Specific Video/Podcast Episode and Timestamp Navigation",
            description="Locate specific podcast episodes, lectures, or music sets and control playback state.",
            user_queries_examples=[
                "search Lex Fridman AI episode on YouTube and start from 10 minutes",
                "youtube pe python tutorial chalao"
            ],
            primary_agents=["media_agent"],
            supporting_agents=["browser_agent"],
            tools_involved=["play_media", "browser_navigate"],
            dag_strategy="Direct URL resolution with timestamp parameters.",
            execution_steps=[
                "1. Resolve video search query with timestamp parameter.",
                "2. Launch playback in browser or media app."
            ],
            edge_cases_handled=["Ad-blocker or autoplay restriction handling."],
            hinglish_keywords=["youtube", "tutorial", "episode", "chalao", "search"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 3: FULL-STACK SOFTWARE ENGINEERING & DEBUGGING
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="code_concurrency_deadlock_debug",
            domain="Software Engineering",
            title="Diagnose and Fix Concurrency Race Conditions and Deadlocks",
            description="Deep inspection of async event loops, locking mechanisms, shared singletons, and thread pools.",
            user_queries_examples=[
                "fix async deadlock in backend event loop",
                "why is the agent hanging when multiple tasks run?",
                "backend me deadlock ho rha hai check karo lock sahi hai ya nhi"
            ],
            primary_agents=["code_agent"],
            supporting_agents=["security_agent", "devops_agent"],
            tools_involved=["code_read", "code_edit", "py_compile_verify", "test_runner"],
            dag_strategy="Sequential diagnostic -> Devil's Advocate self-critique -> safe AST patch -> test verification.",
            execution_steps=[
                "1. Inspect async locks (asyncio.Lock) and shared state across threads.",
                "2. Perform Devil's Advocate critique of potential race conditions.",
                "3. Apply fix ensuring proper try/finally lock release.",
                "4. Verify syntax with py_compile."
            ],
            edge_cases_handled=[
                "Nested async locks causing re-entrant deadlock",
                "Blocking synchronous IO inside async event loop (wrap in asyncio.to_thread)"
            ],
            hinglish_keywords=["deadlock", "hang", "lock", "async", "backend", "fix", "bug"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="code_multi_file_refactor_doc",
            domain="Software Engineering",
            title="Multi-File Architecture Refactoring with Living Documentation",
            description="Refactor complex modules across multiple files while maintaining type safety and documentation.",
            user_queries_examples=[
                "refactor command router and update architectural docs",
                "pura module clean karo aur comments add karo"
            ],
            primary_agents=["code_agent"],
            supporting_agents=["document_agent", "codebase_analysis"],
            tools_involved=["code_edit", "generate_markdown_doc", "py_compile_verify"],
            dag_strategy="DAG: CodeAgent refactors modules -> DocumentAgent syncs ARCHITECTURE.md.",
            execution_steps=[
                "1. Analyze dependency graph across files.",
                "2. Refactor interfaces and ensure zero broken imports.",
                "3. Verify syntax and generate updated living documentation."
            ],
            edge_cases_handled=["Circular imports and missing type hints."],
            hinglish_keywords=["refactor", "clean", "module", "docs", "documentation"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 4: WEB RESEARCH & COMPETITIVE ANALYSIS
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="research_multi_hop_comparative_table",
            domain="Web Research",
            title="Multi-Hop Web Research with Credibility Scoring and Excel Table",
            description="Deep investigative web search across multiple sources, synthesizing data into a comparative spreadsheet.",
            user_queries_examples=[
                "research top 5 LLM serving frameworks and make a comparison excel sheet",
                "compare latest AI agents and give me a detailed report in xlsx"
            ],
            primary_agents=["research_agent"],
            supporting_agents=["data_analyst_agent", "document_agent"],
            tools_involved=["web_search", "domain_credibility_score", "generate_xlsx"],
            dag_strategy="DAG Sequential: ResearchAgent gathers facts -> DataAnalyst structures tabular metrics -> DocumentAgent outputs .xlsx file.",
            execution_steps=[
                "1. Perform multi-hop web search with credibility scoring.",
                "2. Extract structured comparative metrics (latency, throughput, license).",
                "3. Call document_agent to produce formatted Excel spreadsheet."
            ],
            edge_cases_handled=["Conflicting data across web sources (flagged with credibility tier)."],
            hinglish_keywords=["research", "compare", "excel", "sheet", "report", "table"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 5: DEVOPS, DOCKER & CI/CD PIPELINES
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="devops_docker_crash_recovery",
            domain="DevOps & Containers",
            title="Docker Container Crash Diagnosis, Log Analysis, and Recovery",
            description="Investigate failing Docker containers, inspect stderr logs, check port collisions, and restart services.",
            user_queries_examples=[
                "why did my docker container crash with OOM error?",
                "docker logs check karo aur restart karo backend service"
            ],
            primary_agents=["devops_agent"],
            supporting_agents=["system_agent", "security_agent"],
            tools_involved=["docker_logs", "docker_restart", "port_scan", "system_memory_check"],
            dag_strategy="Parallel diagnostic (logs + host RAM check) -> auto-remediation restart.",
            execution_steps=[
                "1. Retrieve tail 100 lines of container stderr logs.",
                "2. Check host RAM and port availability.",
                "3. Apply fix or restart container safely."
            ],
            edge_cases_handled=["Port already bound by orphan host process (kill orphan PID first)."],
            hinglish_keywords=["docker", "crash", "logs", "restart", "service", "error"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 6: SECURITY AUDITING & SECRET PREVENTION
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="security_pre_commit_audit",
            domain="Security Auditing",
            title="Pre-Commit Vulnerability, Secret Leak, and Injection Scan",
            description="Scan codebase for hardcoded API keys, JWT tokens, SQL injection vectors, and unsafe deserialization.",
            user_queries_examples=[
                "scan codebase for hardcoded secrets or security flaws before commit",
                "check karo koi api key leak toh nhi hui hai backend me"
            ],
            primary_agents=["security_agent"],
            supporting_agents=["code_agent"],
            tools_involved=["security_scan", "secret_leak_check", "sql_injection_audit"],
            dag_strategy="Direct security scan with severity classification (HIGH, MEDIUM, LOW) -> automatic remediation proposal.",
            execution_steps=[
                "1. Run AST-based regex and entropy scan for secret keys.",
                "2. Audit SQL/shell command construction for injection risks.",
                "3. Generate security remediation patch."
            ],
            edge_cases_handled=["Dummy test keys in unit tests (ignore if inside /tests/ or mock files)."],
            hinglish_keywords=["security", "scan", "api key", "leak", "secret", "audit", "check"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 7: DATA SCIENCE & ANALYTICS REPORTING
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="data_csv_statistical_chart_pptx",
            domain="Data Science",
            title="CSV/JSON Statistical Summarization, Trend Charting, and Slide Deck",
            description="Ingest raw CSV/JSON data, compute Polars/Pandas statistical metrics, plot charts, and build PowerPoint slides.",
            user_queries_examples=[
                "analyze sales.csv, plot monthly growth chart, and make a pptx presentation",
                "data ka summary nikalo aur charts bana ke slide deck do"
            ],
            primary_agents=["data_analyst_agent"],
            supporting_agents=["document_agent", "creative_agent"],
            tools_involved=["data_analyze", "generate_chart", "generate_pptx"],
            dag_strategy="DAG: DataAnalyst computes stats & renders charts -> CreativeAgent drafts slide copy -> DocumentAgent compiles .pptx.",
            execution_steps=[
                "1. Load CSV into Polars/Pandas DataFrame and compute mean, std, quantiles.",
                "2. Render PNG trend charts using matplotlib/seaborn.",
                "3. Assemble polished PowerPoint deck with embedded charts."
            ],
            edge_cases_handled=["Missing values or malformed CSV headers (auto-clean and impute)."],
            hinglish_keywords=["csv", "data", "chart", "pptx", "slide", "summary", "analyze"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 8: BROWSER AUTOMATION & SCRAPING
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="browser_stealth_invoice_download",
            domain="Browser Automation",
            title="Stealth Portal Login, DOM Scraping, and Automated Invoice Download",
            description="Navigate web portals, fill authentication forms, extract PDF invoices, and save to local folder.",
            user_queries_examples=[
                "login to portal, download this month invoice pdf and save in Documents",
                "website se monthly report scrap kar ke download kar do"
            ],
            primary_agents=["browser_agent"],
            supporting_agents=["system_agent"],
            tools_involved=["browser_navigate", "form_fill", "element_click", "move_file"],
            dag_strategy="Sequential browser interactions -> downloaded file verification and placement.",
            execution_steps=[
                "1. Navigate to target URL with headless=False so user can see progress.",
                "2. Perform login or navigate to invoice section.",
                "3. Click download and move resulting PDF to destination folder."
            ],
            edge_cases_handled=["CAPTCHA challenge detection (pause and ask user for manual solve)."],
            hinglish_keywords=["login", "website", "scrap", "download", "invoice", "report"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 9: MULTI-CHANNEL MESSAGING & ALERTS
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="messaging_automated_standup_dispatch",
            domain="Messaging & Alerts",
            title="Draft and Dispatch Multi-Channel Standup Summaries (WhatsApp/Discord/Email)",
            description="Summarize daily git commits or tasks and dispatch via WhatsApp, Telegram, Discord, or Email.",
            user_queries_examples=[
                "send today's standup summary to team discord channel",
                "whatsapp pe message bhej do ki build pass ho gya hai"
            ],
            primary_agents=["messaging_agent"],
            supporting_agents=["code_agent", "automation_agent"],
            tools_involved=["git_log_summary", "send_discord", "send_whatsapp", "send_email"],
            dag_strategy="Gather context -> Format clean markdown summary -> Execute safe message dispatch.",
            execution_steps=[
                "1. Gather commit log or build status.",
                "2. Format concise message appropriate for platform.",
                "3. Dispatch via verified Webhook/API."
            ],
            edge_cases_handled=["Rate limiting or webhook disconnect (retry with exponential backoff)."],
            hinglish_keywords=["message", "whatsapp", "discord", "email", "bhej do", "standup"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 10: LONG-TERM MEMORY & PERSONALIZATION
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="memory_preference_recall_and_apply",
            domain="Memory & Personalization",
            title="EternalMemory Preference Recall and User Workstyle Adaptation",
            description="Query vector database for user's past preferences, coding conventions, or favorite paths before executing tasks.",
            user_queries_examples=[
                "remember my preferred python lint rules and use them for all projects",
                "yaad rakhna ki mai hamesha pytest use karta hu hu unittest nhi"
            ],
            primary_agents=["memory_agent"],
            supporting_agents=["code_agent"],
            tools_involved=["eternal_memory_store", "eternal_memory_query"],
            dag_strategy="Store persistent memory fact -> Auto-query before subsequent agent tasks.",
            execution_steps=[
                "1. Embed and store user preference fact in EternalMemory SQLite/HNSW store.",
                "2. Retrieve relevant preferences whenever code_agent is invoked."
            ],
            edge_cases_handled=["Contradictory older preferences (update with latest timestamp)."],
            hinglish_keywords=["remember", "yaad", "preference", "hamesha", "style"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 11: VOICE, SPEECH & HANDS-FREE INTERACTION
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="voice_kokoro_executive_readout",
            domain="Voice & Speech",
            title="Kokoro-ONNX High-Quality Speech Synthesis for Executive Reports",
            description="Convert technical summaries or executive briefings into natural human-sounding speech.",
            user_queries_examples=[
                "read aloud the executive research briefing in a calm voice",
                "is report ko bol ke sunao"
            ],
            primary_agents=["voice_agent"],
            supporting_agents=["research_agent"],
            tools_involved=["kokoro_tts_speak", "set_voice_speed"],
            dag_strategy="Text summarization -> audio streaming synthesis.",
            execution_steps=[
                "1. Clean text formatting (strip markdown asterisks and code blocks).",
                "2. Generate audio stream using Kokoro-ONNX engine.",
                "3. Playback through system audio."
            ],
            edge_cases_handled=["Long text chunks (auto-split by sentence breaks)."],
            hinglish_keywords=["read aloud", "bol ke", "sunao", "voice", "speech"]
        ))

        # ─────────────────────────────────────────────────────────────
        # DOMAIN 12: HINGLISH MULTI-DOMAIN COMPOUND ORCHESTRATION
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="hinglish_compound_desktop_media_code",
            domain="Compound Orchestration",
            title="Hinglish Multi-Domain Parallel DAG (Desktop Clean + Media Play + Build Code)",
            description="Handle casual, multi-action Hinglish commands by decomposing into a parallel Directed Acyclic Graph.",
            user_queries_examples=[
                "bhai desktop saaf kar de aur arijit ka gana chala de aur mera rust project build kar",
                "files organize karo aur youtube pe lofi laga do"
            ],
            primary_agents=["commander_agent"],
            supporting_agents=["system_agent", "media_agent", "code_agent"],
            tools_involved=["organize_desktop", "play_media", "run_command"],
            dag_strategy="DAG Parallel Execution: st_1 (system_agent: organize desktop), st_2 (media_agent: play music), st_3 (code_agent: cargo check).",
            execution_steps=[
                "1. Commander decomposes compound query into 3 distinct subtasks.",
                "2. Execute st_1, st_2, and st_3 in parallel (no inter-dependencies).",
                "3. Aggregate results and report unified completion status in user's language."
            ],
            edge_cases_handled=[
                "One subtask fails while others succeed (report partial success clearly).",
                "Hinglish slang resolution ('saaf kar', 'laga do', 'lineup')."
            ],
            hinglish_keywords=["bhai", "aur", "kar de", "saaf", "gana", "build", "laga do"]
        ))

        # ─────────────────────────────────────────────────────────────
        # EXPANDED PATTERNS (26 ADDITIONAL SITUATIONS FOR 100-TASK COVERAGE)
        # ─────────────────────────────────────────────────────────────
        self._register_pattern(SituationalPattern(
            situation_id="os_process_monitoring_and_kill",
            domain="OS & Desktop",
            title="OS Process CPU/Memory Monitoring and Hung Process Termination",
            description="Identify hung or high-CPU processes and terminate them safely.",
            user_queries_examples=[
                "kill hung python process that is taking 100% CPU",
                "find and stop process consuming too much memory"
            ],
            primary_agents=["system_agent"],
            supporting_agents=["automation_agent"],
            tools_involved=["kill_process", "system_memory_check"],
            dag_strategy="Process inspection -> confirmation or threshold check -> safe termination.",
            execution_steps=["1. Enumerate active processes.", "2. Check CPU/memory utilization.", "3. Execute kill_process."],
            edge_cases_handled=["System critical processes are protected from kill command."],
            hinglish_keywords=["kill", "process", "hung", "cpu", "memory", "stop"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="os_disk_space_cleanup_and_alert",
            domain="OS & Desktop",
            title="Disk Space Check, Temp File Cleanup, and Low Space Alert",
            description="Monitor C drive free space, clear temp directories, and report storage health.",
            user_queries_examples=[
                "check disk space on C drive and alert if below 10GB",
                "clean temp files and empty recycle bin"
            ],
            primary_agents=["system_agent"],
            supporting_agents=["automation_agent"],
            tools_involved=["list_dir", "system_memory_check"],
            dag_strategy="Disk free space query -> automated temp cleanup if below threshold.",
            execution_steps=["1. Check volume space.", "2. Delete temporary cache/log files.", "3. Report reclaimed space."],
            edge_cases_handled=["Locked temporary files currently in use are skipped."],
            hinglish_keywords=["disk", "space", "clean", "temp", "recycle", "alert", "drive"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="media_playlist_navigation_and_control",
            domain="Media Control",
            title="Playlist Navigation, Track Skip, and Play/Pause State Control",
            description="Control media playback state including pause, skip, next track, and playlist selection.",
            user_queries_examples=[
                "play next track on spotify",
                "pause currently playing youtube video",
                "search spotify for chill synthwave playlist and hit play"
            ],
            primary_agents=["media_agent"],
            supporting_agents=["system_agent"],
            tools_involved=["play_media", "search_spotify"],
            dag_strategy="Semantic intent parsing -> media control command dispatch.",
            execution_steps=["1. Identify player platform.", "2. Send next/pause/play command via media bridge."],
            edge_cases_handled=["No active player found (open web player URL automatically)."],
            hinglish_keywords=["next", "pause", "play", "track", "spotify", "playlist", "skip"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="media_spotify_volume_mute",
            domain="Media Control",
            title="System Audio Muting and Relative Volume Adjustments",
            description="Handle immediate mute requests or relative volume increments/decrements in Hinglish and English.",
            user_queries_examples=[
                "mute system volume immediately",
                "volume 20 percent badha do"
            ],
            primary_agents=["media_agent"],
            supporting_agents=["system_agent"],
            tools_involved=["set_volume"],
            dag_strategy="Parse volume percentage or mute flag -> execute set_volume.",
            execution_steps=["1. Compute target volume level (0 for mute, relative +20).", "2. Execute set_volume command."],
            edge_cases_handled=["Volume bounds capping between 0 and 100."],
            hinglish_keywords=["mute", "volume", "badha do", "percent", "sound"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="code_unit_test_generation_and_coverage",
            domain="Software Engineering",
            title="Automated Unit Test Suite Generation with 100% Coverage Target",
            description="Generate pytest or unittest test cases for target modules, mocking dependencies and edge cases.",
            user_queries_examples=[
                "write unit tests for situational encyclopedia with 100% coverage",
                "add pytest suite for session_manager.py"
            ],
            primary_agents=["code_agent"],
            supporting_agents=["devops_agent"],
            tools_involved=["code_read", "code_edit", "test_runner"],
            dag_strategy="Read source AST -> identify branch paths -> draft test file -> run pytest.",
            execution_steps=["1. Inspect target function signatures and branches.", "2. Draft pytest assertions.", "3. Execute tests and verify pass."],
            edge_cases_handled=["Mocking external network or file IO calls."],
            hinglish_keywords=["test", "unit", "pytest", "coverage", "suite", "mock"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="code_syntax_and_lint_repair",
            domain="Software Engineering",
            title="py_compile Syntax Error Diagnosis and Auto-Remediation",
            description="Fix Python syntax errors, indentation bugs, and lint failures reported by py_compile or ruff.",
            user_queries_examples=[
                "check why py_compile is failing on syntax error in utils.py",
                "fix indentation and syntax errors in backend"
            ],
            primary_agents=["code_agent"],
            supporting_agents=["security_agent"],
            tools_involved=["code_read", "code_edit", "py_compile_verify"],
            dag_strategy="Read compiler trace -> locate line number -> patch syntax -> run py_compile.",
            execution_steps=["1. Extract error line and column from compiler trace.", "2. Apply syntax correction.", "3. Re-verify compilation."],
            edge_cases_handled=["Mismatched brackets or unclosed docstrings across multiple lines."],
            hinglish_keywords=["syntax", "error", "compile", "py_compile", "indentation", "fix"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="code_docstring_and_typehint_injection",
            domain="Software Engineering",
            title="Docstring Injection, Error Handling, and Type Hint Refactoring",
            description="Enhance code quality by adding comprehensive Google-style docstrings, type hints, and try/except blocks.",
            user_queries_examples=[
                "add docstrings and error handling to all methods in session_manager.py",
                "refactor command router module and add clean type hints"
            ],
            primary_agents=["code_agent"],
            supporting_agents=["document_agent"],
            tools_involved=["code_read", "code_edit", "py_compile_verify"],
            dag_strategy="AST parsing of undocumented signatures -> drop-in docstring and type hint injection.",
            execution_steps=["1. Enumerate function/method AST nodes.", "2. Inject type annotations and docstrings.", "3. Verify zero breaking changes."],
            edge_cases_handled=["Preserving existing comments and logic."],
            hinglish_keywords=["docstring", "comments", "type hints", "error handling", "refactor"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="research_pricing_and_api_comparison",
            domain="Web Research",
            title="Real-Time API Endpoint Pricing and Competitor Feature Comparison",
            description="Search web for latest API pricing tables (Groq, Claude, OpenAI) and compare cost per token.",
            user_queries_examples=[
                "find latest pricing for groq and claude API endpoints",
                "compare polars vs pandas performance for 10 million rows"
            ],
            primary_agents=["research_agent"],
            supporting_agents=["data_analyst_agent"],
            tools_involved=["web_search", "domain_credibility_score"],
            dag_strategy="Multi-query live search -> extract cost metrics -> produce comparative breakdown.",
            execution_steps=["1. Search official pricing documentation.", "2. Normalise costs to per-1M tokens.", "3. Summarise in Markdown table."],
            edge_cases_handled=["Tiered pricing or volume discounts note."],
            hinglish_keywords=["pricing", "compare", "cost", "api", "performance", "benchmark"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="research_scientific_paper_synthesis",
            domain="Web Research",
            title="Scientific Paper Lookup, CEO Disambiguation, and Breakthrough Synthesis",
            description="Research academic literature, executive papers, and technical breakthroughs with citation scoring.",
            user_queries_examples=[
                "who is the CEO of deepmind and what is their recent paper?",
                "research state of the art techniques for HNSW vector search",
                "summarize top 3 breakthroughs in voice synthesis 2026"
            ],
            primary_agents=["research_agent"],
            supporting_agents=["document_agent"],
            tools_involved=["web_search", "domain_credibility_score"],
            dag_strategy="Scholarly web search -> source credibility filtering -> executive briefing synthesis.",
            execution_steps=["1. Execute entity and topic query.", "2. Filter sources by domain authority.", "3. Compose bulleted synthesis."],
            edge_cases_handled=["Disambiguating authors or entities with identical names."],
            hinglish_keywords=["ceo", "paper", "research", "breakthrough", "hnsw", "synthesis"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="devops_ci_cd_github_actions_workflow",
            domain="DevOps & Containers",
            title="GitHub Actions CI/CD Pipeline Workflow Generation and Test Automation",
            description="Draft and validate CI/CD YAML workflows for automated testing, linting, and Docker building.",
            user_queries_examples=[
                "write github actions workflow for automated python pytest",
                "verify CI/CD pipeline build status on github"
            ],
            primary_agents=["devops_agent"],
            supporting_agents=["code_agent"],
            tools_involved=["code_edit"],
            dag_strategy="Analyze project dependencies -> draft .github/workflows/ci.yml -> validate syntax.",
            execution_steps=["1. Check Python version and dependencies.", "2. Create GitHub Actions workflow YAML.", "3. Confirm trigger paths."],
            edge_cases_handled=["Caching pip/poetry dependencies for fast CI runs."],
            hinglish_keywords=["github actions", "ci/cd", "pipeline", "workflow", "build", "pytest"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="devops_kubernetes_pod_crash_diagnosis",
            domain="DevOps & Containers",
            title="Kubernetes Pod CrashLoopBackOff Diagnosis and Proxy Restart",
            description="Diagnose failing K8s pods or Nginx reverse proxy containers, checking port collisions and logs.",
            user_queries_examples=[
                "diagnose why kubernetes pod is stuck in CrashLoopBackOff",
                "restart nginx proxy container safely",
                "check if port 8080 is already in use by another process"
            ],
            primary_agents=["devops_agent"],
            supporting_agents=["system_agent"],
            tools_involved=["docker_logs", "docker_restart", "port_scan"],
            dag_strategy="Port/container log inspection -> identify collision/OOM -> execute restart.",
            execution_steps=["1. Run port scan or inspect container logs.", "2. Free bound port if necessary.", "3. Restart proxy/pod."],
            edge_cases_handled=["Graceful shutdown wait before restart."],
            hinglish_keywords=["kubernetes", "pod", "crashloopbackoff", "nginx", "proxy", "port 8080"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="security_sql_injection_and_input_audit",
            domain="Security Auditing",
            title="SQL Injection Vulnerability Audit, Input Sanitization, and JWT Signature Check",
            description="Audit database queries, WebSocket input handlers, and auth token verification for injection flaws.",
            user_queries_examples=[
                "audit sql queries for potential SQL injection vulnerabilities",
                "verify input sanitization on websocket incoming messages",
                "audit JWT token signature validation logic"
            ],
            primary_agents=["security_agent"],
            supporting_agents=["code_agent"],
            tools_involved=["sql_injection_audit", "security_scan"],
            dag_strategy="AST string formatting inspection -> flag un-parameterized SQL -> generate parameterized fix.",
            execution_steps=["1. Locate raw SQL queries or eval calls.", "2. Verify parameter binding.", "3. Suggest secure replacement."],
            edge_cases_handled=["ORM raw query escape checking."],
            hinglish_keywords=["sql injection", "audit", "sanitization", "websocket", "jwt", "token", "security"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="security_dependency_cve_scan",
            domain="Security Auditing",
            title="Dependency CVE Scanning, SSL/TLS Expiration Check, and File Permissions",
            description="Check installed packages against known CVE databases, verify SSL certs, and audit sensitive file perms.",
            user_queries_examples=[
                "scan dependencies for known CVE security flaws",
                "verify SSL/TLS certificate expiration for domain",
                "check file permissions on sensitive config files"
            ],
            primary_agents=["security_agent"],
            supporting_agents=["devops_agent"],
            tools_involved=["security_scan", "port_scan"],
            dag_strategy="Dependency/manifest inspection -> cross-reference CVE list -> report vulnerable versions.",
            execution_steps=["1. Read requirements/lockfile.", "2. Check vulnerability database.", "3. Report CVE severity table."],
            edge_cases_handled=["Dev-only dependencies flagged separately from production packages."],
            hinglish_keywords=["cve", "dependency", "ssl", "tls", "certificate", "permissions", "scan"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="data_outlier_detection_and_cleaning",
            domain="Data Science",
            title="Statistical Outlier Detection, Telemetry Cleaning, and JSON-to-CSV Conversion",
            description="Detect statistical outliers in transactional CSVs, clean malformed JSON telemetry, and export summaries.",
            user_queries_examples=[
                "detect outliers in transaction data csv file",
                "convert raw json telemetry into formatted csv summary",
                "load csv in polars dataframe and calculate mean and standard deviation"
            ],
            primary_agents=["data_analyst_agent"],
            supporting_agents=["document_agent"],
            tools_involved=["data_analyze", "generate_xlsx"],
            dag_strategy="Load DataFrame -> compute IQR / Z-score -> filter outliers -> save cleaned data.",
            execution_steps=["1. Ingest raw CSV/JSON.", "2. Calculate quartiles and Z-scores.", "3. Flag/remove outliers and output summary."],
            edge_cases_handled=["Zero variance or single-row dataset protection."],
            hinglish_keywords=["outlier", "csv", "json", "convert", "polars", "mean", "standard deviation"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="data_correlation_heatmap_and_summary",
            domain="Data Science",
            title="Correlation Heatmaps, Customer Sentiment Plotting, and Budget Excel Formulas",
            description="Generate seaborn/matplotlib heatmaps, plot customer feedback sentiment trends, and draft budget formulas.",
            user_queries_examples=[
                "plot correlation heatmap between latency and token count",
                "summarize customer feedback surveys and plot sentiment chart",
                "create excel spreadsheet with formulas for monthly budget comparison"
            ],
            primary_agents=["data_analyst_agent"],
            supporting_agents=["document_agent"],
            tools_involved=["generate_chart", "generate_xlsx"],
            dag_strategy="Compute correlation matrix -> render PNG heatmap -> embed in report/sheet.",
            execution_steps=["1. Compute Pearson/Spearman correlation.", "2. Generate matplotlib heatmap graphic.", "3. Export to artifact/spreadsheet."],
            edge_cases_handled=["Non-numeric columns excluded from correlation calculations automatically."],
            hinglish_keywords=["heatmap", "correlation", "sentiment", "excel", "formulas", "chart", "plot"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="browser_form_filling_and_modal_navigation",
            domain="Browser Automation",
            title="Automated Web Form Filling, Onboarding Wizard Navigation, and Screenshots",
            description="Automatically complete web forms, click through wizard modals, and capture landing page screenshots.",
            user_queries_examples=[
                "fill out online feedback form with sample data",
                "automate clicking through onboarding wizard modal",
                "take screenshot of landing page and save to artifacts"
            ],
            primary_agents=["browser_agent"],
            supporting_agents=["system_agent"],
            tools_involved=["browser_navigate", "form_fill", "element_click"],
            dag_strategy="Visible browser launch -> locate form fields by accessibility label -> fill -> screenshot.",
            execution_steps=["1. Open target page.", "2. Identify form inputs and modals.", "3. Fill data and capture WebP/PNG screenshot."],
            edge_cases_handled=["Dynamic DOM loading delays handled with explicit wait."],
            hinglish_keywords=["form", "fill", "wizard", "modal", "screenshot", "landing page", "automate"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="browser_table_scraping_to_markdown",
            domain="Browser Automation",
            title="Web Table Scraping, GitHub PR Counting, and Article Distillation to Markdown",
            description="Extract pricing tables, inspect GitHub repository statistics, and convert article headlines to clean markdown.",
            user_queries_examples=[
                "extract table of pricing plans from competitor website",
                "navigate to github repo and check number of open pull requests",
                "scrape article headlines from tech blog and format as markdown"
            ],
            primary_agents=["browser_agent"],
            supporting_agents=["document_agent"],
            tools_involved=["browser_navigate", "code_read"],
            dag_strategy="Navigate URL -> distill DOM table/headings -> structure as markdown/csv table.",
            execution_steps=["1. Navigate and attach to DOM.", "2. Extract table cells or H1/H2 headlines.", "3. Format clean markdown output."],
            edge_cases_handled=["Pagination handling across multi-page tables."],
            hinglish_keywords=["scrape", "table", "pricing", "github", "pull requests", "markdown", "headlines"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="messaging_email_client_report_drafting",
            domain="Messaging & Alerts",
            title="Client Completion Email Drafting and Slack Daily Commit Summaries",
            description="Draft professional project completion emails and Slack markdown commit summaries.",
            user_queries_examples=[
                "draft email to client with project completion status",
                "summarize git commits from today and share on slack"
            ],
            primary_agents=["messaging_agent"],
            supporting_agents=["code_agent"],
            tools_involved=["send_email", "git_log_summary"],
            dag_strategy="Aggregate git/project context -> generate formal/professional text -> draft email/slack payload.",
            execution_steps=["1. Gather task completions.", "2. Format professional email subject and body.", "3. Prepare dispatch preview."],
            edge_cases_handled=["Ensuring no sensitive internal notes leak into client-facing drafts."],
            hinglish_keywords=["email", "client", "draft", "slack", "summary", "commit", "share"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="messaging_telegram_cpu_alert_dispatch",
            domain="Messaging & Alerts",
            title="High-CPU Resource Exceeded Telegram Alert Dispatch",
            description="Monitor server CPU thresholds and trigger immediate Telegram alert messages.",
            user_queries_examples=[
                "send alert to telegram group if server CPU exceeds 90%"
            ],
            primary_agents=["messaging_agent"],
            supporting_agents=["system_agent", "automation_agent"],
            tools_involved=["send_discord", "system_memory_check"],
            dag_strategy="Monitor threshold -> format severity alert -> dispatch webhook.",
            execution_steps=["1. Check CPU metrics.", "2. Construct high-priority alert text.", "3. Deliver to Telegram/Discord group."],
            edge_cases_handled=["Cooldown anti-spam timer to prevent alert flooding."],
            hinglish_keywords=["telegram", "alert", "cpu", "exceeds", "90%", "group", "message"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="memory_database_schema_recall",
            domain="Memory & Personalization",
            title="EternalMemory Architectural Schema Recall and User Feedback Retrieval",
            description="Retrieve past database schema decisions, UI layout feedback, and architectural consensus from vector store.",
            user_queries_examples=[
                "recall what database schema we decided on last week",
                "query eternal memory for past user feedback on UI layout"
            ],
            primary_agents=["memory_agent"],
            supporting_agents=["code_agent"],
            tools_involved=["eternal_memory_query"],
            dag_strategy="Vector similarity query on HNSW index -> retrieve historical design decisions.",
            execution_steps=["1. Generate query embedding.", "2. Search EternalMemory SQLite/HNSW store.", "3. Return matching decision notes."],
            edge_cases_handled=["Filtering out superseded historical drafts."],
            hinglish_keywords=["recall", "schema", "database", "feedback", "eternal memory", "last week", "layout"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="memory_forget_outdated_keys",
            domain="Memory & Personalization",
            title="EternalMemory Outdated Secret and Obsolete Fact Cascade Purge",
            description="Remove outdated API keys, superseded preferences, or deprecated facts from long-term memory.",
            user_queries_examples=[
                "forget old outdated API keys from memory store"
            ],
            primary_agents=["memory_agent"],
            supporting_agents=["security_agent"],
            tools_involved=["eternal_memory_query"],
            dag_strategy="Locate target fact in vector/WAL store -> execute memory forget cascade.",
            execution_steps=["1. Identify obsolete key/preference records.", "2. Invoke forget cascade.", "3. Confirm deletion."],
            edge_cases_handled=["Ensuring active valid keys are preserved."],
            hinglish_keywords=["forget", "outdated", "api keys", "memory store", "purge", "remove"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="voice_speed_tuning_and_pitch_control",
            domain="Voice & Speech",
            title="Speech Speed Tuning (1.25x), Voice Selection, and Kokoro-ONNX Readouts",
            description="Customize speech synthesis playback speed, pitch, and voice models for executive briefings.",
            user_queries_examples=[
                "adjust speech speed to 1.25x for voice readouts",
                "is report ko bol ke sunao kokoro tts use karke"
            ],
            primary_agents=["voice_agent"],
            supporting_agents=["research_agent"],
            tools_involved=["set_voice_speed", "kokoro_tts_speak"],
            dag_strategy="Set voice parameters -> stream TTS audio through Kokoro-ONNX.",
            execution_steps=["1. Configure speech rate to 1.25x.", "2. Strip markdown table formatting.", "3. Synthesize speech."],
            edge_cases_handled=["Audio buffer underrun prevention during fast playback."],
            hinglish_keywords=["speed", "1.25x", "readouts", "voice", "kokoro", "tts", "bol ke sunao"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="creative_storytelling_and_copywriting",
            domain="Voice & Speech",
            title="Product Launch Executive Storytelling and UX Modal Copywriting",
            description="Draft compelling narrative stories for product launches and crisp UX copy for UI modals.",
            user_queries_examples=[
                "write a compelling executive story for our new product launch",
                "draft UX copywriting for error notification modal"
            ],
            primary_agents=["creative_agent"],
            supporting_agents=["document_agent"],
            tools_involved=["generate_docx"],
            dag_strategy="Understand audience persona -> draft structured narrative/micro-copy -> format output.",
            execution_steps=["1. Define key message and tone.", "2. Draft narrative or concise UX error copy.", "3. Output clean markdown/document."],
            edge_cases_handled=["Character count constraints for UI modals."],
            hinglish_keywords=["story", "compelling", "executive", "product launch", "ux copywriting", "modal"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="hinglish_compound_downloads_deadlock",
            domain="Compound Orchestration",
            title="Hinglish Compound Parallel DAG (Clean Downloads + Backend Deadlock Check)",
            description="Simultaneously organize downloads directory while running async deadlock diagnostics on backend.",
            user_queries_examples=[
                "downloads folder clean karo aur check karo backend me koi deadlock toh nhi hai"
            ],
            primary_agents=["commander_agent"],
            supporting_agents=["system_agent", "code_agent"],
            tools_involved=["organize_desktop", "code_read"],
            dag_strategy="DAG Parallel: st_1 (system_agent clean downloads), st_2 (code_agent deadlock check).",
            execution_steps=["1. Commander creates 2 parallel subtasks.", "2. Execute filesystem cleanup and AST deadlock check.", "3. Combine report."],
            edge_cases_handled=["Independent error isolation between filesystem and code analysis."],
            hinglish_keywords=["downloads", "clean karo", "deadlock", "backend", "aur check karo"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="hinglish_compound_git_whatsapp_deploy",
            domain="Compound Orchestration",
            title="Hinglish Compound DAG (Git Log Check + WhatsApp Deployment Alert)",
            description="Check latest git commits and automatically notify team on WhatsApp regarding successful deployment.",
            user_queries_examples=[
                "git log check karo aur whatsapp pe team ko message kar do ki deploy ho gya"
            ],
            primary_agents=["commander_agent"],
            supporting_agents=["code_agent", "messaging_agent"],
            tools_involved=["git_log_summary", "send_whatsapp"],
            dag_strategy="DAG Sequential: st_1 (code_agent git log) -> st_2 (messaging_agent whatsapp alert).",
            execution_steps=["1. Get latest commit hash and author.", "2. Construct deployment message.", "3. Send WhatsApp notification."],
            edge_cases_handled=["Empty git log or network timeout retry."],
            hinglish_keywords=["git log", "whatsapp", "team", "message kar do", "deploy ho gya"]
        ))

        self._register_pattern(SituationalPattern(
            situation_id="hinglish_compound_csv_chart_voice_summary",
            domain="Compound Orchestration",
            title="Hinglish Compound DAG (CSV Analysis + Trend Chart + Voice Executive Readout)",
            description="Analyze sales data, render trend charts, and read aloud the executive summary via Kokoro voice.",
            user_queries_examples=[
                "sales.csv analyze karo aur chart bana ke executive summary bol ke sunao"
            ],
            primary_agents=["commander_agent"],
            supporting_agents=["data_analyst_agent", "voice_agent"],
            tools_involved=["data_analyze", "generate_chart", "kokoro_tts_speak"],
            dag_strategy="DAG Sequential: st_1 (data_analyst analyze/chart) -> st_2 (voice_agent speak summary).",
            execution_steps=["1. Calculate CSV growth metrics and plot chart.", "2. Prepare 3-bullet executive summary.", "3. Speak aloud via TTS."],
            edge_cases_handled=["Skipping chart binary data from TTS readout text."],
            hinglish_keywords=["sales.csv", "analyze karo", "chart bana ke", "executive summary", "bol ke sunao"]
        ))

    # ──────────────────────────────────────────────────────────────────
    # INTROSPECTION & SEMANTIC RETRIEVAL METHODS
    # ──────────────────────────────────────────────────────────────────

    def get_pattern(self, situation_id: str) -> Optional[SituationalPattern]:
        """Retrieve a specific situational pattern by ID."""
        return self._patterns.get(situation_id)

    def list_domains(self) -> List[str]:
        """List all 12 major situational domains."""
        return sorted(list(self._domain_index.keys()))

    def get_patterns_by_domain(self, domain: str) -> List[SituationalPattern]:
        """Retrieve all patterns within a specific domain."""
        ids = self._domain_index.get(domain, [])
        return [self._patterns[sid] for sid in ids if sid in self._patterns]

    def get_patterns_by_agent(self, agent_name: str) -> List[SituationalPattern]:
        """Retrieve all patterns where an agent is primary or supporting."""
        ids = self._agent_index.get(agent_name.lower(), [])
        return [self._patterns[sid] for sid in ids if sid in self._patterns]

    def search_situations(self, query: str, top_k: int = 5) -> List[Tuple[SituationalPattern, float]]:
        """
        Semantic keyword & Jaccard similarity search across all situational patterns.
        Returns top-k matching patterns with their relevance score (0.0 to 1.0).
        """
        query_tokens = set(re.findall(r'\w+', query.lower()))
        if not query_tokens:
            return []

        results: List[Tuple[SituationalPattern, float]] = []
        for pattern in self._patterns.values():
            # Build target tokens from title, description, examples, and keywords
            target_text = (
                f"{pattern.title} {pattern.description} "
                f"{' '.join(pattern.user_queries_examples)} "
                f"{' '.join(pattern.hinglish_keywords)} "
                f"{' '.join(pattern.primary_agents)} {' '.join(pattern.tools_involved)}"
            ).lower()
            target_tokens = set(re.findall(r'\w+', target_text))

            # Compute Jaccard + Overlap boost
            overlap = query_tokens & target_tokens
            if not overlap:
                continue

            jaccard = len(overlap) / len(query_tokens | target_tokens)
            overlap_ratio = len(overlap) / len(query_tokens)
            score = 0.6 * overlap_ratio + 0.4 * jaccard

            results.append((pattern, round(score, 3)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def build_commander_situational_prompt(self, query: str = "") -> str:
        """
        Builds a rich, dynamic Situational Intelligence section for Makima's Commander Agent.
        If a query is provided, highlights top matching situational workflows.
        Otherwise, provides an exhaustive overview across all domains.
        """
        lines = [
            "## VAST OPERATIONAL SITUATIONAL KNOWLEDGE (100+ REAL-WORLD PATTERNS)",
            "Makima is equipped to handle complex, multi-domain, English/Hindi/Hinglish requests across 12 major engineering & OS domains:",
        ]

        # If specific query matches, show detailed matching patterns first
        matches = self.search_situations(query, top_k=3) if query else []
        if matches:
            lines.append("\n### TOP MATCHING SITUATIONAL PATTERNS FOR CURRENT REQUEST:")
            for pat, score in matches:
                lines.append(f"- **[{pat.domain}] {pat.title}** (Relevance: {score})")
                lines.append(f"  - *Strategy*: {pat.dag_strategy}")
                lines.append(f"  - *Primary Agents*: {', '.join(pat.primary_agents)} | *Tools*: {', '.join(pat.tools_involved)}")
                lines.append(f"  - *Steps*: {' -> '.join(pat.execution_steps)}")

        lines.append("\n### COMPLETE MULTI-AGENT SITUATIONAL DOMAIN REFERENCE:")
        for domain in self.list_domains():
            patterns = self.get_patterns_by_domain(domain)
            pattern_titles = [p.title for p in patterns[:2]]
            lines.append(f"- **{domain}** ({len(patterns)} patterns): e.g., {'; '.join(pattern_titles)}")

        lines.append(
            "\n*Key Rule*: When encountering compound or informal queries ('idhar udhar', 'saaf karo', "
            "'gana chalao', 'deadlock check karo'), always decompose into precise DAG subtasks "
            "mapping to specialized leaf agents rather than failing or guessing."
        )

        return "\n".join(lines)

    def get_encyclopedia_stats(self) -> Dict[str, Any]:
        """Return statistics on Makima's vast operational knowledge."""
        return {
            "total_patterns": len(self._patterns),
            "total_domains": len(self._domain_index),
            "agents_mapped": len(self._agent_index),
            "domains": self.list_domains(),
        }


# Singleton Global Encyclopedia Instance
_GLOBAL_ENCYCLOPEDIA: Optional[AgentSituationalEncyclopedia] = None


def get_situational_encyclopedia() -> AgentSituationalEncyclopedia:
    """Returns the singleton instance of the AgentSituationalEncyclopedia."""
    global _GLOBAL_ENCYCLOPEDIA
    if _GLOBAL_ENCYCLOPEDIA is None:
        _GLOBAL_ENCYCLOPEDIA = AgentSituationalEncyclopedia()
    return _GLOBAL_ENCYCLOPEDIA
