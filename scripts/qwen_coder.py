#!/usr/bin/env python3
"""
Makima OS — Qwen 3.7 Max Code Generator CLI
Uses Alibaba Cloud DashScope OpenAI-compatible API with enable_thinking=True
to generate high-performance code for Makima OS.
"""

import json
import os
import sys
import urllib.request
import urllib.error

DASHSCOPE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

SYSTEM_PROMPT = """You are an Expert Systems Architect & Python Developer building Makima OS v7.2.
When generating code for Makima:
1. Follow the BaseAgent contract (async def execute(self, task_id, message, context, entities)).
2. Implement zero-crash resilience (try/except imports with graceful fallbacks).
3. Enforce high-performance async design and clean type annotations."""

AGENT_SYSTEM_PROMPT = """You are an Autonomous AI Agent Coder for Makima OS.
You can inspect, read, search, and edit files in the workspace autonomously.
To execute a tool, include one or more of the following commands on new lines in your response:
1. Read a file:
<<READ_FILE: relative/path/to/file.py>>
2. List directory contents:
<<LIST_DIR: relative/path>>
3. Search for pattern across files:
<<GREP: pattern>>
4. Create or overwrite a file (with header syntax):
```python:relative/path/to/file.py
# full file code
```
5. When your task is complete and verified:
<<DONE>>
Always explain your reasoning before using tools. Never ask the user to read or write files for you—do it yourself."""

def run_agent_loop(api_key, prompt):
    import glob, re, py_compile
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
    max_turns = 12
    MODELS_FALLBACK_LIST = [
        "qwen3.7-max-2026-05-17",
        "qwen3.7-max-2026-05-20",
        "kimi-k2.7-code",
        "qwen-coder-plus",
        "qwen-plus",
        "qwen-max",
        "qwen3.7-max-2026-06-08",
        "qwen3-coder-plus"
    ]
    for turn in range(1, max_turns + 1):
        print(f"\n========================================================")
        print(f"  MAKIMA AUTONOMOUS AGENT LOOP — TURN {turn}/{max_turns}")
        print(f"========================================================\n")
        full_content = ""
        for model_idx, current_model in enumerate(MODELS_FALLBACK_LIST):
            try:
                payload = {
                    "model": current_model,
                    "messages": messages,
                    "stream": True,
                    "enable_thinking": True
                }
                req = urllib.request.Request(
                    DASHSCOPE_URL,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
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
                                        print("[--- QWEN THINKING PROCESS ---]\n", flush=True)
                                        in_thinking = True
                                    print(reasoning, end="", flush=True)
                                if content:
                                    full_content += content
                                    if in_thinking and not in_content:
                                        print("\n\n[--- QWEN ANSWER & TOOL COMMANDS ---]\n", flush=True)
                                        in_content = True
                                    print(content, end="", flush=True)
                break
            except Exception as e:
                print(f"\n[FALLBACK] {current_model} failed ({e}). Trying next model...")
                if model_idx == len(MODELS_FALLBACK_LIST) - 1:
                    print("\n[ERROR] All fallback models exhausted in Agent Loop!")
                    return
        print("\n\n[TURN COMPLETED]")
        if "<<DONE>>" in full_content:
            print("\n[AGENT COMPLETED] Autonomous task successfully finished!")
            break
        tool_results = ""
        # 1. Handle <<READ_FILE: path>>
        for filepath in re.findall(r"<<READ_FILE:\s*([^>]+)>>", full_content):
            filepath = filepath.strip()
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    tool_results += f"\n--- READ_FILE RESULT: {filepath} ---\n{f.read()[:6000]}\n"
                print(f"[AGENT TOOL] Read file: {filepath}")
            except Exception as e:
                tool_results += f"\n--- READ_FILE ERROR: {filepath}: {e} ---\n"
                print(f"[AGENT TOOL ERROR] Read file: {filepath} ({e})")
        # 2. Handle <<LIST_DIR: path>>
        for dirpath in re.findall(r"<<LIST_DIR:\s*([^>]+)>>", full_content):
            dirpath = dirpath.strip()
            try:
                items = os.listdir(dirpath if dirpath else ".")
                tool_results += f"\n--- LIST_DIR RESULT: {dirpath} ---\n" + "\n".join(items[:50]) + "\n"
                print(f"[AGENT TOOL] Listed directory: {dirpath}")
            except Exception as e:
                tool_results += f"\n--- LIST_DIR ERROR: {dirpath}: {e} ---\n"
        # 3. Handle <<GREP: pattern>>
        for pattern in re.findall(r"<<GREP:\s*([^>]+)>>", full_content):
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
                print(f"[AGENT TOOL] Grep searched pattern: '{pattern}'")
            except Exception as e:
                tool_results += f"\n--- GREP ERROR: {pattern}: {e} ---\n"
        # 4. Handle auto-write ```python:filepath ...
        matches = re.findall(r"```(?:python)?:([a-zA-Z0-9_\-\./\\]+\.py)\s*\n(.*?)\n```", full_content, re.DOTALL)
        if not matches:
            matches = re.findall(r"(?:#\s*filepath:\s*|#\s*file:\s*)([a-zA-Z0-9_\-\./\\]+\.py)\s*\n(?:```(?:python)?\s*\n)?(.*?)(?:\n```|$)", full_content, re.DOTALL)
        for filepath, code_block in matches:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(code_block.strip() + "\n")
                print(f"[AGENT TOOL] Wrote file: {filepath}")
                py_compile.compile(filepath, doraise=True)
                tool_results += f"\n--- WRITE_FILE SUCCESS & SYNTAX VERIFIED: {filepath} ---\n"
                print(f"[AGENT TOOL VERIFIED] Syntax compiler check PASSED for {filepath}!")
            except Exception as e:
                tool_results += f"\n--- WRITE_FILE SYNTAX ERROR: {filepath}: {e} ---\n"
                print(f"[AGENT TOOL ERROR] Syntax error in {filepath}: {e}")

        if not tool_results:
            print("\n[INFO] No tools executed by Agent. Ending loop.")
            break
        messages.append({"role": "assistant", "content": full_content})
        messages.append({"role": "user", "content": f"Tool Execution Results:\n{tool_results}"})


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    api_key = os.getenv(
        "DASHSCOPE_API_KEY",
        "sk-ws-H.XEXYYX.2ebM.MEUCIHvVzZqu27dFAuj2YzCAulR0Vckqmt9RYgi4iE5w4AiVAiEAsAqKZT6q5pXNGylMZ8Rtgg6CQ_KfxtvI5rf22zC9ZyQ"
    )
    if not api_key:
        print("[ERROR] DASHSCOPE_API_KEY environment variable is not set.")
        sys.exit(1)

    args = sys.argv[1:]
    if not args:
        print("Usage: python scripts/qwen_coder.py [--agent] [--all-agents] [-f file.py ...] [-o out.py | --auto-write] <prompt>")
        sys.exit(1)

    import glob
    import re
    import py_compile

    file_contents = ""
    prompt_words = []
    output_file = None
    auto_write = False
    is_agent = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--agent", "-a"):
            is_agent = True
            i += 1
        elif arg == "--all-agents":
            print("[INFO] Loading all Makima agent files automatically...")
            for filepath in sorted(glob.glob("apps/brain/agents/*.py")):
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        file_contents += f"\n--- FILE: {filepath} ---\n{f.read()}\n"
                except Exception as e:
                    print(f"[WARN] Could not read {filepath}: {e}")
            i += 1
        elif arg in ("-f", "--files", "--file"):
            i += 1
            while i < len(args) and not args[i].startswith("-") and os.path.exists(args[i]):
                filepath = args[i]
                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        file_contents += f"\n--- FILE: {filepath} ---\n{f.read()}\n"
                    print(f"[INFO] Included file: {filepath}")
                except Exception as e:
                    print(f"[WARN] Could not read {filepath}: {e}")
                i += 1
        elif arg in ("-o", "--out", "--output"):
            i += 1
            if i < len(args):
                output_file = args[i]
            i += 1
        elif arg == "--auto-write":
            auto_write = True
            i += 1
        else:
            prompt_words.append(arg)
            i += 1

    user_prompt = " ".join(prompt_words)
    if is_agent:
        run_agent_loop(api_key, user_prompt)
        return
    if file_contents:
        user_prompt += f"\n\nHERE ARE THE LOCAL CODEBASE FILES FOR YOUR ANALYSIS AND UPGRADE:\n{file_contents}"
    if output_file:
        user_prompt += f"\n\nCRITICAL INSTRUCTION: Enclose your complete Python code in a single markdown block like ```python\n# code\n``` so it can be saved to {output_file}."
    elif auto_write:
        user_prompt += "\n\nCRITICAL INSTRUCTION: For each file you write, use markdown code block with header like ```python:apps/brain/agents/filename.py\n# code\n```."

    MODELS_FALLBACK_LIST = [
        "qwen3.7-max-2026-05-17",
        "qwen3.7-max-2026-05-20",
        "kimi-k2.7-code",
        "qwen-coder-plus",
        "qwen-plus",
        "qwen-max",
        "qwen3.7-max-2026-06-08",
        "qwen3-coder-plus"
    ]

    for model_idx, current_model in enumerate(MODELS_FALLBACK_LIST):
        print(f"[QWEN] Querying {current_model} (DashScope) for Makima OS... (Attempt {model_idx+1}/{len(MODELS_FALLBACK_LIST)})\n")

        payload = {
            "model": current_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            "stream": True,
            "enable_thinking": True
        }

        req = urllib.request.Request(
            DASHSCOPE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream"
            },
            method="POST"
        )

        in_thinking = False
        in_content = False
        full_content = ""

        try:
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
                                    print("[--- QWEN THINKING PROCESS ---]\n", flush=True)
                                    in_thinking = True
                                print(reasoning, end="", flush=True)

                            if content:
                                full_content += content
                                if in_thinking and not in_content:
                                    print("\n\n[--- QWEN FINAL REPLY & CODE ---]\n", flush=True)
                                    in_content = True
                                elif not in_thinking and not in_content:
                                    print("[--- QWEN FINAL REPLY & CODE ---]\n", flush=True)
                                    in_content = True
                                print(content, end="", flush=True)
            print("\n\n[DONE] Finished generating")

            # Save directly to disk if requested
            if output_file:
                match = re.search(r"```(?:python)?\s*\n(.*?)\n```", full_content, re.DOTALL | re.IGNORECASE)
                code_to_save = match.group(1) if match else full_content.strip()
                os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(code_to_save + "\n")
                print(f"[SUCCESS] Wrote generated code directly to: {output_file}")
                try:
                    py_compile.compile(output_file, doraise=True)
                    print(f"[VERIFIED] Python syntax compiler check PASSED for {output_file}!")
                except Exception as e:
                    print(f"[WARN] Syntax error in generated file {output_file}: {e}")

            elif auto_write:
                matches = re.findall(r"```(?:python)?:([a-zA-Z0-9_\-\./\\]+\.py)\s*\n(.*?)\n```", full_content, re.DOTALL)
                if not matches:
                    matches = re.findall(r"(?:#\s*filepath:\s*|#\s*file:\s*)([a-zA-Z0-9_\-\./\\]+\.py)\s*\n(?:```(?:python)?\s*\n)?(.*?)(?:\n```|$)", full_content, re.DOTALL)
                for filepath, code_block in matches:
                    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(code_block.strip() + "\n")
                    print(f"[SUCCESS] Wrote file: {filepath}")
                    try:
                        py_compile.compile(filepath, doraise=True)
                        print(f"[VERIFIED] Syntax check PASSED for {filepath}!")
                    except Exception as e:
                        print(f"[WARN] Syntax check failed for {filepath}: {e}")

            # If we reached here successfully, break out of fallback loop
            break

        except urllib.error.HTTPError as e:
            err_text = e.read().decode('utf-8', errors='ignore')
            print(f"\n[FALLBACK] Model '{current_model}' threw HTTP {e.code}: {err_text[:120]}... Trying next fallback model...")
            if model_idx == len(MODELS_FALLBACK_LIST) - 1:
                print("\n[ERROR] All fallback models exhausted!")
                raise
        except Exception as e:
            print(f"\n[FALLBACK] Model '{current_model}' threw error: {e}. Trying next fallback model...")
            if model_idx == len(MODELS_FALLBACK_LIST) - 1:
                print("\n[ERROR] All fallback models exhausted!")
                raise


if __name__ == "__main__":
    main()

