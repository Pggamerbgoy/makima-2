#!/usr/bin/env python3
"""
Makima OS v7.2 — Qwen Stateful Session Runner
Location: scripts/qwen_session.py

Features:
- Maintains persistent conversation history across multiple tasks (~/.makima/qwen_session.json).
- Supports interactive multi-turn session mode (--interactive) or single-command chained tasks.
- Autonomously inspects, greps, reads, and writes code files with syntax verification (py_compile).
- Built-in session control (--reset, --status, --history).
"""

import glob
import json
import os
import py_compile
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DASHSCOPE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
SESSION_FILE = Path.home() / ".makima" / "qwen_session.json"

AGENT_SYSTEM_PROMPT = """You are an Autonomous AI Agent Coder & Systems Architect for Makima OS.
You maintain full stateful context across multiple tasks in this active development session.

When assigned a task:
1. Inspect the codebase, read relevant files, and search for patterns autonomously.
2. Formulate a precise engineering plan and implement all necessary code changes.
3. To execute a tool, output one of these commands on a new line:
   - Read file: <<READ_FILE: relative/path/to/file.py>>
   - List directory: <<LIST_DIR: relative/path>>
   - Grep search: <<GREP: pattern>>
   - Create or overwrite code:
     ```python:relative/path/to/file.py
     # complete code
     ```
4. When your task is finished and verified, output:
   <<DONE>>

Always verify syntax after editing. Work autonomously and never ask the user to manually edit code."""

MODELS_FALLBACK_LIST = [
    "qwen3.7-flash-2026-07-15",
    "qwen3.6-plus-2026-04-02",
    "qwen3.7-plus-2026-05-26",
    "qwen3.7-max-preview",
    "qwen3.7-max-2026-05-20",
    "kimi-k2.7-code",
    "qwen-coder-plus",
    "qwen-plus",
    "qwen-max",
    "qwen3.7-max-2026-06-08",
    "qwen3-coder-plus"
]


class QwenSession:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.history_file = SESSION_FILE
        self.messages = self._load_session()

    def _load_session(self) -> list[dict]:
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0 and data[0].get("role") == "system":
                        return data
            except Exception as e:
                print(f"[SESSION WARN] Could not load session file ({e}); starting fresh.")
        return [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]

    def save_session(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[SESSION WARN] Failed to save session state: {e}")

    def reset_session(self):
        self.messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        if self.history_file.exists():
            try:
                os.remove(self.history_file)
            except Exception:
                pass
        print("[SESSION RESET] Qwen session history cleared.")

    def run_task(self, task_prompt: str, max_turns: int = 20):
        print(f"\n========================================================")
        print(f"  MAKIMA QWEN SESSION — TASK EXECUTION")
        print(f"========================================================\n")
        print(f"TASK: {task_prompt}\n")

        self.messages.append({"role": "user", "content": task_prompt})

        for turn in range(1, max_turns + 1):
            print(f"\n--- SESSION TURN {turn}/{max_turns} ---")
            full_content = ""
            for model_idx, current_model in enumerate(MODELS_FALLBACK_LIST):
                try:
                    payload = {
                        "model": current_model,
                        "messages": self.messages,
                        "stream": True,
                        "enable_thinking": True
                    }
                    req = urllib.request.Request(
                        DASHSCOPE_URL,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream"
                        },
                        method="POST"
                    )
                    in_thinking = False
                    in_content = False
                    with urllib.request.urlopen(req) as resp:
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
                                        if not in_thinking:
                                            print("[--- QWEN THINKING ---]\n", flush=True)
                                            in_thinking = True
                                        print(reasoning, end="", flush=True)
                                    if content:
                                        full_content += content
                                        if in_thinking and not in_content:
                                            print("\n\n[--- QWEN ANSWER & ACTIONS ---]\n", flush=True)
                                            in_content = True
                                        print(content, end="", flush=True)
                    break
                except Exception as e:
                    print(f"\n[FALLBACK] {current_model} failed ({e}). Retrying with next model...")
                    if model_idx == len(MODELS_FALLBACK_LIST) - 1:
                        print("\n[ERROR] All fallback models exhausted in session!")
                        self.save_session()
                        return

            print("\n\n[TURN COMPLETED]")
            self.messages.append({"role": "assistant", "content": full_content})
            self.save_session()

            if "<<DONE>>" in full_content or "<DONE>" in full_content:
                print("\n[SESSION TASK FINISHED] Task completed successfully!")
                break

            tool_results = ""
            # 1. READ_FILE (Support both <<READ_FILE: path>> and functions.ReadFile JSON format)
            read_files = re.findall(r"<<?READ_FILE:\s*([^>]+)>>?", full_content)
            for path_match in re.findall(r"ReadFile.*?(?:<\|tool_call_argument_begin\|>|\(\{?)\s*(\{\s*\"path\".*?\})", full_content, re.DOTALL):
                try:
                    p = json.loads(path_match).get("path")
                    if p and p not in read_files: read_files.append(p)
                except Exception: pass
            for filepath in read_files:
                filepath = filepath.strip()
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        tool_results += f"\n--- READ_FILE RESULT: {filepath} ---\n{f.read()[:100000]}\n"
                    print(f"[TOOL] Read file: {filepath}")
                except Exception as e:
                    tool_results += f"\n--- READ_FILE ERROR: {filepath}: {e} ---\n"
                    print(f"[TOOL ERROR] Read file {filepath}: {e}")

            # 2. LIST_DIR
            for dirpath in re.findall(r"<<?LIST_DIR:\s*([^>]+)>>?", full_content):
                dirpath = dirpath.strip()
                try:
                    items = os.listdir(dirpath if dirpath else ".")
                    tool_results += f"\n--- LIST_DIR RESULT: {dirpath} ---\n" + "\n".join(items[:50]) + "\n"
                    print(f"[TOOL] Listed dir: {dirpath}")
                except Exception as e:
                    tool_results += f"\n--- LIST_DIR ERROR: {dirpath}: {e} ---\n"

            # 3. GREP
            for pattern in re.findall(r"<<?GREP:\s*([^>]+)>>?", full_content):
                pattern = pattern.strip()
                try:
                    matches = []
                    for root, _, files in os.walk("apps"):
                        for file in files:
                            if file.endswith(".py"):
                                fpath = os.path.join(root, file)
                                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                    for lno, line in enumerate(f, 1):
                                        if pattern.lower() in line.lower():
                                            matches.append(f"{fpath}:{lno}: {line.strip()}")
                    tool_results += f"\n--- GREP RESULT: '{pattern}' ---\n" + "\n".join(matches[:25]) + "\n"
                    print(f"[TOOL] Grep: '{pattern}'")
                except Exception as e:
                    tool_results += f"\n--- GREP ERROR: {pattern}: {e} ---\n"

            # 4. Auto-write code blocks
            code_matches = re.findall(r"```(?:python)?:([a-zA-Z0-9_\-\./\\]+\.py)\s*\n(.*?)\n```", full_content, re.DOTALL)
            if not code_matches:
                code_matches = re.findall(r"(?:#\s*filepath:\s*|#\s*file:\s*)([a-zA-Z0-9_\-\./\\]+\.py)\s*\n(?:```(?:python)?\s*\n)?(.*?)(?:\n```|$)", full_content, re.DOTALL)
            for filepath, code_block in code_matches:
                os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(code_block.strip() + "\n")
                    print(f"[TOOL WRITE] Saved: {filepath}")
                    py_compile.compile(filepath, doraise=True)
                    tool_results += f"\n--- WRITE_FILE SUCCESS & SYNTAX VERIFIED: {filepath} ---\n"
                    print(f"[TOOL VERIFIED] Syntax compiler check PASSED for {filepath}!")
                except Exception as e:
                    tool_results += f"\n--- WRITE_FILE SYNTAX ERROR: {filepath}: {e} ---\n"
                    print(f"[TOOL ERROR] Syntax error in {filepath}: {e}")

            if not tool_results:
                print("\n[INFO] No further tools executed. Ending turn loop.")
                break

            self.messages.append({"role": "user", "content": f"Tool Execution Results:\n{tool_results}"})
            self.save_session()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("[ERROR] DASHSCOPE_API_KEY environment variable is not set.")
        sys.exit(1)

    session = QwenSession(api_key=api_key)

    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python scripts/qwen_session.py \"<task prompt>\"       # Run a task in the active session")
        print("  python scripts/qwen_session.py --interactive        # Interactive shell session mode")
        print("  python scripts/qwen_session.py --reset              # Reset active session history")
        print("  python scripts/qwen_session.py --status             # Show active session info")
        sys.exit(0)

    cmd = args[0]
    if cmd == "--reset":
        session.reset_session()
        sys.exit(0)
    elif cmd == "--status":
        print(f"[SESSION STATUS] Active history messages: {len(session.messages)}")
        print(f"[SESSION FILE] {SESSION_FILE}")
        sys.exit(0)
    elif cmd == "--interactive":
        print("\n========================================================")
        print("  MAKIMA QWEN INTERACTIVE SESSION MODE")
        print("  Type 'exit' or 'quit' to end session.")
        print("========================================================\n")
        while True:
            try:
                user_inp = input("\nqwen-session> ").strip()
                if not user_inp:
                    continue
                if user_inp.lower() in ("exit", "quit"):
                    print("[SESSION PAUSED] Session history saved.")
                    break
                session.run_task(user_inp)
            except (KeyboardInterrupt, EOFError):
                print("\n[SESSION PAUSED] Saved.")
                break
    else:
        task_prompt = " ".join(args)
        session.run_task(task_prompt)


if __name__ == "__main__":
    main()
