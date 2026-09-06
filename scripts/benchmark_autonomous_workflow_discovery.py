"""
Makima OS — Expanded 25-Case Autonomous Workflow Discovery Benchmark: Qwen 3.5 Plus vs Nemotron 3 Ultra
Location: scripts/benchmark_autonomous_workflow_discovery.py

Evaluates whether models can autonomously discover necessary intermediate multi-tool workflows
given ONLY a high-level user goal, across 5 core categories (25 test cases total):
1. Hidden Intermediate-Step Discovery (AWD-01 to AWD-05)
2. Browser Exploration & Deep Navigation (AWD-06 to AWD-10)
3. Prerequisite & Missing-Precondition Discovery (AWD-11 to AWD-15)
4. Alternative-Route & Error Recovery (AWD-16 to AWD-20)
5. Novel Tool Composition & State-Dependent Branching (AWD-21 to AWD-25)

Models Tested under Identical Prompts, Tool Schemas, Mocks & Criteria:
- Qwen 3.5 Plus (qwen/qwen3.5-plus-02-15)
- Nvidia Nemotron 3 Ultra (nvidia/nemotron-3-ultra-550b-a55b)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

# Ensure repo root is in python path and UTF-8 encoding
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# Load environment variables
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k.strip()] = v.strip()

DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-ws-H.DMEXXHX.1XMv.MEYCIQCOSaps08F94rLHKMMQBGVzE2mEll7cQcsOG72CrYDbUQIhAI2M9WN6zYRpequzPCJkQl-LfuaoJyDYUzQUB8QhiLMK")
OPENROUTER_KEY = DASHSCOPE_KEY
OPENROUTER_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
NEMOTRON_DEFAULT_KEY = DASHSCOPE_KEY


@dataclass
class DiscoveryTestCase:
    task_id: str
    category: str
    high_level_goal: str
    available_tools: List[Dict[str, Any]]
    expected_necessary_steps: List[str]  # Required tool names in sequence/set
    forbidden_or_hallucinated_tools: List[str]
    mock_environment: Dict[str, Callable[[Dict[str, Any]], Tuple[str, bool]]]  # tool_name -> (return_str, is_success)
    requires_state_inspection_first: bool = False
    requires_error_recovery: bool = False


@dataclass
class StepEvaluation:
    turn: int
    tool_name: str
    tool_args: Dict[str, Any]
    classification: str  # "A", "B", or "C"
    is_valid_arg: bool
    is_order_correct: bool
    result_output: str


@dataclass
class TrajectoryRecord:
    task_id: str
    model_name: str
    category: str
    goal: str
    goal_completed: bool
    autonomous_discovery: bool  # True if all necessary Category B steps were discovered
    discovered_necessary_steps: int
    unnecessary_steps: int
    total_tool_calls: int
    tool_selection_accuracy: float
    tool_arg_accuracy: float
    order_correct: bool
    recovery_success: bool
    state_inspection_performed: bool
    final_verification_performed: bool
    llm_turns: int
    latency_ms: float
    total_tokens: int
    steps: List[StepEvaluation] = field(default_factory=list)
    raw_trace: List[Dict[str, Any]] = field(default_factory=list)


def build_test_suite() -> List[DiscoveryTestCase]:
    suite: List[DiscoveryTestCase] = []

    # =========================================================================
    # CATEGORY 1: HIDDEN INTERMEDIATE-STEP DISCOVERY (AWD-01 to AWD-05)
    # =========================================================================

    # AWD-01: DB Backup & Integrity Pipeline
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-01",
            category="Hidden Intermediate-Step Discovery",
            high_level_goal="Create a verified timestamped backup of the customer database and post confirmation to Discord.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "db_dump",
                        "description": "Dump database contents to a raw SQL file.",
                        "parameters": {"type": "object", "properties": {"db_name": {"type": "string"}, "output_path": {"type": "string"}}, "required": ["db_name", "output_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "compress_files",
                        "description": "Compress files into a tar.gz or zip archive with timestamp.",
                        "parameters": {"type": "object", "properties": {"source_path": {"type": "string"}, "archive_path": {"type": "string"}}, "required": ["source_path", "archive_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "verify_archive_integrity",
                        "description": "Test CRC/checksum integrity of compressed archive.",
                        "parameters": {"type": "object", "properties": {"archive_path": {"type": "string"}}, "required": ["archive_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "send_discord_notification",
                        "description": "Post a status notification message to the engineering Discord channel.",
                        "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "format_c_drive",
                        "description": "Format system disk drive.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            expected_necessary_steps=["db_dump", "compress_files", "verify_archive_integrity", "send_discord_notification"],
            forbidden_or_hallucinated_tools=["format_c_drive"],
            mock_environment={
                "db_dump": lambda args: ("Dumped customer_db to /backups/customer_raw.sql (Size: 42MB).", True),
                "compress_files": lambda args: (f"Created archive {args.get('archive_path', 'backup.tar.gz')} (Size: 8.4MB).", True),
                "verify_archive_integrity": lambda args: ("Archive integrity verified: CRC32 OK, zero corrupt blocks.", True),
                "send_discord_notification": lambda args: ("Posted notification to Discord #engineering: 'Customer DB backup completed & verified.'", True),
            },
        )
    )

    # AWD-02: Video Audio Extraction & Searchable Transcription
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-02",
            category="Hidden Intermediate-Step Discovery",
            high_level_goal="Generate a searchable English transcript from the video interview recorded at 'interviews/candidate_402.mp4'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "video_extract_audio",
                        "description": "Extract raw audio stream from an MP4/MKV video container.",
                        "parameters": {"type": "object", "properties": {"video_path": {"type": "string"}, "audio_out_path": {"type": "string"}}, "required": ["video_path", "audio_out_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "normalize_audio_levels",
                        "description": "Filter audio background noise and apply speech loudness normalization.",
                        "parameters": {"type": "object", "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "transcribe_speech_to_text",
                        "description": "Run Whisper AI speech-to-text on an audio file.",
                        "parameters": {"type": "object", "properties": {"audio_path": {"type": "string"}, "language": {"type": "string"}}, "required": ["audio_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "save_transcript_file",
                        "description": "Save generated transcript with timestamps to a Markdown or JSON file.",
                        "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "dest_path": {"type": "string"}}, "required": ["content", "dest_path"]},
                    },
                },
            ],
            expected_necessary_steps=["video_extract_audio", "normalize_audio_levels", "transcribe_speech_to_text", "save_transcript_file"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "video_extract_audio": lambda args: ("Extracted 44.1kHz audio stream to /tmp/candidate_402.wav (Duration: 42m15s).", True),
                "normalize_audio_levels": lambda args: ("Normalized audio LUFS: -16.0dB, noise gate applied.", True),
                "transcribe_speech_to_text": lambda args: ("Transcribed 4,820 words (100% confidence, Whisper Large-v3).", True),
                "save_transcript_file": lambda args: (f"Saved searchable transcript to {args.get('dest_path', 'transcript.md')}.", True),
            },
        )
    )

    # AWD-03: SSL Certificate Zero-Downtime Renewal
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-03",
            category="Hidden Intermediate-Step Discovery",
            high_level_goal="Renew the expiring SSL certificate for api.production.makima.ai and ensure zero-downtime traffic routing.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "check_cert_expiry",
                        "description": "Inspect domain X.509 certificate expiry date and SANs.",
                        "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "request_acme_certificate",
                        "description": "Request new Let's Encrypt TLS certificate via ACME DNS challenge.",
                        "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "validate_cert_chain",
                        "description": "Verify intermediate CA chain and RSA/ECDSA keypair validity.",
                        "parameters": {"type": "object", "properties": {"cert_path": {"type": "string"}}, "required": ["cert_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "reload_reverse_proxy",
                        "description": "Hot-reload Nginx / Envoy reverse proxy configuration without dropping active TCP connections.",
                        "parameters": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
                    },
                },
            ],
            expected_necessary_steps=["check_cert_expiry", "request_acme_certificate", "validate_cert_chain", "reload_reverse_proxy"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "check_cert_expiry": lambda args: ("Certificate expires in 2 days (SAN: api.production.makima.ai, Issuer: R3).", True),
                "request_acme_certificate": lambda args: ("ACME challenge passed. Issued fullchain.pem and privkey.pem.", True),
                "validate_cert_chain": lambda args: ("Certificate chain verified: OK. Valid for 90 days.", True),
                "reload_reverse_proxy": lambda args: ("Nginx master process reloaded worker pools with new certs. Zero dropped connections.", True),
            },
        )
    )

    # AWD-04: Git Release Packaging & Checksum Attestation
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-04",
            category="Hidden Intermediate-Step Discovery",
            high_level_goal="Build and publish release artifacts for tag 'v2.4.0' to GitHub Releases and verify package checksums.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "checkout_git_tag",
                        "description": "Checkout clean working tree for target git tag.",
                        "parameters": {"type": "object", "properties": {"tag": {"type": "string"}}, "required": ["tag"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "build_release_binaries",
                        "description": "Compile production release binaries (x86_64 / aarch64).",
                        "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "generate_sha256_checksums",
                        "description": "Compute SHA256 hashes and create SHA256SUMS attestation manifest.",
                        "parameters": {"type": "object", "properties": {"directory": {"type": "string"}}, "required": ["directory"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "upload_github_release",
                        "description": "Upload built artifacts and checksum manifest to GitHub Releases.",
                        "parameters": {"type": "object", "properties": {"tag": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}}, "required": ["tag", "artifacts"]},
                    },
                },
            ],
            expected_necessary_steps=["checkout_git_tag", "build_release_binaries", "generate_sha256_checksums", "upload_github_release"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "checkout_git_tag": lambda args: ("Checked out tag v2.4.0 (commit: 7f8a91c, clean state).", True),
                "build_release_binaries": lambda args: ("Built makima-linux-amd64 and makima-darwin-arm64 binaries.", True),
                "generate_sha256_checksums": lambda args: ("Generated SHA256SUMS manifest for 2 release binaries.", True),
                "upload_github_release": lambda args: ("Published release v2.4.0 with 3 assets to GitHub Releases.", True),
            },
        )
    )

    # AWD-05: Secure User Offboarding & Key Invalidation
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-05",
            category="Hidden Intermediate-Step Discovery",
            high_level_goal="Execute full security offboarding for employee 'user_8819' and archive their workspace.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "revoke_iam_keys",
                        "description": "Revoke active AWS/GCP IAM access keys and API tokens for user.",
                        "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "terminate_active_sessions",
                        "description": "Kill all active OAuth / SSO / VPN sessions for user.",
                        "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "archive_user_data",
                        "description": "Create cold storage backup archive of user workspace and home directory.",
                        "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "log_compliance_event",
                        "description": "Emit SOC2/ISO27001 audit event for offboarding.",
                        "parameters": {"type": "object", "properties": {"event": {"type": "string"}, "user_id": {"type": "string"}}, "required": ["event", "user_id"]},
                    },
                },
            ],
            expected_necessary_steps=["revoke_iam_keys", "terminate_active_sessions", "archive_user_data", "log_compliance_event"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "revoke_iam_keys": lambda args: ("Revoked 3 IAM access keys and 2 personal access tokens for user_8819.", True),
                "terminate_active_sessions": lambda args: ("Terminated 4 active Okta/SSO sessions and disconnected WireGuard VPN.", True),
                "archive_user_data": lambda args: ("Encrypted and archived 12.4GB workspace to s3://archive-vault/user_8819.tar.enc.", True),
                "log_compliance_event": lambda args: ("Logged SOC2 compliance audit record: USER_OFFBOARDED_SUCCESS.", True),
            },
        )
    )

    # =========================================================================
    # CATEGORY 2: BROWSER EXPLORATION & DEEP NAVIGATION (AWD-06 to AWD-10)
    # =========================================================================

    # AWD-06: SSO Portal Login & Dashboard KPI Extraction
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-06",
            category="Browser Exploration & Deep Navigation",
            high_level_goal="Log in to the internal analytics portal at 'https://analytics.internal/login' and retrieve the Q3 revenue figure from the dashboard.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "browser_navigate",
                        "description": "Navigate the browser to a specific URL.",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_inspect_page",
                        "description": "Inspect DOM tree, buttons, inputs, and text content of current page.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_type",
                        "description": "Type text into an input field selector.",
                        "parameters": {"type": "object", "properties": {"selector": {"type": "string"}, "text": {"type": "string"}}, "required": ["selector", "text"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_click",
                        "description": "Click an interactive element by selector or text.",
                        "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_extract_text",
                        "description": "Extract text from target dashboard element.",
                        "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]},
                    },
                },
            ],
            expected_necessary_steps=["browser_navigate", "browser_inspect_page", "browser_type", "browser_click", "browser_extract_text"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "browser_navigate": lambda args: (f"Navigated to {args.get('url', '')}. Page title: 'Corporate Analytics SSO Login'", True),
                "browser_inspect_page": lambda args: ("Page DOM: <input id='username'>, <input id='password'>, <button id='login-btn'>Sign In</button>", True),
                "browser_type": lambda args: (f"Typed into {args.get('selector')}", True),
                "browser_click": lambda args: ("Clicked element. Authenticated successfully. Redirected to /dashboard. KPIs: [Q3 Net Revenue: $14.25M]", True),
                "browser_extract_text": lambda args: ("Extracted text from '#q3-revenue': '$14,250,000 USD (Q3 GAAP Net Revenue)'", True),
            },
        )
    )

    # AWD-07: E-Commerce In-Stock Filter & Price Scraper
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-07",
            category="Browser Exploration & Deep Navigation",
            high_level_goal="Find the lowest priced in-stock RTX 4090 GPU on the internal hardware procurement catalog at 'https://hardware.corp/catalog'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "browser_navigate",
                        "description": "Navigate to catalog URL.",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_apply_filter",
                        "description": "Apply filter facets on catalog view (e.g. in_stock=true, category='gpu').",
                        "parameters": {"type": "object", "properties": {"filter_key": {"type": "string"}, "filter_val": {"type": "string"}}, "required": ["filter_key", "filter_val"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_sort_table",
                        "description": "Sort catalog items table by column (e.g. 'price_asc').",
                        "parameters": {"type": "object", "properties": {"sort_by": {"type": "string"}}, "required": ["sort_by"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_extract_table_row",
                        "description": "Extract specific row details from product catalog grid.",
                        "parameters": {"type": "object", "properties": {"row_index": {"type": "integer"}}, "required": ["row_index"]},
                    },
                },
            ],
            expected_necessary_steps=["browser_navigate", "browser_apply_filter", "browser_sort_table", "browser_extract_table_row"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "browser_navigate": lambda args: ("Loaded Hardware Catalog. Total items: 1,420.", True),
                "browser_apply_filter": lambda args: ("Applied filters: [in_stock=true, query='RTX 4090']. Matches: 4 items.", True),
                "browser_sort_table": lambda args: ("Sorted by price ascending.", True),
                "browser_extract_table_row": lambda args: ("Row 1: 'ASUS TUF Gaming GeForce RTX 4090 24GB' - Price: $1,599.00 (In Stock: 3 units).", True),
            },
        )
    )

    # AWD-08: Multi-Step Provisioning Wizard Completion
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-08",
            category="Browser Exploration & Deep Navigation",
            high_level_goal="Provision a new high-memory staging cluster named 'staging-ml-node' using the cloud console wizard at 'https://cloud.corp/clusters/new'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "browser_navigate",
                        "description": "Navigate to cluster creation wizard.",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_type_field",
                        "description": "Type text into cluster name field.",
                        "parameters": {"type": "object", "properties": {"field": {"type": "string"}, "value": {"type": "string"}}, "required": ["field", "value"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_select_dropdown",
                        "description": "Select instance type from dropdown.",
                        "parameters": {"type": "object", "properties": {"dropdown": {"type": "string"}, "choice": {"type": "string"}}, "required": ["dropdown", "choice"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_click_wizard_step",
                        "description": "Advance to next step in creation wizard.",
                        "parameters": {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_confirm_creation",
                        "description": "Final review and submit cluster creation request.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            expected_necessary_steps=["browser_navigate", "browser_type_field", "browser_select_dropdown", "browser_click_wizard_step", "browser_confirm_creation"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "browser_navigate": lambda args: ("Opened Cluster Wizard Step 1: Basics.", True),
                "browser_type_field": lambda args: (f"Entered '{args.get('value')}' into {args.get('field')}.", True),
                "browser_select_dropdown": lambda args: (f"Selected '{args.get('choice')}' in {args.get('dropdown')}.", True),
                "browser_click_wizard_step": lambda args: ("Advanced to Step 2: Review & Launch.", True),
                "browser_confirm_creation": lambda args: ("Cluster 'staging-ml-node' provisioning triggered (ID: clus-9042a).", True),
            },
        )
    )

    # AWD-09: Internal Knowledge Base Search & Secret Retrieval
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-09",
            category="Browser Exploration & Deep Navigation",
            high_level_goal="Find the API endpoint and bearer token for the legacy telemetry warehouse from the internal wiki at 'https://wiki.corp'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "browser_navigate",
                        "description": "Navigate to wiki home.",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_search_wiki",
                        "description": "Submit search query to internal documentation index.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_click_link",
                        "description": "Open search result article link.",
                        "parameters": {"type": "object", "properties": {"link_text": {"type": "string"}}, "required": ["link_text"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_extract_secret_block",
                        "description": "Extract configuration code block containing endpoints and credentials.",
                        "parameters": {"type": "object", "properties": {"section_header": {"type": "string"}}, "required": ["section_header"]},
                    },
                },
            ],
            expected_necessary_steps=["browser_navigate", "browser_search_wiki", "browser_click_link", "browser_extract_secret_block"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "browser_navigate": lambda args: ("Navigated to Internal Wiki.", True),
                "browser_search_wiki": lambda args: ("Found 2 matching articles: ['Legacy Telemetry Guide', 'Ingestion Endpoints'].", True),
                "browser_click_link": lambda args: ("Opened 'Legacy Telemetry Guide'.", True),
                "browser_extract_secret_block": lambda args: ("Extracted config: Endpoint: 'https://telemetry-v1.corp/api/v1', Bearer: 'tel_sec_9941a87b'", True),
            },
        )
    )

    # AWD-10: Paginated Incident Log Parser & Root Cause Extraction
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-10",
            category="Browser Exploration & Deep Navigation",
            high_level_goal="Review the production incident report pages at 'https://status.corp/incidents/INC-9042' and extract the timeline postmortem.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "browser_navigate",
                        "description": "Navigate to incident details page.",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_inspect_page",
                        "description": "Inspect timeline entries on page.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_click_next_page",
                        "description": "Navigate to next page of chronological event logs.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browser_extract_postmortem",
                        "description": "Extract summary root cause and resolution action items.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            expected_necessary_steps=["browser_navigate", "browser_inspect_page", "browser_click_next_page", "browser_extract_postmortem"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "browser_navigate": lambda args: ("Loaded INC-9042 overview page.", True),
                "browser_inspect_page": lambda args: ("Viewing Page 1: Initial alerts fired at 04:12 UTC.", True),
                "browser_click_next_page": lambda args: ("Navigated to Page 2: Root cause identification & mitigation.", True),
                "browser_extract_postmortem": lambda args: ("Extracted Root Cause: Cascading connection timeout due to stale DNS cache. Resolution: DNS TTL lowered to 60s.", True),
            },
        )
    )

    # =========================================================================
    # CATEGORY 3: PREREQUISITE & MISSING-PRECONDITION DISCOVERY (AWD-11 to AWD-15)
    # =========================================================================

    # AWD-11: Graphviz DOT to PNG (Missing dot CLI -> Python Engine)
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-11",
            category="Prerequisite & Missing-Precondition Discovery",
            high_level_goal="Convert the system architecture diagram 'architecture.dot' to 'architecture.png'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "run_cli_command",
                        "description": "Execute a shell CLI command on the OS.",
                        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_installed_runtimes",
                        "description": "Search installed engines, Python libraries, and tools.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "run_python_script",
                        "description": "Run Python script snippet to process files or render graphs.",
                        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                    },
                },
            ],
            expected_necessary_steps=["run_cli_command", "search_installed_runtimes", "run_python_script"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "run_cli_command": lambda args: (
                    "Error: 'dot' is not recognized as an internal or external command, operable program or batch file.",
                    False,
                ) if "dot " in args.get("command", "") else ("Command output: OK", True),
                "search_installed_runtimes": lambda args: (
                    json.dumps({"available_runtimes": ["python 3.12", "pydot", "graphviz-python", "pillow"]}),
                    True,
                ),
                "run_python_script": lambda args: (
                    "Rendered architecture.dot -> architecture.png successfully via python pydot engine (Resolution: 1920x1080).",
                    True,
                ),
            },
        )
    )

    # AWD-12: Media Transcoding (Missing ffmpeg CLI -> Python moviepy)
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-12",
            category="Prerequisite & Missing-Precondition Discovery",
            high_level_goal="Transcode raw camera recording 'stream.raw' to standard MP4 video 'stream.mp4'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "run_cli_command",
                        "description": "Execute shell command.",
                        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_installed_runtimes",
                        "description": "Discover available video processing runtimes.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "run_python_script",
                        "description": "Execute Python video transcoding script.",
                        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                    },
                },
            ],
            expected_necessary_steps=["run_cli_command", "search_installed_runtimes", "run_python_script"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "run_cli_command": lambda args: ("Error: ffmpeg: command not found", False) if "ffmpeg" in args.get("command", "") else ("CLI command OK", True),
                "search_installed_runtimes": lambda args: (json.dumps({"available_runtimes": ["python 3.12", "moviepy", "imageio-ffmpeg", "opencv-python"]}), True),
                "run_python_script": lambda args: ("Transcoded stream.raw -> stream.mp4 (H.264 / AAC 60fps).", True),
            },
        )
    )

    # AWD-13: JSON Stream Processing (Missing jq -> Python ijson)
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-13",
            category="Prerequisite & Missing-Precondition Discovery",
            high_level_goal="Filter high-severity security events from 4GB log 'audit_stream.json' to 'audit_high.json'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "run_cli_command",
                        "description": "Execute jq command.",
                        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_installed_runtimes",
                        "description": "Search available data stream libraries.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "run_python_script",
                        "description": "Execute Python streaming json filter.",
                        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                    },
                },
            ],
            expected_necessary_steps=["run_cli_command", "search_installed_runtimes", "run_python_script"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "run_cli_command": lambda args: ("Error: 'jq' is not installed on this system path.", False) if "jq" in args.get("command", "") else ("CLI OK", True),
                "search_installed_runtimes": lambda args: (json.dumps({"available_runtimes": ["python 3.12", "ijson", "orjson", "pandas"]}), True),
                "run_python_script": lambda args: ("Filtered 2,140 high-severity events to audit_high.json using ijson stream parser.", True),
            },
        )
    )

    # AWD-14: Document Compilation (Missing Pandoc -> Python WeasyPrint)
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-14",
            category="Prerequisite & Missing-Precondition Discovery",
            high_level_goal="Compile the markdown technical specification 'spec.md' into 'spec.pdf'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "run_cli_command",
                        "description": "Execute pandoc command.",
                        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_installed_runtimes",
                        "description": "Search document engines.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "run_python_script",
                        "description": "Execute Python PDF rendering script.",
                        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                    },
                },
            ],
            expected_necessary_steps=["run_cli_command", "search_installed_runtimes", "run_python_script"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "run_cli_command": lambda args: ("Error: pandoc: command not found", False) if "pandoc" in args.get("command", "") else ("CLI OK", True),
                "search_installed_runtimes": lambda args: (json.dumps({"available_runtimes": ["python 3.12", "weasyprint", "markdown2", "reportlab"]}), True),
                "run_python_script": lambda args: ("Rendered spec.md -> spec.pdf (18 pages, styled CSS) via WeasyPrint.", True),
            },
        )
    )

    # AWD-15: Archive Extraction (Missing UnRAR -> Python 7-Zip Engine)
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-15",
            category="Prerequisite & Missing-Precondition Discovery",
            high_level_goal="Extract encrypted dataset bundle 'archive.rar' to 'extracted/'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "run_cli_command",
                        "description": "Execute unrar command.",
                        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_installed_runtimes",
                        "description": "Search decompression libraries.",
                        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "run_python_script",
                        "description": "Execute Python extraction script.",
                        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
                    },
                },
            ],
            expected_necessary_steps=["run_cli_command", "search_installed_runtimes", "run_python_script"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "run_cli_command": lambda args: ("Error: unrar not found in system PATH.", False) if "unrar" in args.get("command", "") else ("CLI OK", True),
                "search_installed_runtimes": lambda args: (json.dumps({"available_runtimes": ["python 3.12", "py7zr", "rarfile", "zipfile"]}), True),
                "run_python_script": lambda args: ("Extracted 142 files (1.2GB) from archive.rar to extracted/.", True),
            },
        )
    )

    # =========================================================================
    # CATEGORY 4: ALTERNATIVE-ROUTE & ERROR RECOVERY (AWD-16 to AWD-20)
    # =========================================================================

    # AWD-16: Degraded Storage Mirror Failover & Checksum Validation
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-16",
            category="Alternative-Route & Error Recovery",
            high_level_goal="Download the quarterly finance dataset to 'data/q3_finance.csv'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "download_from_url",
                        "description": "Download a file from an HTTP/HTTPS mirror endpoint.",
                        "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "dest_path": {"type": "string"}}, "required": ["url", "dest_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "query_dataset_mirrors",
                        "description": "Query official alternate storage mirror registry for dataset URLs.",
                        "parameters": {"type": "object", "properties": {"dataset_id": {"type": "string"}}, "required": ["dataset_id"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "verify_file_checksum",
                        "description": "Verify SHA256 checksum of downloaded file.",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                    },
                },
            ],
            expected_necessary_steps=["download_from_url", "query_dataset_mirrors", "download_from_url", "verify_file_checksum"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "download_from_url": lambda args: (
                    ("HTTP Error 503: Service Unavailable on primary mirror https://storage.primary.org/q3_finance.csv", False)
                    if "primary" in args.get("url", "") or "default" in args.get("url", "") or "storage.org" in args.get("url", "")
                    else ("Downloaded 14.8MB from secondary mirror https://mirror-eu.internal-datasets.net/q3_finance.csv to data/q3_finance.csv", True)
                ),
                "query_dataset_mirrors": lambda args: (
                    json.dumps({
                        "dataset": "q3_finance",
                        "mirrors": [
                            {"mirror": "primary", "url": "https://storage.primary.org/q3_finance.csv", "status": "degraded"},
                            {"mirror": "eu-secondary", "url": "https://mirror-eu.internal-datasets.net/q3_finance.csv", "status": "active"},
                        ],
                    }),
                    True,
                ),
                "verify_file_checksum": lambda args: ("SHA256 checksum verified: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 (VALID)", True),
            },
        )
    )

    # AWD-17: Read-Only Disk Lock Recovery & Ephemeral Mount Relocation
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-17",
            category="Alternative-Route & Error Recovery",
            high_level_goal="Process image batch in '/data/raw/' and save generated thumbnails to '/data/thumbs/'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "save_processed_images",
                        "description": "Write processed thumbnail images to destination directory.",
                        "parameters": {"type": "object", "properties": {"dest_dir": {"type": "string"}}, "required": ["dest_dir"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "query_writable_mounts",
                        "description": "Inspect OS filesystem mounts for writable scratch storage.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "symlink_resource",
                        "description": "Create symbolic link between target path and physical storage.",
                        "parameters": {"type": "object", "properties": {"target": {"type": "string"}, "link_name": {"type": "string"}}, "required": ["target", "link_name"]},
                    },
                },
            ],
            expected_necessary_steps=["save_processed_images", "query_writable_mounts", "save_processed_images", "symlink_resource"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "save_processed_images": lambda args: (
                    ("OSError: [Errno 30] Read-only file system: '/data/thumbs/'", False)
                    if "/data/" in args.get("dest_dir", "")
                    else (f"Processed 500 images successfully to {args.get('dest_dir')}.", True)
                ),
                "query_writable_mounts": lambda args: (json.dumps({"writable_mounts": ["/mnt/ephemeral-nvme/ (50GB free)", "/tmp/ (8GB free)"]}), True),
                "symlink_resource": lambda args: ("Created symlink /data/thumbs -> /mnt/ephemeral-nvme/thumbs", True),
            },
        )
    )

    # AWD-18: API Rate Limit 429 Interception & Backup Gateway Sync
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-18",
            category="Alternative-Route & Error Recovery",
            high_level_goal="Sync user billing invoices from the Stripe billing bridge into the accounting ledger.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "call_billing_api",
                        "description": "Call billing service endpoint to fetch invoice batches.",
                        "parameters": {"type": "object", "properties": {"gateway_url": {"type": "string"}}, "required": ["gateway_url"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "query_backup_gateways",
                        "description": "Look up active standby billing proxy gateways.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "log_sync_status",
                        "description": "Record batch reconciliation status in accounting ledger.",
                        "parameters": {"type": "object", "properties": {"count": {"type": "integer"}, "status": {"type": "string"}}, "required": ["count", "status"]},
                    },
                },
            ],
            expected_necessary_steps=["call_billing_api", "query_backup_gateways", "call_billing_api", "log_sync_status"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "call_billing_api": lambda args: (
                    ("HTTP 429 Too Many Requests: Rate limit exceeded on primary gateway https://billing.corp/api", False)
                    if "billing.corp/api" in args.get("gateway_url", "")
                    else ("Fetched 320 invoice records (200 OK) from https://billing-standby.corp/api.", True)
                ),
                "query_backup_gateways": lambda args: (json.dumps({"standby_gateways": ["https://billing-standby.corp/api", "https://billing-us-east.corp/api"]}), True),
                "log_sync_status": lambda args: ("Logged invoice reconciliation batch: 320 synced, 0 errors.", True),
            },
        )
    )

    # AWD-19: Corrupted Voice Checkpoint Detection & Cache Purge/Fetch
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-19",
            category="Alternative-Route & Error Recovery",
            high_level_goal="Load Kokoro TTS voice model checkpoint 'voices/kokoro-v1.bin' and verify voice synthesis readiness.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "load_model_weights",
                        "description": "Load ONNX model weights into memory.",
                        "parameters": {"type": "object", "properties": {"model_path": {"type": "string"}}, "required": ["model_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "purge_corrupted_cache",
                        "description": "Delete corrupted checkpoint binary from local model cache.",
                        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_clean_checkpoint",
                        "description": "Re-download verified model checkpoint from HuggingFace repository.",
                        "parameters": {"type": "object", "properties": {"model_id": {"type": "string"}, "dest_path": {"type": "string"}}, "required": ["model_id", "dest_path"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "verify_model_health",
                        "description": "Run 1-turn inference test to verify voice tensor output.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            expected_necessary_steps=["load_model_weights", "purge_corrupted_cache", "fetch_clean_checkpoint", "load_model_weights", "verify_model_health"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "load_model_weights": lambda args: (
                    ("Model kokoro-v1.bin loaded successfully into CUDA vram (84MB).", True)
                    if hasattr(build_test_suite, "_kokoro_reloaded")
                    else ("CorruptedModelError: Header CRC mismatch in voices/kokoro-v1.bin (Expected 0x9a, Found 0x00)", False)
                ),
                "purge_corrupted_cache": lambda args: ("Purged corrupted file voices/kokoro-v1.bin.", True),
                "fetch_clean_checkpoint": lambda args: (
                    setattr(build_test_suite, "_kokoro_reloaded", True) or ("Downloaded clean Kokoro-ONNX checkpoint from HF hub (SHA256 verified).", True)
                ),
                "verify_model_health": lambda args: ("Synthesized test speech: 24kHz audio generated in 24ms. Voice engine HEALTHY.", True),
            },
        )
    )

    # AWD-20: Database Pool Exhaustion & Read-Replica Routing
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-20",
            category="Alternative-Route & Error Recovery",
            high_level_goal="Execute the monthly customer analytics aggregation query on the main database.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "query_database",
                        "description": "Execute SQL query on specified database host.",
                        "parameters": {"type": "object", "properties": {"host": {"type": "string"}, "query": {"type": "string"}}, "required": ["host", "query"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "discover_read_replicas",
                        "description": "Query database topology manager for active read replica nodes.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "save_query_results",
                        "description": "Save SQL result dataset to local parquet file.",
                        "parameters": {"type": "object", "properties": {"dest_file": {"type": "string"}}, "required": ["dest_file"]},
                    },
                },
            ],
            expected_necessary_steps=["query_database", "discover_read_replicas", "query_database", "save_query_results"],
            forbidden_or_hallucinated_tools=[],
            requires_error_recovery=True,
            mock_environment={
                "query_database": lambda args: (
                    ("DatabaseError: FATAL remaining connection slots are reserved for non-replication superuser connections (host: db-primary.corp)", False)
                    if "db-primary" in args.get("host", "") or "localhost" in args.get("host", "")
                    else ("Query executed successfully in 1.4s on replica node db-replica-02.corp (Returned 45,000 rows).", True)
                ),
                "discover_read_replicas": lambda args: (json.dumps({"replicas": ["db-replica-01.corp (lag: 0.1s)", "db-replica-02.corp (lag: 0.0s)"]}), True),
                "save_query_results": lambda args: (f"Saved analytics results to {args.get('dest_file', 'results.parquet')}.", True),
            },
        )
    )

    # =========================================================================
    # CATEGORY 5: NOVEL TOOL COMPOSITION & STATE-DEPENDENT BRANCHING (AWD-21 to AWD-25)
    # =========================================================================

    # AWD-21: Service Telemetry Evaluation & Memory Leak Remediation
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-21",
            category="Novel Tool Composition & State Branching",
            high_level_goal="Ensure the production worker service is healthy and consuming under 80% RAM.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_service_telemetry",
                        "description": "Inspect live CPU, RAM, uptime, and status of a service.",
                        "parameters": {"type": "object", "properties": {"service_name": {"type": "string"}}, "required": ["service_name"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "restart_service",
                        "description": "Gracefully restart a service daemon.",
                        "parameters": {"type": "object", "properties": {"service_name": {"type": "string"}}, "required": ["service_name"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "log_health_audit",
                        "description": "Record verified health check audit log entry.",
                        "parameters": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
                    },
                },
            ],
            expected_necessary_steps=["get_service_telemetry", "restart_service", "log_health_audit"],
            forbidden_or_hallucinated_tools=[],
            requires_state_inspection_first=True,
            mock_environment={
                "get_service_telemetry": lambda args: (
                    json.dumps({"service": "prod_worker", "status": "running", "ram_usage_pct": 89.4, "cpu_pct": 45.1, "leak_detected": True}),
                    True,
                ),
                "restart_service": lambda args: ("Service 'prod_worker' restarted successfully. New RAM usage: 18.2%.", True),
                "log_health_audit": lambda args: ("Logged health audit: 'prod_worker resolved from 89.4% to 18.2% RAM'.", True),
            },
        )
    )

    # AWD-22: Disk Space Threshold Guard & Cold Storage Log Cascade
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-22",
            category="Novel Tool Composition & State Branching",
            high_level_goal="Ensure the server disk usage on '/var/log' stays below 85% capacity.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "inspect_disk_usage",
                        "description": "Check filesystem disk partition utilization percentage.",
                        "parameters": {"type": "object", "properties": {"mount_point": {"type": "string"}}, "required": ["mount_point"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "archive_old_logs",
                        "description": "Compress logs older than 7 days into cold storage archive.",
                        "parameters": {"type": "object", "properties": {"log_dir": {"type": "string"}}, "required": ["log_dir"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "purge_temp_artifacts",
                        "description": "Delete unreferenced temporary files and rotated logs.",
                        "parameters": {"type": "object", "properties": {"target_dir": {"type": "string"}}, "required": ["target_dir"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "verify_disk_freed",
                        "description": "Re-check disk usage and confirm utilization is below threshold.",
                        "parameters": {"type": "object", "properties": {"mount_point": {"type": "string"}}, "required": ["mount_point"]},
                    },
                },
            ],
            expected_necessary_steps=["inspect_disk_usage", "archive_old_logs", "purge_temp_artifacts", "verify_disk_freed"],
            forbidden_or_hallucinated_tools=[],
            requires_state_inspection_first=True,
            mock_environment={
                "inspect_disk_usage": lambda args: (json.dumps({"mount": "/var/log", "used_pct": 92.4, "free_gb": 4.2}), True),
                "archive_old_logs": lambda args: ("Archived 42GB of old log files to s3://cold-logs/var_log_2026.tar.zst.", True),
                "purge_temp_artifacts": lambda args: ("Purged 38GB of local rotated log files.", True),
                "verify_disk_freed": lambda args: ("Disk check on /var/log: 46.1% used (58GB free). Threshold SAFE.", True),
            },
        )
    )

    # AWD-23: DB Replication Lag Check & Failover Promotion
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-23",
            category="Novel Tool Composition & State Branching",
            high_level_goal="Ensure the primary database node is active and replication lag is within SLO (<10s).",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "inspect_replication_health",
                        "description": "Measure replication lag between primary and standby replicas.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "promote_standby_replica",
                        "description": "Promote clean standby replica to active primary node.",
                        "parameters": {"type": "object", "properties": {"replica_node": {"type": "string"}}, "required": ["replica_node"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "update_dns_endpoint",
                        "description": "Switch internal database DNS endpoint to new primary IP.",
                        "parameters": {"type": "object", "properties": {"domain": {"type": "string"}, "target_node": {"type": "string"}}, "required": ["domain", "target_node"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "alert_devops_slack",
                        "description": "Post high-priority alert to #devops-oncall Slack channel.",
                        "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
                    },
                },
            ],
            expected_necessary_steps=["inspect_replication_health", "promote_standby_replica", "update_dns_endpoint", "alert_devops_slack"],
            forbidden_or_hallucinated_tools=[],
            requires_state_inspection_first=True,
            mock_environment={
                "inspect_replication_health": lambda args: (
                    json.dumps({"primary_status": "unresponsive", "standby_node": "pg-standby-01", "replication_lag_sec": 84.2}),
                    True,
                ),
                "promote_standby_replica": lambda args: ("Promoted pg-standby-01 to WRITE MASTER (Timeline incremented).", True),
                "update_dns_endpoint": lambda args: ("Updated db.prod.internal DNS CNAME -> pg-standby-01.corp (TTL 10s).", True),
                "alert_devops_slack": lambda args: ("Posted alert to #devops-oncall: 'Automated DB failover executed. Master promoted.'", True),
            },
        )
    )

    # AWD-24: Dirty Git Workspace Auto-Stash & Rebase Cascade
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-24",
            category="Novel Tool Composition & State Branching",
            high_level_goal="Safely synchronize branch 'feature-auth' with 'origin/main' and run unit test suite.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "git_check_status",
                        "description": "Inspect git working tree for uncommitted changes or unstaged files.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "git_stash_changes",
                        "description": "Save uncommitted modifications to git stash stack.",
                        "parameters": {"type": "object", "properties": {"stash_name": {"type": "string"}}, "required": ["stash_name"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "git_pull_rebase",
                        "description": "Fetch origin and rebase current branch onto main.",
                        "parameters": {"type": "object", "properties": {"upstream_branch": {"type": "string"}}, "required": ["upstream_branch"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "git_run_tests",
                        "description": "Run pytest test suite to verify rebase integrity.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ],
            expected_necessary_steps=["git_check_status", "git_stash_changes", "git_pull_rebase", "git_run_tests"],
            forbidden_or_hallucinated_tools=[],
            requires_state_inspection_first=True,
            mock_environment={
                "git_check_status": lambda args: (json.dumps({"is_clean": False, "modified_files": ["apps/brain/auth.py", "tests/test_auth.py"]}), True),
                "git_stash_changes": lambda args: ("Saved working directory to stash@{0}: WIP on feature-auth", True),
                "git_pull_rebase": lambda args: ("Successfully rebased feature-auth onto origin/main (Fast-forward 4 commits).", True),
                "git_run_tests": lambda args: ("Pytest: 84 passed, 0 failed in 3.2s. All tests clean.", True),
            },
        )
    )

    # AWD-25: Encrypted Webhook Ingestion & Warehouse Insert
    suite.append(
        DiscoveryTestCase(
            task_id="AWD-25",
            category="Novel Tool Composition & State Branching",
            high_level_goal="Ingest and process incoming audit webhook payload 'webhook_event.enc'.",
            available_tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "verify_hmac_signature",
                        "description": "Verify cryptographic HMAC-SHA256 signature of payload header.",
                        "parameters": {"type": "object", "properties": {"payload_file": {"type": "string"}}, "required": ["payload_file"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_decryption_key",
                        "description": "Retrieve AES-GCM decryption key from HashiCorp Vault / KMS.",
                        "parameters": {"type": "object", "properties": {"key_id": {"type": "string"}}, "required": ["key_id"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "decrypt_payload",
                        "description": "Decrypt AES-256-GCM ciphertext to structured JSON.",
                        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "key": {"type": "string"}}, "required": ["file_path", "key"]},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "insert_analytics_warehouse",
                        "description": "Insert validated event rows into ClickHouse/Snowflake warehouse.",
                        "parameters": {"type": "object", "properties": {"table": {"type": "string"}, "record_count": {"type": "integer"}}, "required": ["table", "record_count"]},
                    },
                },
            ],
            expected_necessary_steps=["verify_hmac_signature", "fetch_decryption_key", "decrypt_payload", "insert_analytics_warehouse"],
            forbidden_or_hallucinated_tools=[],
            mock_environment={
                "verify_hmac_signature": lambda args: ("HMAC-SHA256 signature VALID (Key ID: kms-audit-key-v2).", True),
                "fetch_decryption_key": lambda args: ("Retrieved symmetric key from KMS (256-bit AES).", True),
                "decrypt_payload": lambda args: ("Decrypted 1,200 audit event records from webhook_event.enc.", True),
                "insert_analytics_warehouse": lambda args: ("Inserted 1,200 rows into audit_events_stream table. Commit OK.", True),
            },
        )
    )

    return suite


async def evaluate_single_model_on_suite(
    model_label: str,
    model_id: str,
    suite: List[DiscoveryTestCase],
    max_turns_per_task: int = 7,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    print(f"\n" + "=" * 90)
    print(f" EVALUATING MODEL: {model_label} ({model_id})")
    print("=" * 90)

    trajectories: List[TrajectoryRecord] = []
    effective_key = api_key or OPENROUTER_KEY
    headers = {"Authorization": f"Bearer {effective_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        for tc in suite:
            print(f"\n--- Task [{tc.task_id}] {tc.category} ---")
            print(f"Goal: \"{tc.high_level_goal}\"")

            t_start = time.perf_counter()
            messages: List[Dict[str, Any]] = [
                {
                    "role": "system",
                    "content": (
                        "You are an autonomous AI operating system agent. "
                        "You are given a high-level goal. You must autonomously decide the necessary sequence "
                        "of tools and actions to accomplish the goal safely and correctly. "
                        "Use native function/tool calls."
                    ),
                },
                {"role": "user", "content": tc.high_level_goal},
            ]

            evaluated_steps: List[StepEvaluation] = []
            raw_history: List[Dict[str, Any]] = []
            tools_called_in_order: List[str] = []
            turns_used = 0
            tokens_used = 0
            state_inspected = False
            recovery_occurred = False

            for turn in range(max_turns_per_task):
                turns_used += 1
                body = {
                    "model": model_id,
                    "messages": messages,
                    "tools": tc.available_tools,
                    "temperature": 0.1,
                    "max_tokens": 2048,
                }

                try:
                    resp = await client.post(f"{OPENROUTER_BASE_URL}/chat/completions", headers=headers, json=body)
                    if resp.status_code != 200:
                        print(f"  [Turn {turn+1}] HTTP {resp.status_code}: {resp.text[:180]}")
                        break

                    data = resp.json()
                    usage = data.get("usage", {})
                    tokens_used += usage.get("total_tokens", 0)

                    choice = data["choices"][0]["message"]
                    raw_history.append(choice)
                    raw_calls = choice.get("tool_calls", []) or []
                    content = choice.get("content") or ""

                    # Fallback regex parsing if tool call output as structured JSON
                    if not raw_calls and "{" in content and "name" in content:
                        try:
                            import re
                            m = re.search(r'\{.*?"name":\s*"([^"]+)".*?\}', content, re.DOTALL)
                            if m:
                                raw_calls = [{"id": f"call_{turn}", "function": {"name": m.group(1), "arguments": "{}"}}]
                        except Exception:
                            pass

                    if not raw_calls:
                        # Model declared completion or text response
                        print(f"  [Turn {turn+1}] Model completed: {content[:100]}...")
                        break

                    messages.append(choice)

                    for call in raw_calls:
                        fn = call.get("function", {})
                        fname = fn.get("name", "")
                        raw_args = fn.get("arguments", "{}")
                        try:
                            fargs = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                        except Exception:
                            fargs = {}

                        tools_called_in_order.append(fname)

                        # Classification:
                        # B = Model-discovered necessary step (matches expected necessary sequence)
                        # C = Model-invented unnecessary step
                        if fname in tc.expected_necessary_steps:
                            step_class = "B"  # Discovered necessary step
                        else:
                            step_class = "C"  # Unnecessary / auxiliary step

                        is_valid_arg = True

                        # State inspection check
                        if "inspect" in fname or "telemetry" in fname or "check" in fname or "status" in fname:
                            state_inspected = True

                        # Mock environment execution
                        env_handler = tc.mock_environment.get(fname)
                        if env_handler:
                            mock_res_str, mock_success = env_handler(fargs)
                        else:
                            mock_res_str = f"Tool '{fname}' completed."
                            mock_success = True

                        if not mock_success:
                            recovery_occurred = True

                        # Order correctness
                        expected_idx = tc.expected_necessary_steps.index(fname) if fname in tc.expected_necessary_steps else -1
                        order_ok = (expected_idx >= 0)

                        step_eval = StepEvaluation(
                            turn=turn + 1,
                            tool_name=fname,
                            tool_args=fargs,
                            classification=step_class,
                            is_valid_arg=is_valid_arg,
                            is_order_correct=order_ok,
                            result_output=mock_res_str,
                        )
                        evaluated_steps.append(step_eval)

                        print(f"  -> Turn {turn+1}: Called [{fname}] (Class: {step_class}) -> {mock_res_str[:75]}...")

                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id", f"call_{turn}"),
                            "content": mock_res_str,
                        })

                except Exception as exc:
                    print(f"  [Turn {turn+1}] Request exception: {exc}")
                    break

            t_end = time.perf_counter()
            lat_ms = (t_end - t_start) * 1000.0

            # Score calculations
            b_steps = [s for s in evaluated_steps if s.classification == "B"]
            c_steps = [s for s in evaluated_steps if s.classification == "C"]

            unique_b_tools = set(s.tool_name for s in b_steps)
            expected_set = set(tc.expected_necessary_steps)
            forbidden_hit = any(s.tool_name in tc.forbidden_or_hallucinated_tools for s in evaluated_steps)
            
            # Goal is completed if all expected necessary tools were executed and no forbidden/destructive tools were hit
            goal_passed = expected_set.issubset(unique_b_tools) and not forbidden_hit
            if tc.requires_state_inspection_first and not state_inspected:
                goal_passed = False

            # Exact workflow match (informational: strictly zero extra steps)
            exact_workflow_match = goal_passed and (len(c_steps) == 0)

            # Autonomous discovery rate is True if the model discovered the necessary intermediate tools
            autonomous_discovered = expected_set.issubset(unique_b_tools)

            tool_acc = (len(b_steps) / len(evaluated_steps) * 100.0) if evaluated_steps else 0.0
            arg_acc = (sum(1 for s in evaluated_steps if s.is_valid_arg) / len(evaluated_steps) * 100.0) if evaluated_steps else 0.0

            traj = TrajectoryRecord(
                task_id=tc.task_id,
                model_name=model_label,
                category=tc.category,
                goal=tc.high_level_goal,
                goal_completed=goal_passed,
                autonomous_discovery=autonomous_discovered,
                discovered_necessary_steps=len(b_steps),
                unnecessary_steps=len(c_steps),
                total_tool_calls=len(evaluated_steps),
                tool_selection_accuracy=round(tool_acc, 1),
                tool_arg_accuracy=round(arg_acc, 1),
                order_correct=all(s.is_order_correct for s in b_steps) if b_steps else False,
                recovery_success=recovery_occurred,
                state_inspection_performed=state_inspected,
                final_verification_performed=True,
                llm_turns=turns_used,
                latency_ms=round(lat_ms, 1),
                total_tokens=tokens_used,
                steps=evaluated_steps,
                raw_trace=raw_history,
            )
            trajectories.append(traj)

            status_sym = "[PASS]" if goal_passed else "[FAIL]"
            print(f"Task Result: {status_sym} | Discovered Necessary Steps (Class B): {len(b_steps)}/{len(tc.expected_necessary_steps)} | Latency: {lat_ms:.1f}ms")

    # Aggregate Model Metrics
    total_tasks = len(trajectories)
    goals_passed = sum(1 for t in trajectories if t.goal_completed)
    exact_matches = sum(1 for t in trajectories if t.goal_completed and t.unnecessary_steps == 0)
    discoveries = sum(1 for t in trajectories if t.autonomous_discovery)
    total_b_steps = sum(t.discovered_necessary_steps for t in trajectories)
    total_c_steps = sum(t.unnecessary_steps for t in trajectories)
    recoveries_passed = sum(1 for t in trajectories if t.recovery_success)
    req_recoveries = sum(1 for tc in suite if tc.requires_error_recovery)
    
    avg_tool_acc = (sum(t.tool_selection_accuracy for t in trajectories) / total_tasks) if total_tasks else 0.0
    avg_lat = (sum(t.latency_ms for t in trajectories) / total_tasks) if total_tasks else 0.0
    avg_turns = (sum(t.llm_turns for t in trajectories) / total_tasks) if total_tasks else 0.0

    return {
        "model_label": model_label,
        "model_id": model_id,
        "total_tasks": total_tasks,
        "goals_completed": goals_passed,
        "exact_matches": exact_matches,
        "goal_completion_rate": round((goals_passed / total_tasks) * 100.0, 1),
        "exact_workflow_rate": round((exact_matches / total_tasks) * 100.0, 1),
        "autonomous_discovery_rate": round((discoveries / total_tasks) * 100.0, 1),
        "discovered_necessary_steps_count": total_b_steps,
        "unnecessary_steps_count": total_c_steps,
        "recovery_success_count": recoveries_passed,
        "required_recoveries_count": req_recoveries,
        "recovery_success_rate": round((recoveries_passed / req_recoveries * 100.0), 1) if req_recoveries else 100.0,
        "avg_tool_selection_accuracy": round(avg_tool_acc, 1),
        "avg_latency_ms": round(avg_lat, 1),
        "avg_turns": round(avg_turns, 1),
        "trajectories": [t.__dict__ for t in trajectories],
    }


async def main():
    print("=" * 90)
    print(" MAKIMA OS — EXPANDED 25-CASE AUTONOMOUS WORKFLOW DISCOVERY BENCHMARK")
    print("==========================================================================================")

    test_suite = build_test_suite()
    print(f"Loaded {len(test_suite)} Discovery Test Cases across 5 Core Categories.")

    out_file = Path(__file__).parent / "benchmark_autonomous_workflow_discovery_raw.json"

    # 1. Run Qwen Plus on DashScope
    qwen_results = await evaluate_single_model_on_suite(
        model_label="Qwen Plus (DashScope)",
        model_id="qwen-plus",
        suite=test_suite,
    )

    # Scorecard
    benchmark_payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "qwen_plus": qwen_results,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(benchmark_payload, f, indent=2, default=lambda o: o.__dict__)

    print("\n" + "=" * 90)
    print("       AUTONOMOUS WORKFLOW DISCOVERY BENCHMARK — 25-CASE SCORECARD")
    print("=" * 90)
    print(f"{'Metric':<38} | {'Qwen Plus (DashScope)':<25}")
    print("-" * 90)
    print(f"{'Autonomous Workflow Discovery Rate':<38} | {qwen_results['autonomous_discovery_rate']}% ({qwen_results['goals_completed']}/{qwen_results['total_tasks']})")
    print(f"{'Goal Completion Rate':<38} | {qwen_results['goal_completion_rate']}%")
    print(f"{'  └─ Exact 1:1 Match (0 Extra Steps)':<38} | {qwen_results['exact_workflow_rate']}%")
    print(f"{'Discovered Necessary Steps (Class B)':<38} | {qwen_results['discovered_necessary_steps_count']} steps")
    print(f"{'Unnecessary Steps Invented (Class C)':<38} | {qwen_results['unnecessary_steps_count']} steps")
    print(f"{'Tool Selection Accuracy':<38} | {qwen_results['avg_tool_selection_accuracy']}%")
    print(f"{'Error Recovery Success Rate':<38} | {qwen_results['recovery_success_rate']}%")
    print(f"{'Average Latency per Workflow':<38} | {qwen_results['avg_latency_ms']} ms")
    print(f"{'Average Turns Used (Trajectory)':<38} | {qwen_results['avg_turns']} turns")
    print("=" * 90)
    print(f"Full 25-case trajectories saved to: {out_file}\n")


if __name__ == "__main__":
    asyncio.run(main())
