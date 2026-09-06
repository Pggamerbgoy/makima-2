#!/usr/bin/env python3
"""
EXPANDED AUTONOMOUS TOOL-USE & WORKFLOW DISCOVERY BENCHMARK V2
============================================================
Evaluation Target: DeepSeek V4 Flash (deepseek-v4-flash) via Alibaba DashScope Intl
Registry: Standard 28 tools across Browser, System, Filesystem, Media, Process, Network, and Verification.

Fairness & Rigor:
- Zero workflow hints, zero DAG annotations, zero step enumeration.
- Models must autonomously discover intermediate steps (Class B actions).
- Realistic mock environment with flexible valid paths and proper state tracking.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Callable

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# -----------------------------------------------------------------------------
# Configuration & Endpoints
# -----------------------------------------------------------------------------
API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
WORKING_API_KEY = "sk-ws-H.DMEXXHX.1XMv.MEYCIQCOSaps08F94rLHKMMQBGVzE2mEll7cQcsOG72CrYDbUQIhAI2M9WN6zYRpequzPCJkQl-LfuaoJyDYUzQUB8QhiLMK"

MODELS_UNDER_TEST = [
    {
        "name": "DeepSeek V4 Flash",
        "model_id": "deepseek-v4-flash",
        "api_key": WORKING_API_KEY,
        "endpoint_url": API_URL,
    },
]

# -----------------------------------------------------------------------------
# FIXED 28-TOOL BENCHMARK REGISTRY
# -----------------------------------------------------------------------------
COMMON_TOOL_REGISTRY = [
    # Browser Tools
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate the browser to the specified URL.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "Target web address"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_page",
            "description": "Inspect and return the current page title, URL, interactive DOM elements, forms, and status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an interactive element (button, link, tab) matching target selector or description.",
            "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "Target element selector, text, or id"}}, "required": ["target"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "Fill an input field with the specified value.",
            "parameters": {"type": "object", "properties": {"field": {"type": "string", "description": "Field selector, id, or label"}, "value": {"type": "string", "description": "Value to input"}}, "required": ["field", "value"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_back",
            "description": "Navigate back to the previous page in history.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_find",
            "description": "Search text occurrences on the current page.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "Text pattern to find"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract",
            "description": "Extract text or structured data from the page matching selector or description.",
            "parameters": {"type": "object", "properties": {"selector_or_description": {"type": "string", "description": "CSS selector or text description of data to extract"}}, "required": ["selector_or_description"]},
        },
    },
    # System / App Tools
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Launch an installed desktop application.",
            "parameters": {"type": "object", "properties": {"app_name": {"type": "string", "description": "Name or executable of the application"}}, "required": ["app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_installed_apps",
            "description": "Search installed applications by keyword.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Application search query"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_window",
            "description": "Manage desktop application window state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["minimize", "maximize", "focus", "close"], "description": "Window action"},
                    "title": {"type": "string", "description": "Window title or application name"},
                },
                "required": ["action", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_process_list",
            "description": "List all active processes with PID, CPU %, and memory usage.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_stats",
            "description": "Retrieve current CPU load %, RAM usage %, disk capacity, and uptime.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # Filesystem Tools
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents from local filesystem.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Path to target file"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite content to local filesystem.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Destination file path"}, "content": {"type": "string", "description": "Content string to write"}}, "required": ["path", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move a file from source to destination.",
            "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}, "required": ["source", "destination"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a file from source to destination.",
            "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}, "required": ["source", "destination"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_file",
            "description": "Rename a file or directory.",
            "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}, "required": ["source", "destination"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_exists",
            "description": "Check if a file or directory exists at the given path.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    # Media Tools
    {
        "type": "function",
        "function": {
            "name": "play_media",
            "description": "Start or resume media playback with optional search query.",
            "parameters": {"type": "object", "properties": {"query_or_title": {"type": "string", "description": "Track, video, or playlist title"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_media",
            "description": "Pause all active media and audio playback.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set system master volume percentage (0-100).",
            "parameters": {"type": "object", "properties": {"level": {"type": "integer", "description": "Volume percentage (0-100)"}}, "required": ["level"]},
        },
    },
    # Process Tools
    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": "Terminate a running process by process name or executable.",
            "parameters": {"type": "object", "properties": {"process_name": {"type": "string"}}, "required": ["process_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_process_priority",
            "description": "Set CPU priority class for a process (low, normal, high, realtime).",
            "parameters": {"type": "object", "properties": {"process_name": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "normal", "high", "realtime"]}}, "required": ["process_name", "priority"]},
        },
    },
    # Network / Research Tools
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Perform live internet web search for queries.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch HTTP/HTTPS content or API payload from remote URL.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    # Verification / State Tools
    {
        "type": "function",
        "function": {
            "name": "get_open_windows",
            "description": "List all active desktop application windows and focus state.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_process_info",
            "description": "Get detailed runtime info (threads, memory, priority, status) for a specific process.",
            "parameters": {"type": "object", "properties": {"process_name": {"type": "string"}}, "required": ["process_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_info",
            "description": "Get file metadata including size, checksum, modification timestamp, and permissions.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
]

# -----------------------------------------------------------------------------
# Test Case Definition & Test Suite
# -----------------------------------------------------------------------------
@dataclass
class DiscoveryTestCaseV2:
    task_id: str
    category: str
    goal: str
    expected_necessary_steps: List[str]
    verification_tools: List[str]
    mock_env_handler: Callable[[str, Dict[str, Any], Dict[str, Any]], tuple[str, bool]]
    initial_env_state: Dict[str, Any] = field(default_factory=dict)
    is_browser_task: bool = False
    requires_recovery: bool = False

def build_v2_test_suite() -> List[DiscoveryTestCaseV2]:
    suite: List[DiscoveryTestCaseV2] = []

    # =========================================================================
    # A. BROWSER WORKFLOW DISCOVERY (5 Tasks)
    # =========================================================================

    # AWD2-01: Analytics Portal Login -> Dashboard -> Extract Revenue -> Save File
    def h_awd2_01(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            state["url"] = args.get("url", "")
            return "Loaded https://analytics.internal/login. Page Title: 'SSO Login'. Elements: input#username, input#password, button#login-btn (Demo credentials available).", True
        if tool == "browser_get_page":
            if state.get("logged_in"):
                return "Page Title: Executive Analytics Dashboard. URL: https://analytics.internal/dashboard. Metrics: Q3 GAAP Revenue: $14.25M (#q3-revenue), Margin: 24.2%.", True
            return "Page Title: SSO Login. URL: https://analytics.internal/login. Elements: input#username, input#password, button#login-btn.", True
        if tool == "browser_fill":
            field = args.get("field", "")
            val = args.get("value", "")
            state[f"field_{field}"] = val
            return f"Filled {field} with '{val}'.", True
        if tool == "browser_click":
            state["logged_in"] = True
            return "Authenticated successfully. Redirected to https://analytics.internal/dashboard. Dashboard data loaded: Q3 GAAP Revenue is $14,250,000 USD.", True
        if tool == "browser_extract":
            return "$14,250,000 USD (Q3 GAAP Net Revenue)", True
        if tool == "write_file":
            state["saved_file"] = args.get("path")
            return f"Wrote content to {args.get('path')}.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-01",
        category="A. Browser Workflow Discovery",
        goal="Get the Q3 revenue figure from the analytics portal at https://analytics.internal and save it to reports/q3.txt.",
        expected_necessary_steps=["browser_navigate", "write_file"],
        verification_tools=[],
        mock_env_handler=h_awd2_01,
        is_browser_task=True,
    ))

    # AWD2-02: Hardware Catalog Search & Stock Filter -> Extract Price -> Write Quote
    def h_awd2_02(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            return "Loaded https://hardware.corp/catalog. Total items: 1,420. Filter elements: input#search, checkbox#in-stock-only.", True
        if tool == "browser_get_page":
            return "Catalog table: 1,420 items. Filter elements: input#search, checkbox#in-stock-only, table#catalog-table.", True
        if tool == "browser_fill":
            state["search_query"] = args.get("value", "")
            return f"Entered search query '{args.get('value')}'. Filtered table to 12 items.", True
        if tool == "browser_click":
            state["in_stock_filtered"] = True
            return "Applied 'In-Stock Only' filter. Filtered table to 3 in-stock items: [ASUS TUF RTX 4090: $1,599, MSI Gaming X RTX 4090: $1,689, ROG Strix RTX 4090: $1,899].", True
        if tool == "browser_extract":
            return "Lowest In-Stock Item: ASUS TUF Gaming GeForce RTX 4090 24GB | Price: $1,599.00 USD", True
        if tool == "write_file":
            state["quote_written"] = True
            return f"Saved quote record ($1,599.00) to {args.get('path')}.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-02",
        category="A. Browser Workflow Discovery",
        goal="Find the lowest priced in-stock RTX 4090 GPU on the internal hardware procurement catalog at https://hardware.corp/catalog and record the price to procurement/gpu_quote.txt.",
        expected_necessary_steps=["browser_navigate", "write_file"],
        verification_tools=[],
        mock_env_handler=h_awd2_02,
        is_browser_task=True,
    ))

    # AWD2-03: On-Call Escalation Directory Discovery -> Save Alerts Contact
    def h_awd2_03(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            return "Loaded Intranet Home (https://intranet.corp). Navigation links: ['IT Helpdesk', 'HR Portal', 'Infrastructure On-Call Directory' (href='/infra/oncall')].", True
        if tool == "browser_get_page" or tool == "browser_find":
            return "Matching Link: 'Infrastructure On-Call Directory' -> href='/infra/oncall'", True
        if tool == "browser_click":
            state["oncall_page"] = True
            return "Navigated to https://intranet.corp/infra/oncall. Table: Primary Lead: Alex Vance (+1-555-019-8834), Secondary: Dave K (+1-555-019-9941).", True
        if tool == "browser_extract":
            return "Emergency Escalation Lead Phone: +1-555-019-8834 (Alex Vance - Primary Infra SRE)", True
        if tool == "write_file":
            state["contact_saved"] = True
            return f"Saved contact information to {args.get('path')}.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-03",
        category="A. Browser Workflow Discovery",
        goal="Look up the emergency escalation phone number for the infra team on the company intranet at https://intranet.corp and save it to alerts/contact.txt.",
        expected_necessary_steps=["browser_navigate", "write_file"],
        verification_tools=[],
        mock_env_handler=h_awd2_03,
        is_browser_task=True,
    ))

    # AWD2-04: Support Ticket Form Submission
    def h_awd2_04(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            return "Loaded https://support.corp/new-ticket. Form fields: input#ticket-subject, textarea#ticket-desc, select#priority, button#submit-ticket.", True
        if tool == "browser_get_page":
            return "Form: input#ticket-subject, textarea#ticket-desc, button#submit-ticket.", True
        if tool == "browser_fill":
            state[f"filled_{args.get('field')}"] = args.get("value")
            return f"Filled {args.get('field')} successfully.", True
        if tool == "browser_click":
            state["ticket_submitted"] = True
            return "Ticket submitted successfully! Generated Ticket ID: TIK-88402 (Priority: P1-High, Assignee: Database Ops Team).", True
        if tool == "browser_extract":
            return "Ticket Confirmation: TIK-88402 | Status: Open | Queue: Database Operations", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-04",
        category="A. Browser Workflow Discovery",
        goal="Submit a high-priority support ticket for database lag on the internal ticketing portal at https://support.corp/new-ticket.",
        expected_necessary_steps=["browser_navigate", "browser_fill", "browser_click"],
        verification_tools=[],
        mock_env_handler=h_awd2_04,
        is_browser_task=True,
    ))

    # AWD2-05: Compliance Checklist Download & Local Verification
    def h_awd2_05(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            return "Loaded https://compliance.corp/docs. Available documents: 'Monthly Compliance Checklist' (Download URL: https://compliance.corp/downloads/checklist.pdf).", True
        if tool == "browser_find" or tool == "browser_get_page":
            return "Found item: 'Monthly Compliance Checklist' -> Download URL: https://compliance.corp/downloads/checklist.pdf", True
        if tool == "fetch_url":
            state["downloaded_content"] = "%PDF-1.4 Compliance Checklist v3.2..."
            return "Fetched 240KB binary document stream.", True
        if tool == "write_file":
            state["file_written"] = True
            return f"Wrote compliance document to {args.get('path')}.", True
        if tool == "file_exists":
            return "File exists: compliance/checklist.pdf (Size: 240,112 bytes)", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-05",
        category="A. Browser Workflow Discovery",
        goal="Download the monthly compliance checklist from the portal at https://compliance.corp/docs and confirm it was saved locally to compliance/checklist.pdf.",
        expected_necessary_steps=["browser_navigate", "write_file", "file_exists"],
        verification_tools=["file_exists"],
        mock_env_handler=h_awd2_05,
        is_browser_task=True,
    ))

    # =========================================================================
    # B. MISSING-PREREQUISITE DISCOVERY (2 Tasks)
    # =========================================================================

    # AWD2-06: Presentation Tool Discovery & Launch
    def h_awd2_06(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "search_installed_apps":
            return json.dumps({"matches": ["Microsoft PowerPoint (C:\\Program Files\\Office\\POWERPNT.EXE)", "LibreOffice Impress"]}), True
        if tool == "launch_app":
            state["app_launched"] = args.get("app_name")
            return f"Launched application: {args.get('app_name')} (PID: 7720).", True
        if tool == "get_open_windows":
            return json.dumps([{"title": "PowerPoint - Presentation1", "state": "active", "pid": 7720}]), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-06",
        category="B. Missing-Prerequisite Discovery",
        goal="Prepare the quarterly slide deck presentation using the presentation tool.",
        expected_necessary_steps=["launch_app"],
        verification_tools=["get_open_windows"],
        mock_env_handler=h_awd2_06,
    ))

    # AWD2-07: Legacy CSV Cleansing & File Metadata Verification
    def h_awd2_07(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            return "File exists: exports/contacts.csv (Size: 84KB)", True
        if tool == "read_file":
            return "id,name,email\n1,Alice,alice@corp.com\n2,Bob,bob@corp.com\n3,,null", True
        if tool == "write_file":
            state["clean_written"] = True
            return f"Wrote cleaned data to {args.get('path')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": "exports/contacts_clean.csv", "size": 61200, "valid": True}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-07",
        category="B. Missing-Prerequisite Discovery",
        goal="Extract the customer contact details from the legacy export file 'exports/contacts.csv' and verify the processed file 'exports/contacts_clean.csv' is ready.",
        expected_necessary_steps=["read_file", "write_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_07,
    ))

    # =========================================================================
    # C. NOVEL MULTI-TOOL COMPOSITION (3 Tasks)
    # =========================================================================

    # AWD2-08: Workspace Silence & Debugger Launch
    def h_awd2_08(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_process_list" or tool == "get_open_windows":
            return json.dumps([{"name": "Spotify.exe", "status": "playing"}, {"name": "chrome.exe", "status": "idle"}]), True
        if tool == "pause_media":
            state["media_paused"] = True
            return "Media playback paused across all audio sinks.", True
        if tool == "launch_app":
            state["editor_launched"] = args.get("app_name")
            return f"Launched {args.get('app_name')} (PID: 8812).", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-08",
        category="C. Novel Multi-Tool Composition",
        goal="Inspect the system state, silence any background noise, and open the code editor for debugging.",
        expected_necessary_steps=["pause_media", "launch_app"],
        verification_tools=["get_open_windows"],
        mock_env_handler=h_awd2_08,
    ))

    # AWD2-09: Telemetry Scraping & Structured File Archival
    def h_awd2_09(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            return "Loaded https://router.local. Status: Online. Bandwidth: 820 Mbps Down / 94 Mbps Up. Connected clients: 18.", True
        if tool == "browser_extract" or tool == "browser_get_page":
            return json.dumps({"wan_ip": "198.51.100.2", "latency_ms": 11.4, "packet_loss": "0.0%"}), True
        if tool == "write_file":
            state["telemetry_saved"] = True
            return f"Saved telemetry snapshot to {args.get('path')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": args.get("path"), "size": 340, "sha256": "4a8e..."}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-09",
        category="C. Novel Multi-Tool Composition",
        goal="Collect current network telemetry from the router dashboard at https://router.local and archive it to logs/network_status.json.",
        expected_necessary_steps=["browser_navigate", "write_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_09,
    ))

    # AWD2-10: Critical Dump File Relocation & Backup Verification
    def h_awd2_10(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            return "Found: temp/critical_dump.sql (Size: 1.4GB)", True
        if tool == "copy_file" or tool == "move_file":
            state["dump_backed_up"] = True
            return f"Transferred file {args.get('source')} -> {args.get('destination')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": "backups/critical_dump.sql", "size": 1503238550, "verified": True}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-10",
        category="C. Novel Multi-Tool Composition",
        goal="Find all large dump files in 'temp/' and back up the critical dump to 'backups/critical_dump.sql'.",
        expected_necessary_steps=["copy_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_10,
    ))

    # =========================================================================
    # D. STATE-DEPENDENT DECISION MAKING (3 Tasks)
    # =========================================================================

    # AWD2-11: Rogue Crypto Miner Termination
    def h_awd2_11(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_system_stats":
            if state.get("killed_miner"):
                return json.dumps({"cpu_load_pct": 12.4, "ram_used_pct": 28.0, "status": "HEALTHY"}), True
            return json.dumps({"cpu_load_pct": 98.6, "ram_used_pct": 92.4, "status": "DEGRADED"}), True
        if tool == "get_process_list":
            return json.dumps([
                {"name": "system_core", "cpu_pct": 2.1},
                {"name": "xmr_miner.exe", "cpu_pct": 94.8, "pid": 9940},
                {"name": "explorer.exe", "cpu_pct": 1.2},
            ]), True
        if tool == "kill_process":
            state["killed_miner"] = True
            return f"Terminated process {args.get('process_name')} (PID: 9940).", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-11",
        category="D. State-Dependent Decision Making",
        goal="Check overall system health and eliminate any runaway rogue process consuming excessive resources.",
        expected_necessary_steps=["get_process_list", "kill_process"],
        verification_tools=["get_system_stats"],
        mock_env_handler=h_awd2_11,
    ))

    # AWD2-12: Conditional Server Config File Initialization
    def h_awd2_12(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            return "File NOT found: config/server.env", False
        if tool == "write_file":
            state["created_env"] = True
            return "Wrote default config 'PORT=8080\\nNODE_ENV=production' to config/server.env.", True
        if tool == "get_file_info":
            return json.dumps({"path": "config/server.env", "size": 32, "status": "created"}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-12",
        category="D. State-Dependent Decision Making",
        goal="Check if the server config file 'config/server.env' exists; if not, create it with default PORT=8080.",
        expected_necessary_steps=["file_exists", "write_file"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_12,
    ))

    # AWD2-13: Meeting Mode Volume Leveling
    def h_awd2_13(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_process_list" or tool == "get_open_windows":
            return json.dumps([{"name": "Spotify.exe", "state": "playing", "volume": 85}]), True
        if tool == "set_volume":
            state["volume"] = args.get("level")
            return f"System master volume set to {args.get('level')}%.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-13",
        category="D. State-Dependent Decision Making",
        goal="Check if the media player is currently playing music, and if so, lower the volume to 20% for meeting mode.",
        expected_necessary_steps=["set_volume"],
        verification_tools=[],
        mock_env_handler=h_awd2_13,
    ))

    # =========================================================================
    # E. ERROR RECOVERY (2 Tasks)
    # =========================================================================

    # AWD2-14: Primary Feed 503 Outage -> Public Mirror Fallback Search & Ingest
    def h_awd2_14(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "fetch_url":
            if "security.corp" in args.get("url", ""):
                return "HTTP 503 Service Unavailable: Primary advisory endpoint down for maintenance.", False
            state["fetched_mirror"] = True
            return '{"advisory_id": "SEC-2026-04", "severity": "HIGH", "cve": "CVE-2026-8812"}', True
        if tool == "web_search":
            return "Search results: Public security advisory mirror available at https://mirror.secops.internal/advisory.json", True
        if tool == "write_file":
            state["saved_advisory"] = True
            return f"Saved advisory to {args.get('path')}.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-14",
        category="E. Error Recovery",
        goal="Fetch the latest security advisory from primary feed https://security.corp/advisory.json; if unavailable, search and retrieve from public mirror.",
        expected_necessary_steps=["fetch_url", "web_search", "write_file"],
        verification_tools=[],
        mock_env_handler=h_awd2_14,
        requires_recovery=True,
    ))

    # AWD2-15: Multi-Candidate File Discovery & Copy
    def h_awd2_15(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            path = args.get("path", "")
            if "downloads/spec.pdf" in path:
                return "File NOT found: downloads/spec.pdf", False
            if "drafts/spec.pdf" in path:
                return "File found: drafts/spec.pdf (Size: 2.1MB)", True
            return "File found: drafts/spec.pdf", True
        if tool == "copy_file":
            state["spec_copied"] = True
            return f"Copied {args.get('source')} to {args.get('destination')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": "specs/final_spec.pdf", "size": 2201840, "status": "VERIFIED"}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-15",
        category="E. Error Recovery",
        goal="Locate the misplaced design specification 'spec.pdf' across candidate folders and copy it to 'specs/final_spec.pdf'.",
        expected_necessary_steps=["copy_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_15,
        requires_recovery=True,
    ))

    # =========================================================================
    # F. FILE + APPLICATION WORKFLOWS (2 Tasks)
    # =========================================================================

    # AWD2-16: Editor Launch & Target Window Focus
    def h_awd2_16(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists" or tool == "read_file":
            return "Notes content: # Project Standup Notes\n- Review AWD benchmark v2...", True
        if tool == "launch_app":
            state["app_started"] = True
            return f"Launched application: {args.get('app_name')} (PID: 9012).", True
        if tool == "manage_window":
            state["window_focused"] = True
            return f"Window action '{args.get('action')}' applied to '{args.get('title')}'. Window active in foreground.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-16",
        category="F. File + Application Workflows",
        goal="Open the project notes 'notes/meeting.md' in the text editor and bring its window to focus.",
        expected_necessary_steps=["launch_app", "manage_window"],
        verification_tools=[],
        mock_env_handler=h_awd2_16,
    ))

    # AWD2-17: Log Rotation & Fresh Header Initialization
    def h_awd2_17(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            return "Found active log file: logs/app.log (Size: 84MB)", True
        if tool == "rename_file" or tool == "move_file":
            state["rotated"] = True
            return f"Renamed {args.get('source')} -> {args.get('destination')}.", True
        if tool == "write_file":
            state["fresh_log_initialized"] = True
            return f"Initialized new log header in {args.get('path')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": "logs/app.log", "size": 128, "created": "now"}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-17",
        category="F. File + Application Workflows",
        goal="Archive the current log 'logs/app.log' to 'logs/app.log.old' and write a fresh initialization header to 'logs/app.log'.",
        expected_necessary_steps=["rename_file", "write_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_17,
    ))

    # =========================================================================
    # G. BROWSER + FILE WORKFLOWS (2 Tasks)
    # =========================================================================

    # AWD2-18: Remote API Changelog Extraction to Local Documentation
    def h_awd2_18(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "browser_navigate":
            return "Loaded https://api.corp/changelog. Page Title: API Release Notes v3.4. Changelog: Added streaming JSON, deprecated legacy tokens.", True
        if tool == "browser_extract" or tool == "browser_get_page":
            return "### API v3.4 Changes\n- Added streaming JSON endpoint\n- Deprecated legacy tokens\n- Rate limits increased to 500 req/min.", True
        if tool == "write_file":
            state["docs_updated"] = True
            return f"Updated documentation file: {args.get('path')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": "docs/API_CHANGES.md", "size": 1240, "updated": True}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-18",
        category="G. Browser + File Workflows",
        goal="Extract the API changelog from https://api.corp/changelog and update 'docs/API_CHANGES.md'.",
        expected_necessary_steps=["browser_navigate", "write_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_18,
        is_browser_task=True,
    ))

    # AWD2-19: Local URL Manifest Read -> Navigate & Extract
    def h_awd2_19(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "read_file":
            return "https://internal-portal.corp/status\nhttps://backup.corp/status", True
        if tool == "browser_navigate":
            state["navigated_target"] = args.get("url")
            return f"Loaded {args.get('url')}. Page title: 'Production Operations Dashboard'. Status: All Systems Nominal.", True
        if tool == "browser_get_page" or tool == "browser_extract":
            return "Title: Production Operations Dashboard | Status: All Systems Nominal", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-19",
        category="G. Browser + File Workflows",
        goal="Read the URL list from 'inputs/sites.txt', navigate to the first URL, and extract the page title.",
        expected_necessary_steps=["read_file", "browser_navigate"],
        verification_tools=[],
        mock_env_handler=h_awd2_19,
        is_browser_task=True,
    ))

    # =========================================================================
    # H. SYSTEM + MEDIA WORKFLOWS (2 Tasks)
    # =========================================================================

    # AWD2-20: Presentation Workspace Preparation
    def h_awd2_20(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_open_windows":
            return json.dumps([
                {"title": "Spotify Free", "state": "playing"},
                {"title": "Visual Studio Code - main.py", "state": "active"},
                {"title": "Google Chrome - Internal Docs", "state": "inactive"},
            ]), True
        if tool == "pause_media":
            state["music_paused"] = True
            return "Spotify audio paused.", True
        if tool == "manage_window":
            state[f"window_{args.get('action')}_{args.get('title')}"] = True
            return f"Applied {args.get('action')} to window '{args.get('title')}'.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-20",
        category="H. System + Media Workflows",
        goal="Prepare the workspace for the presentation.",
        expected_necessary_steps=["pause_media", "manage_window"],
        verification_tools=["get_open_windows"],
        mock_env_handler=h_awd2_20,
    ))

    # AWD2-21: Focus Background Music Initiation
    def h_awd2_21(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_process_list":
            return json.dumps([{"name": "chrome.exe"}, {"name": "code.exe"}]), True
        if tool == "launch_app":
            state["app_started"] = True
            return "Launched Spotify (PID: 9402).", True
        if tool == "play_media":
            state["playing_lofi"] = True
            return f"Playing track: '{args.get('query_or_title', 'lofi focus beats')}' on Spotify.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-21",
        category="H. System + Media Workflows",
        goal="Check if Spotify is running; if not, start background music for focus time.",
        expected_necessary_steps=["launch_app", "play_media"],
        verification_tools=[],
        mock_env_handler=h_awd2_21,
    ))

    # =========================================================================
    # I. 3–5 STEP HIDDEN WORKFLOWS (2 Tasks)
    # =========================================================================

    # AWD2-22: Safe Verified Database Copy
    def h_awd2_22(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_file_info":
            path = args.get("path", "")
            if "backups" in path:
                return json.dumps({"path": path, "size": 84200110, "sha256": "c89a...", "status": "VERIFIED"}), True
            return json.dumps({"path": path, "size": 84200110, "sha256": "c89a...", "status": "SRC_OK"}), True
        if tool == "copy_file":
            state["copied"] = True
            return f"Copied {args.get('source')} to {args.get('destination')}.", True
        if tool == "file_exists":
            return "File exists: backups/prod.db.bak (84MB)", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-22",
        category="I. 3-5 Step Hidden Workflows",
        goal="Create a safe verified backup of 'data/prod.db' in 'backups/prod.db.bak'.",
        expected_necessary_steps=["copy_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_22,
    ))

    # AWD2-23: Web Worker Priority Rebalancing
    def h_awd2_23(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "get_process_list":
            return json.dumps([
                {"name": "web_worker", "pid": 4410, "cpu_pct": 14.0, "priority": "low"},
                {"name": "background_indexer", "pid": 4412, "cpu_pct": 82.0, "priority": "high"},
            ]), True
        if tool == "get_process_info":
            return json.dumps({"process": args.get("process_name"), "priority": state.get("priority", "low"), "threads": 16}), True
        if tool == "set_process_priority":
            state["priority"] = args.get("priority")
            return f"Updated {args.get('process_name')} priority class to '{args.get('priority')}'.", True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-23",
        category="I. 3-5 Step Hidden Workflows",
        goal="Investigate why the web worker is sluggish and restore optimal priority.",
        expected_necessary_steps=["get_process_list", "set_process_priority"],
        verification_tools=["get_process_info"],
        mock_env_handler=h_awd2_23,
    ))

    # =========================================================================
    # J. TASKS REQUIRING FINAL STATE VERIFICATION (2 Tasks)
    # =========================================================================

    # AWD2-24: Safe Environment Config Deployment
    def h_awd2_24(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            return "File exists: config/staging.env", True
        if tool == "read_file":
            return "DB_HOST=pg-prod.internal\nCACHE_ENABLED=true\nAPI_KEY=enc_994a", True
        if tool == "copy_file" or tool == "write_file":
            state["deployed"] = True
            return "Transferred staging configuration to config/production.env.", True
        if tool == "get_file_info":
            return json.dumps({"path": "config/production.env", "size": 68, "checksum_match": True}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-24",
        category="J. State Verification",
        goal="Safely deploy the updated environment configuration from 'config/staging.env' to 'config/production.env' and verify file integrity.",
        expected_necessary_steps=["copy_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_24,
    ))

    # AWD2-25: Crash Log Archival & Verification
    def h_awd2_25(tool: str, args: Dict[str, Any], state: Dict[str, Any]):
        if tool == "file_exists":
            path = args.get("path", "")
            if "archives" in path:
                return "Archive exists: archives/crash_reports.bak", True
            return "Found 14 orphaned crash logs in crash_reports/", True
        if tool == "move_file" or tool == "copy_file":
            state["archived"] = True
            return f"Moved {args.get('source')} -> {args.get('destination')}.", True
        if tool == "get_file_info":
            return json.dumps({"path": "archives/crash_reports.bak", "total_files": 14, "verified": True}), True
        return "Action OK", True

    suite.append(DiscoveryTestCaseV2(
        task_id="AWD2-25",
        category="J. State Verification",
        goal="Clean up orphaned crash logs from 'crash_reports/' by archiving them to 'archives/crash_reports.bak' and verify the target exists.",
        expected_necessary_steps=["move_file", "get_file_info"],
        verification_tools=["get_file_info"],
        mock_env_handler=h_awd2_25,
    ))

    return suite

# -----------------------------------------------------------------------------
# Action Classification & Evaluation Logic
# -----------------------------------------------------------------------------
@dataclass
class ActionRecordV2:
    turn: int
    tool_name: str
    arguments: Dict[str, Any]
    classification: str
    output_preview: str

@dataclass
class TaskEvaluationResultV2:
    task_id: str
    category: str
    goal: str
    passed: bool
    autonomous_discovery_success: bool
    discovered_steps: List[str]
    missing_required_steps: List[str]
    unnecessary_actions: List[str]
    incorrect_actions: List[str]
    tool_selection_accuracy: float
    argument_accuracy: float
    recovery_success: bool
    final_verification_success: bool
    total_turns: int
    total_tool_calls: int
    latency_ms: float
    trajectory: List[ActionRecordV2]
    final_model_response: str

def evaluate_model_on_task_v2(
    model_cfg: Dict[str, Any],
    test_case: DiscoveryTestCaseV2,
    max_turns: int = 6,
) -> TaskEvaluationResultV2:
    api_key = model_cfg["api_key"]
    model_id = model_cfg["model_id"]
    endpoint_url = model_cfg.get("endpoint_url", API_URL)

    system_prompt = (
        "You are an autonomous AI operating system agent. "
        "The user will state a high-level goal. "
        "You have access to a set of system, browser, file, and media tools. "
        "Determine the necessary steps and execute them via tool calls to fulfill the goal completely. "
        "If a tool gives you the necessary data to proceed to the next step, proceed directly. "
        "Do not ask the user for confirmation when you can complete the workflow autonomously."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": test_case.goal},
    ]

    expected_b_steps = list(test_case.expected_necessary_steps)
    expected_b_set = set(expected_b_steps)
    valid_tool_names = {t["function"]["name"] for t in COMMON_TOOL_REGISTRY}

    called_tools_sequence: List[str] = []
    trajectory: List[ActionRecordV2] = []
    state: Dict[str, Any] = dict(test_case.initial_env_state)
    start_time = time.time()
    final_response_text = ""
    had_error = False
    recovered_from_error = False

    print(f"\n--- Task [{test_case.task_id}] {test_case.category} ---")
    print(f"Goal: \"{test_case.goal}\"")

    for turn in range(1, max_turns + 1):
        payload = {
            "model": model_id,
            "messages": messages,
            "tools": COMMON_TOOL_REGISTRY,
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_tokens": 512,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(
            endpoint_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"  [Turn {turn}] HTTP Error {e.code}: {err_body[:200]}")
            time.sleep(1)
            continue
        except Exception as e:
            print(f"  [Turn {turn}] Request exception: {e}")
            time.sleep(1)
            continue

        choice = res_data.get("choices", [{}])[0]
        message = choice.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls", [])
        content = message.get("content", "")

        if not tool_calls:
            final_response_text = content or ""
            preview = (final_response_text[:90] + "...") if len(final_response_text) > 90 else final_response_text
            print(f"  [Turn {turn}] Model completed: {preview.strip()}")
            break

        for tc in tool_calls:
            fn = tc.get("function", {})
            t_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")

            if t_name.startswith("functions."):
                t_name = t_name[len("functions."):]

            try:
                t_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                t_args = {}

            if t_name not in valid_tool_names:
                classification = "D"
            elif t_name in expected_b_set or t_name in ["browser_get_page", "browser_find", "browser_extract", "get_open_windows", "get_file_info", "get_process_list"]:
                classification = "B"
            else:
                classification = "C"

            if t_name in valid_tool_names:
                t_output, t_success = test_case.mock_env_handler(t_name, t_args, state)
            else:
                t_output, t_success = f"Error: Tool '{t_name}' is not recognized in registry.", False

            if not t_success:
                had_error = True
            elif had_error and t_success:
                recovered_from_error = True

            called_tools_sequence.append(t_name)
            output_prev = (t_output[:80] + "...") if len(t_output) > 80 else t_output
            print(f"  -> Turn {turn}: Called [{t_name}] (Class: {classification}) -> {output_prev}")

            trajectory.append(
                ActionRecordV2(
                    turn=turn,
                    tool_name=t_name,
                    arguments=t_args,
                    classification=classification,
                    output_preview=output_prev,
                )
            )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{turn}"),
                "name": tc.get("function", {}).get("name", t_name),
                "content": t_output,
            })

    total_latency = (time.time() - start_time) * 1000.0

    unique_called = set(called_tools_sequence)
    discovered_steps = [t for t in called_tools_sequence if t in expected_b_set]
    missing_steps = [t for t in expected_b_steps if t not in unique_called]
    unnecessary_actions = [t for t in called_tools_sequence if t not in expected_b_set and t in valid_tool_names and t not in ["browser_get_page", "browser_find", "get_open_windows", "get_file_info", "browser_extract", "browser_fill", "browser_click"]]
    incorrect_actions = [t for t in called_tools_sequence if t not in valid_tool_names]

    discovery_success = expected_b_set.issubset(unique_called) and len(incorrect_actions) == 0
    verification_success = all(vt in unique_called for vt in test_case.verification_tools) if test_case.verification_tools else True

    goal_passed = discovery_success and verification_success
    if test_case.requires_recovery and not recovered_from_error:
        goal_passed = False

    tool_selection_accuracy = 100.0 if len(incorrect_actions) == 0 and len(unnecessary_actions) == 0 else 85.0
    argument_accuracy = 100.0 if len(incorrect_actions) == 0 else 50.0

    result_status = "PASS" if goal_passed else "FAIL"
    print(f"Task Result: [{result_status}] | Discovered Core Steps (Class B): {len(set(discovered_steps))}/{len(expected_b_set)} | Latency: {total_latency:.1f}ms")

    return TaskEvaluationResultV2(
        task_id=test_case.task_id,
        category=test_case.category,
        goal=test_case.goal,
        passed=goal_passed,
        autonomous_discovery_success=discovery_success,
        discovered_steps=discovered_steps,
        missing_required_steps=missing_steps,
        unnecessary_actions=unnecessary_actions,
        incorrect_actions=incorrect_actions,
        tool_selection_accuracy=tool_selection_accuracy,
        argument_accuracy=argument_accuracy,
        recovery_success=recovered_from_error or not test_case.requires_recovery,
        final_verification_success=verification_success,
        total_turns=len(trajectory) + 1,
        total_tool_calls=len(called_tools_sequence),
        latency_ms=total_latency,
        trajectory=trajectory,
        final_model_response=final_response_text,
    )

# -----------------------------------------------------------------------------
# Main Runner & Aggregate Comparison
# -----------------------------------------------------------------------------
def run_v2_benchmark():
    print("=" * 90)
    print(" MAKIMA OS — AUTONOMOUS TOOL-USE & WORKFLOW DISCOVERY BENCHMARK V2 (DEEPSEEK V4 FLASH)")
    print("=" * 90)
    suite = build_v2_test_suite()
    print(f"Loaded {len(suite)} Discovery Test Cases across 10 Categories.")

    full_results: Dict[str, Any] = {
        "benchmark_version": "v2.0-deepseek-v4-flash",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_test_cases": len(suite),
        "models": {},
    }

    scorecard: Dict[str, Dict[str, Any]] = {}

    for model_cfg in MODELS_UNDER_TEST:
        m_name = model_cfg["name"]
        print("\n" + "=" * 90)
        print(f" EVALUATING MODEL: {m_name} ({model_cfg['model_id']})")
        print("=" * 90)

        task_results: List[TaskEvaluationResultV2] = []
        for test_case in suite:
            res = evaluate_model_on_task_v2(model_cfg, test_case)
            task_results.append(res)
            time.sleep(0.3)

        total = len(task_results)
        passed_count = sum(1 for r in task_results if r.passed)
        discovery_count = sum(1 for r in task_results if r.autonomous_discovery_success)
        browser_tasks = [r for r in task_results if r.category.startswith("A. Browser")]
        browser_passed = sum(1 for r in browser_tasks if r.passed)
        recovery_tasks = [r for r in task_results if r.category.startswith("E. Error Recovery")]
        recovery_passed = sum(1 for r in recovery_tasks if r.recovery_success)

        avg_latency = sum(r.latency_ms for r in task_results) / total
        avg_turns = sum(r.total_turns for r in task_results) / total
        total_class_b = sum(len(r.discovered_steps) for r in task_results)
        total_class_c = sum(len(r.unnecessary_actions) for r in task_results)
        total_class_d = sum(len(r.incorrect_actions) for r in task_results)
        avg_tool_accuracy = sum(r.tool_selection_accuracy for r in task_results) / total
        avg_arg_accuracy = sum(r.argument_accuracy for r in task_results) / total

        scorecard[m_name] = {
            "autonomous_discovery_rate": f"{(discovery_count / total) * 100:.1f}% ({discovery_count}/{total})",
            "goal_completion_rate": f"{(passed_count / total) * 100:.1f}% ({passed_count}/{total})",
            "browser_discovery_score": f"{(browser_passed / len(browser_tasks)) * 100:.1f}% ({browser_passed}/{len(browser_tasks)})",
            "recovery_success_rate": f"{(recovery_passed / len(recovery_tasks)) * 100:.1f}% ({recovery_passed}/{len(recovery_tasks)})",
            "tool_selection_accuracy": f"{avg_tool_accuracy:.1f}%",
            "argument_accuracy": f"{avg_arg_accuracy:.1f}%",
            "discovered_necessary_steps_b": total_class_b,
            "unnecessary_actions_c": total_class_c,
            "incorrect_actions_d": total_class_d,
            "average_latency_ms": f"{avg_latency:.1f} ms",
            "average_turns": f"{avg_turns:.1f} turns",
        }

        full_results["models"][m_name] = {
            "model_id": model_cfg["model_id"],
            "metrics": scorecard[m_name],
            "tasks": [asdict(r) for r in task_results],
        }

    # Print Final Scorecard
    print("\n" + "=" * 90)
    print("        AUTONOMOUS TOOL DISCOVERY V2 — FINAL SCORECARD")
    print("=" * 90)
    header = f"{'Metric':<42} | {'DeepSeek V4 Flash':<25}"
    print(header)
    print("-" * 90)

    keys = [
        ("Autonomous Discovery Rate", "autonomous_discovery_rate"),
        ("Goal Completion Rate", "goal_completion_rate"),
        ("Browser Discovery Score", "browser_discovery_score"),
        ("Error Recovery Success Rate", "recovery_success_rate"),
        ("Tool Selection Accuracy", "tool_selection_accuracy"),
        ("Argument Accuracy", "argument_accuracy"),
        ("Discovered Necessary Steps (Class B)", "discovered_necessary_steps_b"),
        ("Unnecessary Actions Invented (Class C)", "unnecessary_actions_c"),
        ("Incorrect Actions (Class D)", "incorrect_actions_d"),
        ("Average Latency per Workflow", "average_latency_ms"),
        ("Average Trajectory Turns", "average_turns"),
    ]

    for label, k in keys:
        ds_val = str(scorecard.get("DeepSeek V4 Flash", {}).get(k, "N/A"))
        print(f"{label:<42} | {ds_val:<25}")

    print("=" * 90)

    out_json_path = os.path.join(os.path.dirname(__file__), "benchmark_autonomous_tool_discovery_v2_raw.json")
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2, ensure_ascii=False)
    print(f"Full 25-case raw trajectories saved to: {out_json_path}\n")

if __name__ == "__main__":
    run_v2_benchmark()
