#!/usr/bin/env python3
"""
Makima OS — Multi-Agent Qwen/DashScope Session Runner
Location: scripts/qwen_agent.py

What's new vs qwen_session.py:
- Multiple NAMED agents/sessions, each with its own isolated history file
  (~/.makima/sessions/<agent_name>.json) instead of one global session.
- Agents can run sequentially or in PARALLEL (threaded), each with its
  own conversation history, so you can fan work out across several
  concurrent tasks using one API key.
- More tools: READ_FILE, LIST_DIR, GREP, WRITE_FILE (syntax-checked),
  and a sandboxed RUN_CMD tool with a timeout and output cap.
- System prompt is codebase-analysis-skill-aware: before doing agentic
  work in an existing project, the agent is told to read/produce
  docs/ARCHITECTURE.md and keep docs/PROGRESS.md, docs/DECISIONS.md,
  docs/KNOWN_ISSUES.md up to date, matching SKILL.md's Phase 1 / Phase 2
  protocol. (SKILL.md itself is untouched -- this just wires into it.)
- No hardcoded API key. The key MUST come from the DASHSCOPE_API_KEY
  environment variable. The process exits with an error if it's absent.

Usage:
  export DASHSCOPE_API_KEY="sk-..."

  # single agent, single task
  python scripts/qwen_agent.py run backend "implement the /users endpoint"

  # interactive shell for one named agent
  python scripts/qwen_agent.py --interactive backend

  # run several agents in parallel, each on its own task
  python scripts/qwen_agent.py --parallel \
      backend="implement the /users endpoint" \
      frontend="build the login form" \
      docs="update docs/ARCHITECTURE.md for the new auth flow"

  # session management
  python scripts/qwen_agent.py --list
  python scripts/qwen_agent.py --status backend
  python scripts/qwen_agent.py --reset backend
"""

import concurrent.futures
import json
import os
import py_compile
import re
import subprocess
import sys
import threading
from pathlib import Path

DASHSCOPE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
TOKENROUTER_URL = "https://api.tokenrouter.com/v1/chat/completions"
TOKENROUTER_KEY = os.getenv("TOKENROUTER_API_KEY", "")
SESSION_DIR = Path.home() / ".makima" / "sessions"

CODEBASE_ANALYSIS_BRIEF = """
CODEBASE ANALYSIS & LIVING DOCUMENTATION PROTOCOL (see .agents/skills/codebase-analysis/SKILL.md for full details):
You have no memory between sessions. This protocol exists so every session starts from real understanding of the codebase and leaves behind an accurate documentation trail.

- When this fires:
  - First task in a project with no docs/ARCHITECTURE.md yet -> run Phase 1 in full before starting feature/module design.
  - Any later task -> Phase 2 only (read before, update after).
  - Major structural change (new service/layer, framework swap, dependency overhaul) -> re-run Phase 1's affected sections.

- Phase 1 — Initial deep analysis (once per project, write to docs/ARCHITECTURE.md):
  [ ] Structure   — directory tree, architectural boundaries, who owns what
  [ ] Stack       — languages/frameworks/libraries + actual installed versions from lockfiles (Tier 1 evidence)
  [ ] Entry points— where execution starts; trace one real request/task end-to-end through every layer it touches
  [ ] Data flow   — external deps: APIs, DBs, queues, other services
  [ ] Conventions — naming, error handling, logging, config patterns actually in use
  [ ] Risk zones  — TODO/FIXME/HACK density, thin-tested critical paths, places where code and docs already disagree
  Note: No row gets deleted for seeming irrelevant. Mark "N/A — [reason]" instead.

- Phase 2 — Continuous tracking while working:
  - Before touching code: read docs/ARCHITECTURE.md and docs/PROGRESS.md if they exist.
  - After any meaningful change: update the relevant doc in the same turn.
  - Doc and code disagree -> fix the doc immediately.
  - Documents to maintain:
    | File | Purpose | Update trigger |
    | docs/ARCHITECTURE.md | Structure, stack, conventions, data flow | Structural change |
    | docs/PROGRESS.md | Done / in-flight / next | End of session / completed subtask |
    | docs/DECISIONS.md | Why, alternatives rejected | Any non-trivial decision (skip if devlog.py exists) |
    | docs/KNOWN_ISSUES.md | Bugs, tech debt, deliberate shortcuts | Shortcut taken / bug found-not-fixed |
  - Entry discipline: short and factual (1-2 lines); prepend new entries under dated headings; past ~200 lines compress older entries; document the "why" and "non-obvious".

- Non-negotiables:
  - Never start Tier 2/3 work in an existing codebase without reading docs/ARCHITECTURE.md first (or producing it first).
  - Never let a doc silently drift from the code it describes (fix in the same turn).
  - Never spin up a second decision log if devlog.py (or equivalent) already owns that job.
  - Governs documentation habits only — does not license unsolicited refactors/renames/restructuring.
  - Adapt to existing project docs layouts.
"""

MASTER_WORKFLOW_BRIEF = """
MASTER-WORKFLOW PROTOCOL (see .agents/skills/master-workflow/SKILL.md for full sequence):
- On EVERY task, you must follow the master execution sequence (MS-1 to MS-15):
  1. MS-1: Triage task into Tier 1 (isolated, blast radius <= 1 file, skip MS-2 to MS-7), Tier 2 (small additions/existing pattern, lightweight pass), or Tier 3 (new modules, subsystems, multi-file, or high-stakes like Auth, PyO3 FFI, system prompts, provider-routing).
  2. MS-2: Understand the problem (inputs, failure modes, unstated implied requirements).
  3. MS-3: Entry Path (Path A: explicit file/module target; Path B: new module/feature).
  4. MS-4: Case Enumeration (Happy-path, edge cases, failure modes, concurrency/timing, integration, platform).
  5. MS-5 ↔ MS-6: Research & Reason Loop (apply Gate F and Gate R per claim/case).
  6. MS-7: Efficiency & Performance (memory, latency, CPU/GPU, tradeoffs under expected load).
  7. MS-8: Draft Design (approach, key decisions, explicitly not doing, file changes, open questions).
  8. MS-9: Merged Critique (adversarial critique of draft: breakage, assumptions, simplicity, security, completeness, failure surface).
  9. MS-10: Write Decision Log (post inline before coding, and append to memory-bank/decisionLog.md if Tier 3/flagged).
  10. MS-11: Last Live Check (verify exact current syntax, signatures, and idioms in official docs).
  11. MS-12: Write Code (Act mode, small testable functions, follow existing patterns, handle errors explicitly).
  12. MS-13: Actually Run It (run scripts, tests, commands; watch output and fix issues).
  13. MS-14: Security Pass (secrets, SQL/command injection, input validation, deserialization, CORS, TLS).
  15. MS-15: Close the Loop (update activeContext.md and progress.md; state what was verified vs not verified).
- ALWAYS execute three Gates:
  - Gate F (Fact-check): Search live, prefer source hierarchy (official docs > PyPI/registry > repo README), cross-check >= 2 sources.
  - Gate R (Reasoning): Decompose, hold multiple hypotheses, overcorrection check, evidence tier tagging (1-6), confidence tag, devil's-advocate pass.
  - Gate T (Re-triage): Re-run triage and MS-9 if task scope/intent shifts mid-work.
"""

RIGOROUS_CODE_DEVELOPMENT_BRIEF = """
RIGOROUS CODE DEVELOPMENT PROTOCOL (see .agents/skills/rigorous-code-development/SKILL.md for details):
- Never assume, always verify: live fact-check package names, system dependencies, API signatures, deprecation status, OS behaviors, version support, default parameter values, and error behaviors. Hedging is not verification.
- Understand implicit/unstated requirements: identify rate limits, retries, connection pooling, size limits, session management, input validation, and observability logs before implementing.
- Run a dedicated Security Pass (MS-14): check for secrets/credentials, SQL injection, command injection, path traversal, unsafe deserialization, insecure defaults, and outdated dependencies. Run security_scan.py if available.
"""

PROBLEM_REASONING_BRIEF = """
PROBLEM-REASONING PROTOCOL (see .agents/skills/problem-reasoning/SKILL.md for details):
- Match the strength of your claims to the strength of your evidence. Avoid under-claiming, over-correcting, or precision collapse.
- Decompose complex or bundled questions before forming a view. State the actual question in precise terms.
- Hold multiple hypotheses: explicitly list and evaluate alternative candidates/explanations.
- Tag every load-bearing conclusion with:
  - Evidence Tier (Tier 1: run myself, Tier 2: official docs, Tier 3: official repo README, Tier 4: unofficial/third-party, Tier 5: training data, Tier 6: logic/speculation).
  - Confidence Level (High, Medium, Low) with a brief justification.
  - Alternatives considered.
  - Falsifying scenario (the scenario that would prove you wrong, and how it was ruled out).
- Run a devil's-advocate pass with a concrete falsifiability test.
"""

CODEBASE_GAP_ANALYSIS_BRIEF = """
CODEBASE GAP ANALYSIS PROTOCOL (see .agents/skills/codebase-gap-analysis/SKILL.md for details):
- Use this skill when asked to find gaps, audit, check production-readiness, find tech debt, or review codebase health.
- Run the heuristic scanner first if available: `python .agents/skills/codebase-gap-analysis/scripts/scan_gaps.py <repo_root> --out /tmp/gaps.json`
- Read findings from the JSON report, treat them as candidates, open the files, and filter out false positives.
- Perform audit across three phases:
  - Phase 1: Structural/correctness (swallowed exceptions, input validation boundaries, security patterns, dead code/stubs, test coverage, consistency).
  - Phase 2: Feature completeness (documented-but-unimplemented features, half-wired features, asymmetric APIs, error/edge UX, i18n/a11y gaps, observability).
  - Phase 3: Improvement opportunities (hand-rolled vs stdlib, complexity updates, outdated deps, manual step automation).
- Assign severity (Critical, High, Medium, Low) with clear explanation of the consequence.
- Follow the required output format: Summary, Critical, High, Medium, Low, Feature Gaps & Incomplete Work, Improvement Opportunities, and "What this scan does NOT cover".
"""

AGENT_SYSTEM_PROMPT = """You are an Autonomous AI Agent Coder & Systems Architect for Makima OS.
You maintain full stateful context across multiple tasks in this active development session.

When assigned a task:
1. Inspect the codebase, read relevant files, and search for patterns autonomously.
2. Formulate a precise engineering plan and implement all necessary code changes.
3. To execute a tool, output one of these commands on a new line:
   - Read file:        <<READ_FILE: relative/path/to/file.py>>
   - List directory:   <<LIST_DIR: relative/path>>
   - Grep search:       <<GREP: pattern>>
   - Run a command:     <<RUN_CMD: shell command here>>
   - Patch a file (PREFERRED for edits — surgical find+replace, does NOT rewrite whole file):
     <<PATCH_FILE: relative/path/to/file.py>>
     <<<FIND
     exact lines to find (copy them exactly from the file)
     >>>REPLACE
     replacement lines
     <<<END
   - Create a NEW file only (never use this to edit an existing file):
     ```python:relative/path/to/new_file.py
     # complete code for a brand new file
     ```
4. When your task is finished and verified, output:
   <<DONE>>

CRITICAL EDITING RULE: When modifying an EXISTING file, ALWAYS use <<PATCH_FILE>> — never rewrite the whole file with a code block. Rewriting entire files destroys unrelated code and is FORBIDDEN for existing files. Use code blocks ONLY when creating a completely new file that does not exist yet.

CRITICAL: Do NOT output HTML/XML-like tags like `<tool_call>`, `<parameter>`, or `<run_command>`. Use ONLY the exact commands defined above.

Always verify syntax after editing. Work autonomously and never ask the user to manually edit code.

You have access to local skill manuals under `.agents/skills/`. Before performing complex tasks or if you need to refresh your understanding, read these files using <<READ_FILE: ...>>.
""" + CODEBASE_ANALYSIS_BRIEF + MASTER_WORKFLOW_BRIEF + RIGOROUS_CODE_DEVELOPMENT_BRIEF + PROBLEM_REASONING_BRIEF + CODEBASE_GAP_ANALYSIS_BRIEF

MODELS_FALLBACK_LIST = [
    "deepseek-v4-pro",
    "qwen3.7-max-preview",
    "qwen3.7-plus",
    "qwen3.7-flash",
]

# If set via --model flag, only this model is tried (no fallback list).
MODEL_OVERRIDE: str = ""

# Safety cap so a runaway command can't hang an agent or flood output.
RUN_CMD_TIMEOUT_SECONDS = 60
RUN_CMD_OUTPUT_CAP = 20000

_print_lock = threading.Lock()


def log(agent_name: str, msg: str, end: str = "\n", flush: bool = True):
    """Thread-safe, agent-tagged print so parallel agents don't interleave garbage."""
    with _print_lock:
        prefix = f"[{agent_name}] "
        if end == "\n":
            print(prefix + msg, end=end, flush=flush)
        else:
            # streaming token output: only tag the first character of a burst
            print(msg, end=end, flush=flush)


class Agent:
    """One named, independently-stateful session against the DashScope API."""

    def __init__(self, name: str, api_key: str):
        self.name = name
        self.api_key = api_key
        self.history_file = SESSION_DIR / f"{name}.json"
        self.messages = self._load_session()

    # ---------- session persistence ----------

    def _load_session(self) -> list:
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data and data[0].get("role") == "system":
                        return data
            except Exception as e:
                log(self.name, f"[SESSION WARN] Could not load session file ({e}); starting fresh.")
        return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]

    def save_session(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log(self.name, f"[SESSION WARN] Failed to save session state: {e}")

    def reset_session(self):
        self.messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        if self.history_file.exists():
            try:
                os.remove(self.history_file)
            except Exception:
                pass
        log(self.name, "[SESSION RESET] History cleared.")

    # ---------- tools ----------

    def _tool_read_file(self, filepath: str) -> str:
        filepath = filepath.strip()
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()[:100000]
            log(self.name, f"[TOOL] Read file: {filepath}")
            return f"\n--- READ_FILE RESULT: {filepath} ---\n{content}\n"
        except Exception as e:
            log(self.name, f"[TOOL ERROR] Read file {filepath}: {e}")
            return f"\n--- READ_FILE ERROR: {filepath}: {e} ---\n"

    def _tool_list_dir(self, dirpath: str) -> str:
        dirpath = dirpath.strip()
        try:
            items = os.listdir(dirpath if dirpath else ".")
            log(self.name, f"[TOOL] Listed dir: {dirpath or '.'}")
            return f"\n--- LIST_DIR RESULT: {dirpath} ---\n" + "\n".join(items[:100]) + "\n"
        except Exception as e:
            return f"\n--- LIST_DIR ERROR: {dirpath}: {e} ---\n"

    def _tool_grep(self, pattern: str, root: str = ".") -> str:
        pattern = pattern.strip()
        matches = []
        try:
            for r, _, files in os.walk(root):
                # skip common noise directories
                if any(part in r for part in (".git", "node_modules", "__pycache__", ".venv")):
                    continue
                for file in files:
                    if file.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".yaml", ".yml")):
                        fpath = os.path.join(r, file)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                for lno, line in enumerate(f, 1):
                                    if pattern.lower() in line.lower():
                                        matches.append(f"{fpath}:{lno}: {line.strip()}")
                        except Exception:
                            continue
            log(self.name, f"[TOOL] Grep: '{pattern}' ({len(matches)} matches)")
            return f"\n--- GREP RESULT: '{pattern}' ---\n" + "\n".join(matches[:50]) + "\n"
        except Exception as e:
            return f"\n--- GREP ERROR: {pattern}: {e} ---\n"

    def _tool_run_cmd(self, command: str) -> str:
        command = command.strip()
        log(self.name, f"[TOOL] Running command: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=RUN_CMD_TIMEOUT_SECONDS,
            )
            out = (result.stdout or "") + (result.stderr or "")
            out = out[:RUN_CMD_OUTPUT_CAP]
            return (
                f"\n--- RUN_CMD RESULT: {command} (exit {result.returncode}) ---\n{out}\n"
            )
        except subprocess.TimeoutExpired:
            return f"\n--- RUN_CMD TIMEOUT: {command} (>{RUN_CMD_TIMEOUT_SECONDS}s) ---\n"
        except Exception as e:
            return f"\n--- RUN_CMD ERROR: {command}: {e} ---\n"

    def _tool_write_file(self, filepath: str, code_block: str) -> str:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(code_block.strip() + "\n")
            log(self.name, f"[TOOL WRITE] Saved: {filepath}")
            if filepath.endswith(".py"):
                py_compile.compile(filepath, doraise=True)
                log(self.name, f"[TOOL VERIFIED] Syntax check passed for {filepath}")
            return f"\n--- WRITE_FILE SUCCESS: {filepath} ---\n"
        except Exception as e:
            log(self.name, f"[TOOL ERROR] Write {filepath}: {e}")
            return f"\n--- WRITE_FILE ERROR: {filepath}: {e} ---\n"

    def _tool_patch_file(self, filepath: str, patch_body: str) -> str:
        """Surgical find+replace inside an existing file. Does NOT rewrite the whole file.

        Patch format (everything between <<<FIND and <<<END):
            <<<FIND
            exact lines to find
            >>>REPLACE
            replacement lines
            <<<END
        Multiple FIND/REPLACE blocks are supported in a single PATCH_FILE call.
        """
        filepath = filepath.strip()
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception as e:
            return f"\n--- PATCH_FILE ERROR: cannot read {filepath}: {e} ---\n"

        content = original
        # Extract all FIND/REPLACE blocks (accept both <<<END and >>>END)
        blocks = re.findall(
            r"<<<FIND\r?\n(.*?)\r?\n?>>>REPLACE\r?\n(.*?)\r?\n?(?:<<<END|>>>END)",
            patch_body,
            re.DOTALL,
        )
        if not blocks:
            return f"\n--- PATCH_FILE ERROR: no valid <<<FIND...>>>REPLACE...<<<END blocks found in patch body ---\n"

        results = []
        for find_text, replace_text in blocks:
            find_text = find_text.rstrip("\n")
            replace_text = replace_text.rstrip("\n")
            if find_text not in content:
                # Try normalizing line endings
                find_normalized = find_text.replace("\r\n", "\n").replace("\r", "\n")
                content_normalized = content.replace("\r\n", "\n").replace("\r", "\n")
                if find_normalized in content_normalized:
                    content = content_normalized.replace(find_normalized, replace_text, 1)
                    results.append(f"PATCHED (normalized line endings)")
                else:
                    results.append(f"NOT FOUND: {repr(find_text[:80])}")
                continue
            content = content.replace(find_text, replace_text, 1)
            results.append(f"PATCHED")

        if content == original:
            return f"\n--- PATCH_FILE WARN: no changes applied to {filepath} (find text not matched?) ---\n"

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            log(self.name, f"[TOOL PATCH] Patched: {filepath} ({len(blocks)} block(s))")
            if filepath.endswith(".py"):
                py_compile.compile(filepath, doraise=True)
                log(self.name, f"[TOOL VERIFIED] Syntax check passed for {filepath}")
            return f"\n--- PATCH_FILE SUCCESS: {filepath} | Results: {results} ---\n"
        except Exception as e:
            log(self.name, f"[TOOL ERROR] Patch write {filepath}: {e}")
            return f"\n--- PATCH_FILE ERROR: {filepath}: {e} ---\n"

    def _run_all_tools(self, full_content: str) -> str:
        tool_results = ""

        # Helper function to extract robust commands
        def find_commands(tag: str) -> list[str]:
            cmds = re.findall(rf"<+{tag}:\s*(.+?)>+", full_content, re.DOTALL)
            return [c.strip() for c in cmds if c.strip()]

        for path in find_commands("READ_FILE"):
            tool_results += self._tool_read_file(path)

        for path in find_commands("LIST_DIR"):
            tool_results += self._tool_list_dir(path)

        for pattern in find_commands("GREP"):
            tool_results += self._tool_grep(pattern)

        for cmd in find_commands("RUN_CMD"):
            tool_results += self._tool_run_cmd(cmd)

        # PATCH_FILE: surgical find+replace (multi-line body extracted between tag and next tag)
        patch_matches = re.findall(
            r"<+PATCH_FILE:\s*([^\n>]+)>+\s*\n(.*?)(?=<+[A-Z_]+:|```[a-z]*:|$)",
            full_content,
            re.DOTALL,
        )
        for patch_filepath, patch_body in patch_matches:
            tool_results += self._tool_patch_file(patch_filepath.strip(), patch_body)

        # Handle XML-style tool call syntax fallback:
        xml_patches = re.findall(
            r'<parameter name="command">PATCH_FILE</parameter>\s*<parameter name="arguments">(.*?)</parameter>',
            full_content,
            re.DOTALL,
        )
        for args_json in xml_patches:
            try:
                parsed_args = json.loads(args_json.strip())
                if isinstance(parsed_args, dict) and "file" in parsed_args and "patch" in parsed_args:
                    tool_results += self._tool_patch_file(parsed_args["file"], parsed_args["patch"])
            except Exception:
                pass

        # Also support running XML-like native tool calls as a fallback!
        # E.g. <tool_call>\n run_command >\npython -c "..."\n</parameter>
        native_cmds = re.findall(r"<tool_call>\s*\w+\s*>\s*\n(.*?)\n(?:</parameter>|</tool_call>)", full_content, re.DOTALL)
        for cmd in native_cmds:
            cmd_clean = cmd.strip()
            if cmd_clean and cmd_clean not in tool_results:
                tool_results += self._tool_run_cmd(cmd_clean)

        # Catch <parameter=command> command </parameter>
        param_cmds = re.findall(r"<parameter(?:=|\s+name=)[\"']?command[\"']?>(.*?)</parameter>", full_content, re.DOTALL)
        for cmd in param_cmds:
            cmd_clean = cmd.strip()
            if cmd_clean and cmd_clean not in tool_results:
                tool_results += self._tool_run_cmd(cmd_clean)

        # Flexible Kimi K3 tool call extractor
        call_blocks = re.findall(r'call tool="([^"]+)"(.*?)(?=<\|close\|>|call tool=|</tool>|$)', full_content, re.DOTALL)
        for tspec, body in call_blocks:
            tspec_clean = tspec.split(":")[0].strip().upper()
            # Extract path or target from body
            arg_match = re.search(r'(?:path|file|pattern|command|key="[^"]+")\s*[:=]?\s*["\']?([^"\':\n<]+)', body, re.IGNORECASE)
            val = arg_match.group(1).strip() if arg_match else "."
            if "LIST" in tspec_clean or "DIR" in tspec_clean:
                tool_results += self._tool_list_dir(val)
            elif "READ" in tspec_clean:
                tool_results += self._tool_read_file(val)
            elif "GREP" in tspec_clean:
                tool_results += self._tool_grep(val)
            elif "RUN" in tspec_clean or "CMD" in tspec_clean:
                tool_results += self._tool_run_cmd(val)

        code_matches = re.findall(
            r"```(?:python)?:([a-zA-Z0-9_\-\./\\]+\.\w+)\s*\n(.*?)\n```", full_content, re.DOTALL
        )
        for filepath, code_block in code_matches:
            tool_results += self._tool_write_file(filepath, code_block)

        return tool_results

    # ---------- main loop ----------

    def run_task(self, task_prompt: str, max_turns: int = 60):
        log(self.name, "=" * 56)
        log(self.name, "TASK EXECUTION")
        log(self.name, f"TASK: {task_prompt}")
        log(self.name, "=" * 56)

        self.messages.append({"role": "user", "content": task_prompt})

        for turn in range(1, max_turns + 1):
            log(self.name, f"--- TURN {turn}/{max_turns} ---")
            result = self._call_model_with_fallback()
            if result is None:
                log(self.name, "[ERROR] All fallback models exhausted. Stopping.")
                self.save_session()
                return

            full_content = result["content"]
            full_reasoning = result["reasoning_content"]

            msg_entry = {"role": "assistant", "content": full_content}
            if full_reasoning:
                msg_entry["reasoning_content"] = full_reasoning
            self.messages.append(msg_entry)
            self.save_session()

            if "<<DONE>>" in full_content or "<DONE>" in full_content:
                log(self.name, "[FINISHED] Task completed.")
                break

            tool_results = self._run_all_tools(full_content)
            if not tool_results:
                if any(tag in full_content for tag in ["<invoke", "<tool_call", "<function_call", "PATCH_FILE", "RUN_CMD", "READ_FILE", "LIST_DIR", "GREP"]):
                    log(self.name, "[INFO] Caught hallucinated XML tool call. Sending format reminder.")
                    tool_results = "\n--- SYSTEM ERROR: Unrecognized tool format. You MUST use <<RUN_CMD: command>>, <<READ_FILE: path>>, <<PATCH_FILE: file>>\\n...patch... or ```python:file.py``` syntax. To browse the web, use <<RUN_CMD: curl -s URL>>. Do NOT use <invoke> tags. ---\n"
                else:
                    log(self.name, "[INFO] No tools invoked this turn. Ending loop.")
                    break

            self.messages.append({"role": "user", "content": f"Tool Execution Results:\n{tool_results}"})
            self.save_session()

    def _call_model_with_fallback(self):
        import urllib.error
        import urllib.request

        model_list = [MODEL_OVERRIDE] if MODEL_OVERRIDE else MODELS_FALLBACK_LIST
        for idx, model in enumerate(model_list):
            full_content = ""
            full_reasoning = ""
            try:
                if "moonshotai" in model or "kimi" in model or "glm" in model:
                    target_url = TOKENROUTER_URL
                    target_key = TOKENROUTER_KEY
                    payload = {
                        "model": model,
                        "messages": self.messages,
                        "stream": True,
                    }
                else:
                    target_url = DASHSCOPE_URL
                    target_key = self.api_key
                    payload = {
                        "model": model,
                        "messages": self.messages,
                        "stream": True,
                        "enable_thinking": True,
                    }

                req = urllib.request.Request(
                    target_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {target_key}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    method="POST",
                )
                in_thinking = False
                in_content = False
                with urllib.request.urlopen(req, timeout=None) as resp:
                    for line in resp:
                        line_str = line.decode("utf-8").strip()
                        if not line_str or line_str == "data: [DONE]":
                            continue
                        if line_str.startswith("data: "):
                            data_json = json.loads(line_str[6:])
                            choices = data_json.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content = delta.get("content") or ""
                                if reasoning:
                                    full_reasoning += reasoning
                                    if not in_thinking:
                                        log(self.name, "\n[THINKING]\n", flush=True)
                                        in_thinking = True
                                    log(self.name, reasoning, end="", flush=True)
                                if content:
                                    full_content += content
                                    if in_thinking and not in_content:
                                        log(self.name, "\n[ANSWER]\n", flush=True)
                                        in_content = True
                                    log(self.name, content, end="", flush=True)
                log(self.name, "\n")
                return {"content": full_content, "reasoning_content": full_reasoning}
            except Exception as e:
                log(self.name, f"\n[FALLBACK] {model} failed ({e}). Trying next model...")
                if idx == len(model_list) - 1:
                    return None
        return None


# ---------------- orchestration ----------------

def load_env():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

def get_api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY", "")


def list_agents():
    if not SESSION_DIR.exists():
        print("No agents yet.")
        return
    files = sorted(SESSION_DIR.glob("*.json"))
    if not files:
        print("No agents yet.")
        return
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            n_msgs = len(data)
        except Exception:
            n_msgs = "?"
        print(f"  {f.stem:<20} ({n_msgs} messages)  {f}")


def run_parallel(tasks: dict, api_key: str):
    """tasks: {agent_name: task_prompt}. Each agent gets its own thread and history."""
    def worker(name, prompt):
        agent = Agent(name, api_key)
        agent.run_task(prompt)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(worker, name, prompt) for name, prompt in tasks.items()]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()  # re-raise any exception


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "--list":
        list_agents()
        return

    if cmd == "--status":
        name = args[1] if len(args) > 1 else None
        if not name:
            list_agents()
            return
        f = SESSION_DIR / f"{name}.json"
        if not f.exists():
            print(f"[{name}] No session file yet.")
            return
        data = json.loads(f.read_text(encoding="utf-8"))
        print(f"[{name}] {len(data)} messages, file: {f}")
        return

    if cmd == "--reset":
        name = args[1] if len(args) > 1 else None
        if not name:
            print("Usage: --reset <agent_name>")
            sys.exit(1)
        api_key = get_api_key()
        Agent(name, api_key).reset_session()
        return

    if cmd == "--interactive":
        name = args[1] if len(args) > 1 else "default"
        api_key = get_api_key()
        agent = Agent(name, api_key)
        print(f"\n=== INTERACTIVE SESSION: {name} (type 'exit' to quit) ===\n")
        while True:
            try:
                user_inp = input(f"{name}> ").strip()
                if not user_inp:
                    continue
                if user_inp.lower() in ("exit", "quit"):
                    print(f"[{name}] Session saved.")
                    break
                agent.run_task(user_inp)
            except (KeyboardInterrupt, EOFError):
                print(f"\n[{name}] Session saved.")
                break
        return

    if cmd == "--parallel":
        # each remaining arg looks like:  agent_name="task prompt"
        api_key = get_api_key()
        tasks = {}
        for spec in args[1:]:
            if "=" not in spec:
                print(f"[SKIP] Malformed task spec (need name=\"task\"): {spec}")
                continue
            name, prompt = spec.split("=", 1)
            tasks[name.strip()] = prompt.strip().strip('"')
        if not tasks:
            print('Usage: --parallel agent1="task one" agent2="task two"')
            sys.exit(1)
        print(f"[ORCHESTRATOR] Launching {len(tasks)} agents in parallel: {', '.join(tasks)}")
        run_parallel(tasks, api_key)
        return

    if cmd == "run":
        remaining = list(args[1:])
        global MODEL_OVERRIDE
        if len(remaining) >= 2 and remaining[0] == "--model":
            MODEL_OVERRIDE = remaining[1]
            remaining = remaining[2:]
        if len(remaining) < 2:
            print('Usage: run [--model <model_name>] <agent_name> "<task prompt>"')
            sys.exit(1)
        name = remaining[0]
        task_prompt = " ".join(remaining[1:])
        api_key = get_api_key()
        if MODEL_OVERRIDE:
            log(name, f"[MODEL] Forced: {MODEL_OVERRIDE}")
        Agent(name, api_key).run_task(task_prompt)
        return

    print(__doc__)


if __name__ == "__main__":
    main()
