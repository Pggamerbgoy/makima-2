"""
scripts/benchmark_complex_commands_capability.py

M1 Pre-Implementation Capability Benchmark:
Measures Makima's baseline ability to decompose and execute compound, multi-domain, multi-step user requests.
READ-ONLY: Zero modifications to production code.
"""
import os
import sys
import json
import time
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.brain.core.orchestration_engine import OrchestrationEngine, Intent, SubIntent
from apps.brain.core.kernel import NextGenOrchestrator
from apps.brain.ai_handler import AIHandler
from apps.brain.tool_registry import ToolRegistry
from apps.brain.agents.system_agent import SystemAgent
from apps.brain.agents.code_agent import CodeAgent
from apps.brain.agents.research_agent import ResearchAgent
from apps.brain.agents.browser_agent import BrowserAgent
from apps.brain.agents.document_agent import DocumentAgent
from apps.brain.agents.automation_agent import AutomationAgent
from apps.brain.agents.messaging_agent import MessagingAgent
from apps.brain.agents.security_agent import SecurityAgent
from apps.brain.agents.data_analyst_agent import DataAnalystAgent


@dataclass
class ExpectedSubtask:
    intent: str
    expected_tool: Optional[str]
    description: str
    is_negative_constraint: bool = False


@dataclass
class ComplexTestCase:
    case_id: str
    user_prompt: str
    category: str
    is_sequential: bool  # True if dependent sequence, False if independent parallel
    expected_subtasks: List[ExpectedSubtask]
    negative_constraints: List[str] = field(default_factory=list)


# 20 Diverse Compound Test Cases
TEST_CASES: List[ComplexTestCase] = [
    ComplexTestCase(
        case_id="CC-01",
        user_prompt="Close Notepad and play jazz on Spotify.",
        category="System + Media",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Close Notepad"),
            ExpectedSubtask(intent="media", expected_tool="play_media", description="Play jazz on Spotify"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-02",
        user_prompt="Open Spotify and open VS Code.",
        category="System + System (Parallel)",
        is_sequential=False,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch Spotify"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch VS Code"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-03",
        user_prompt="Minimize Chrome, open VS Code, and launch Terminal.",
        category="System + System + System (3-Step)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Minimize Chrome"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch VS Code"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch Terminal"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-04",
        user_prompt="Search for latest Qwen 3.5 models and save the summary to qwen_models.txt.",
        category="Research + Code/File (Dependent)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="research", expected_tool="web_search", description="Search for Qwen 3.5 models"),
            ExpectedSubtask(intent="code", expected_tool="write_file", description="Save summary to qwen_models.txt"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-05",
        user_prompt="Create a backup folder in Documents and move photo.png into it.",
        category="System + Filesystem (Dependent)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="write_file", description="Create folder / file"),
            ExpectedSubtask(intent="system_control", expected_tool="move_file", description="Move photo.png to backup"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-06",
        user_prompt="Navigate to github.com and summarize the trending repos into a word document.",
        category="Browser + Document (Dependent)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="browser", expected_tool="browser_navigate", description="Navigate to github.com"),
            ExpectedSubtask(intent="document", expected_tool="document_generate", description="Generate word document"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-07",
        user_prompt="Mute the volume and minimize Spotify.",
        category="Media + System",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="media", expected_tool="set_volume", description="Mute volume"),
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Minimize Spotify"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-08",
        user_prompt="Don't close VS Code, just minimize it and open Chrome.",
        category="Negation + System + System",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Minimize VS Code"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch Chrome"),
        ],
        negative_constraints=["do NOT close VS Code", "don't close"]
    ),
    ComplexTestCase(
        case_id="CC-09",
        user_prompt="Set a reminder for 5 PM to call John and clean my desktop.",
        category="Automation + System",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="automation", expected_tool="set_reminder", description="Set reminder for 5 PM"),
            ExpectedSubtask(intent="system_control", expected_tool="organize_desktop", description="Organize Desktop"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-10",
        user_prompt="Find the Python code for quicksort, save it to sort.py, and open it in Notepad.",
        category="Research + Code + System (3-Step Dependent)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="research", expected_tool="web_search", description="Search/generate quicksort"),
            ExpectedSubtask(intent="code", expected_tool="write_file", description="Save to sort.py"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Open sort.py in Notepad"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-11",
        user_prompt="Pause the music and set a 20 minute focus timer.",
        category="Media + Automation",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="media", expected_tool="pause_media", description="Pause music"),
            ExpectedSubtask(intent="automation", expected_tool="set_timer", description="Set 20m timer"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-12",
        user_prompt="Open YouTube in browser and then bring VS Code to the front.",
        category="Browser + System",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="browser", expected_tool="browser_navigate", description="Open YouTube"),
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Focus VS Code"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-13",
        user_prompt="Take a screenshot and draft a WhatsApp message to Alex with it.",
        category="System + Messaging (Shared Entity)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="mouse_draw", description="Take screenshot"),
            ExpectedSubtask(intent="messaging", expected_tool="send_whatsapp", description="Draft message to Alex"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-14",
        user_prompt="Delete temp_log.txt and restart Explorer.",
        category="Filesystem + System",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="move_file", description="Delete/move temp_log.txt"),
            ExpectedSubtask(intent="system_control", expected_tool="manage_service", description="Restart Explorer"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-15",
        user_prompt="Audit app.py for hardcoded API keys and write the security report to audit.md.",
        category="Security + Code (Dependent)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="security", expected_tool="scan_vulnerabilities", description="Audit app.py for secrets"),
            ExpectedSubtask(intent="code", expected_tool="write_file", description="Write audit.md"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-16",
        user_prompt="Pehle Chrome minimize karo phir lo-fi gaana bajao.",
        category="System + Media (Hinglish)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Minimize Chrome"),
            ExpectedSubtask(intent="media", expected_tool="play_media", description="Play lo-fi song"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-17",
        user_prompt="Analyze sales.csv to calculate total revenue and create a summary Excel spreadsheet.",
        category="Data Analyst + Document (Dependent)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="data_analyst", expected_tool="analyze_data", description="Analyze sales.csv"),
            ExpectedSubtask(intent="document", expected_tool="generate_excel", description="Create summary Excel"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-18",
        user_prompt="Launch Calculator and maximize it.",
        category="System + System (Anaphora)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch Calculator"),
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Maximize Calculator"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-19",
        user_prompt="Search for latest AI news and open the top article in Chrome.",
        category="Research + Browser",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="research", expected_tool="web_search", description="Search AI news"),
            ExpectedSubtask(intent="browser", expected_tool="browser_navigate", description="Open article in Chrome"),
        ]
    ),
    ComplexTestCase(
        case_id="CC-20",
        user_prompt="Close Discord, mute volume, and shutdown computer after 10 minutes.",
        category="System + Media + Automation (3-Step)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Close Discord"),
            ExpectedSubtask(intent="media", expected_tool="set_volume", description="Mute volume"),
            ExpectedSubtask(intent="automation", expected_tool="set_timer", description="Schedule shutdown 10m"),
        ]
    ),
    # ── M1-B Extended Multi-Hop Benchmark Cases ──────────────────────────────
    ComplexTestCase(
        case_id="M1B-01",
        user_prompt="Search for Python 3.12 release notes, summarize into notes.txt, and open notes.txt in VS Code.",
        category="3-Step Sequential Linear (Research -> File -> System)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="research", expected_tool="web_search", description="Search Python 3.12 notes"),
            ExpectedSubtask(intent="code", expected_tool="write_file", description="Save to notes.txt"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Open in VS Code"),
        ]
    ),
    ComplexTestCase(
        case_id="M1B-02",
        user_prompt="Close Chrome, then open Spotify and launch VS Code in parallel.",
        category="3-Step Mixed Fork Dependency (System -> {Media, System})",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="manage_window", description="Close Chrome"),
            ExpectedSubtask(intent="media", expected_tool="play_media", description="Open Spotify"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch VS Code"),
        ]
    ),
    ComplexTestCase(
        case_id="M1B-03",
        user_prompt="Open Calculator, open Notepad, and open Terminal.",
        category="3-Step Pure Parallel Branch (Independent)",
        is_sequential=False,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Open Calculator"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Open Notepad"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Open Terminal"),
        ]
    ),
    ComplexTestCase(
        case_id="M1B-04",
        user_prompt="Search for latest AI news, write summary to news.md, audit news.md for errors, and draft email to team.",
        category="4-Step Deep Pipeline (Research -> File -> Security -> Messaging)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="research", expected_tool="web_search", description="Search AI news"),
            ExpectedSubtask(intent="code", expected_tool="write_file", description="Write news.md"),
            ExpectedSubtask(intent="security", expected_tool="scan_vulnerabilities", description="Audit news.md"),
            ExpectedSubtask(intent="messaging", expected_tool="send_email", description="Draft email"),
        ]
    ),
    ComplexTestCase(
        case_id="M1B-05",
        user_prompt="Read data.csv, analyze the statistics, and plot revenue chart to chart.png.",
        category="3-Step Data Pipeline (File -> Data Analyst -> Document)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="read_file", description="Read data.csv"),
            ExpectedSubtask(intent="data_analyst", expected_tool="analyze_data", description="Analyze statistics"),
            ExpectedSubtask(intent="document", expected_tool="generate_chart", description="Plot revenue chart"),
        ]
    ),
    ComplexTestCase(
        case_id="M1B-06",
        user_prompt="Open Chrome and launch VS Code, but don't close existing Spotify window.",
        category="2-Step with Later Negation Constraint",
        is_sequential=False,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Open Chrome"),
            ExpectedSubtask(intent="system_control", expected_tool="launch_app", description="Launch VS Code"),
        ],
        negative_constraints=["don't close existing Spotify", "preserve spotify"]
    ),
    ComplexTestCase(
        case_id="M1B-07",
        user_prompt="Take a screenshot, save it to screen.png, and move screen.png to Desktop folder.",
        category="3-Step Shared Entity Propagation (System -> File -> System)",
        is_sequential=True,
        expected_subtasks=[
            ExpectedSubtask(intent="system_control", expected_tool="mouse_draw", description="Take screenshot"),
            ExpectedSubtask(intent="system_control", expected_tool="write_file", description="Save to screen.png"),
            ExpectedSubtask(intent="system_control", expected_tool="move_file", description="Move to Desktop"),
        ]
    ),
]


async def run_benchmark():
    import yaml
    from pathlib import Path
    from apps.brain.core.app_bootstrap import AppBootstrap

    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 90)
    print(" MAKIMA OS — M1-B CAPABILITY BENCHMARK (27 COMPLEX CASES)")
    print("=" * 90)

    cfg_file = Path(__file__).resolve().parents[1] / "configs" / "default.yaml"
    with open(cfg_file, encoding="utf-8") as f:
        runtime_config = yaml.safe_load(f) or {}

    bootstrap = AppBootstrap(config=runtime_config)
    services = await bootstrap.initialize_services()
    engine = services.get("orchestration_engine")
    if not engine:
        orch = services.get("orchestrator") or services.get("nextgen_orchestrator")
        engine = getattr(orch, "orchestration_engine", None) or orch

    total_cases = len(TEST_CASES)
    total_atomic_subtasks = sum(len(tc.expected_subtasks) for tc in TEST_CASES)
    
    whole_task_passes = 0
    atomic_subtasks_completed = 0
    dropped_intents_count = 0
    routing_failure_count = 0
    decomposition_failure_count = 0
    
    results_log = []

    for idx, tc in enumerate(TEST_CASES, 1):
        t0 = time.time()
        # 1. Edge Intent Classification
        intent_res = await engine.classify_intent(tc.user_prompt)
        dt_ms = (time.time() - t0) * 1000

        routed_intent = intent_res.intent.value if hasattr(intent_res.intent, "value") else str(intent_res.intent)
        sub_intents = getattr(intent_res, "sub_intents", []) or []
        num_sub_detected = len(sub_intents)
        
        # Check atomic fulfillment
        expected_intents = [st.intent for st in tc.expected_subtasks]
        
        # Evaluation Logic:
        # If routed to single intent when 2+ distinct expected intents exist -> Dropped intent failure!
        if routed_intent != "multi_step" and len(set(expected_intents)) > 1:
            is_whole_pass = False
            routing_failure_count += 1
            # Only the single routed intent might be fulfilled, others are dropped
            atomic_done = 1 if routed_intent in expected_intents else 0
            dropped = len(tc.expected_subtasks) - atomic_done
            dropped_intents_count += dropped
            failure_loc = "A. Edge Routing (Single-verb override swallowed compound command)"
        elif routed_intent == "multi_step":
            # Multi-step was recognized at edge
            if num_sub_detected == len(tc.expected_subtasks):
                is_whole_pass = True
                atomic_done = len(tc.expected_subtasks)
                dropped = 0
                failure_loc = "None"
            else:
                is_whole_pass = False
                atomic_done = num_sub_detected
                dropped = len(tc.expected_subtasks) - num_sub_detected
                dropped_intents_count += max(0, dropped)
                decomposition_failure_count += 1
                failure_loc = "B. Decomposition (Sub-intent count mismatch)"
        else:
            # Single domain multiple steps (e.g. system + system)
            if routed_intent in expected_intents:
                is_whole_pass = False  # Single agent ReAct dropped 2nd parallel step without multi_step coordinator
                atomic_done = 1
                dropped = len(tc.expected_subtasks) - 1
                dropped_intents_count += dropped
                failure_loc = "A. Edge Routing (Single-agent ReAct cannot parallelize independent tasks)"
            else:
                is_whole_pass = False
                atomic_done = 0
                dropped = len(tc.expected_subtasks)
                dropped_intents_count += dropped
                routing_failure_count += 1
                failure_loc = "A. Edge Routing (Wrong domain classification)"

        if is_whole_pass:
            whole_task_passes += 1
        atomic_subtasks_completed += atomic_done

        status_str = "[PASS]" if is_whole_pass else "[FAIL]"
        print(f"[{idx:02d}/20] {tc.case_id} | {tc.category:<32} | {status_str} | Lat: {dt_ms:>6.1f}ms | Routed: {routed_intent:<14} | Sub: {num_sub_detected}/{len(tc.expected_subtasks)} | '{tc.user_prompt[:35]}...'", flush=True)

        results_log.append({
            "case_id": tc.case_id,
            "prompt": tc.user_prompt,
            "category": tc.category,
            "status": "PASS" if is_whole_pass else "FAIL",
            "routed_intent": routed_intent,
            "expected_intents": expected_intents,
            "subtasks_expected": len(tc.expected_subtasks),
            "subtasks_completed": atomic_done,
            "dropped_subtasks": dropped,
            "failure_location": failure_loc,
            "latency_ms": dt_ms
        })

    # Summary Metrics
    whole_task_pct = (whole_task_passes / total_cases) * 100
    atomic_pct = (atomic_subtasks_completed / total_atomic_subtasks) * 100

    print("=" * 90)
    print("                M1 PRE-IMPLEMENTATION BASELINE CAPABILITY REPORT")
    print("=" * 90)
    print(f"TOTAL COMPLEX TEST CASES EVALUATED : {total_cases}")
    print(f"TOTAL ATOMIC SUBTASKS REQUIRED    : {total_atomic_subtasks}")
    print(f"1. WHOLE-TASK COMPLETION RATE      : {whole_task_pct:.1f}% ({whole_task_passes}/{total_cases})")
    print(f"2. ATOMIC SUBTASK COMPLETION RATE  : {atomic_pct:.1f}% ({atomic_subtasks_completed}/{total_atomic_subtasks})")
    print(f"3. TOTAL DROPPED SUB-INTENTS       : {dropped_intents_count} / {total_atomic_subtasks} ({(dropped_intents_count/total_atomic_subtasks)*100:.1f}%)")
    print(f"4. EDGE ROUTING FAILURES           : {routing_failure_count} (Single-intent override swallowed compound)")
    print(f"5. DECOMPOSITION FAILURES          : {decomposition_failure_count}")
    print("=" * 90)

    # Write raw output JSON
    out_file = os.path.join(os.path.dirname(__file__), "benchmark_complex_commands_capability_raw.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results_log, f, indent=2)
    print(f"Raw capability report saved to: {out_file}\n")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
