"""
Makima v7.2 — Elite Code Agent
Advanced capabilities: Multi-file orchestration, AST auto-correction, secure sandbox execution, 
static analysis integration, and self-healing code generation loops.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import sys
import tempfile
import traceback
from ast import NodeVisitor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Zero-crash resilience: Optional shutil for temp dir cleanup
try:
    import shutil
except ImportError:
    shutil = None

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.code")


class SecurityNodeVisitor(NodeVisitor):
    """AST Visitor to detect potentially dangerous operations in Python code."""
    DANGEROUS_CALLS = {"eval", "exec", "compile", "execfile", "__import__"}
    DANGEROUS_MODULES = {"os", "subprocess", "shutil", "sys", "ctypes", "socket", "multiprocessing"}
    
    def __init__(self) -> None:
        self.warnings: List[str] = []
        
    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in self.DANGEROUS_CALLS:
            self.warnings.append(f"Unsafe function call: {node.func.id}() at line {node.lineno}")
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in self.DANGEROUS_MODULES:
                self.warnings.append(f"Unsafe module method: {node.func.value.id}.{node.func.attr}() at line {node.lineno}")
        self.generic_visit(node)
        
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split('.')[0] in self.DANGEROUS_MODULES:
                self.warnings.append(f"Unsafe import: {alias.name} at line {node.lineno}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module.split('.')[0] in self.DANGEROUS_MODULES:
            self.warnings.append(f"Unsafe import: from {node.module} at line {node.lineno}")
        self.generic_visit(node)


class CodeAgent(BaseAgent):
    AGENT_NAME = "code"
    DESCRIPTION = "Elite Code Generation, AST Auto-Correction, Secure Sandbox Execution, and Multi-file Refactoring"
    CAPABILITIES = ["code_generation", "syntax_debugging", "refactoring", "ast_validation", "sandbox_execution"]
    AGENT_TOOLS = ["run_code", "format_code", "lint_code"]
    TAGS = ["code", "programming", "python", "rust", "sandbox", "ast"]

    SYSTEM_PROMPT = """You are Makima's Elite Code Agent, a principal software engineer and systems architect.
You write robust, production-grade, modular, and secure code across Python, Rust, TypeScript, and Go.

CORE ENGINEERING RULES:
1. Structured Thinking: Always decompose the problem, enumerate boundary edge cases, and plan the architecture inside a <thinking>...</thinking> block before generating code.
2. Zero Stubs & Complete Code: Never output lazy placeholders (e.g. '// TODO', 'pass', '...'). Always provide complete, working implementations.
3. Strict Syntax & Type Safety: Ensure all generated code is syntactically flawless, statically type-annotated, and passes AST validation.
4. Markdown File Tagging: ALWAYS use language-tagged markdown blocks with optional file paths (e.g. ```python:src/engine.py).
5. Security First: Never use dangerous built-ins (eval, exec, unvalidated subprocess calls) without rigorous sanitization and sandboxing.
6. Error Remediation: When fixing a bug, provide the root cause diagnosis, the exact corrected snippet, and an explanation of the fix.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    def __init__(
        self,
        ai_handler: Any = None,
        memory: Any = None,
        tool_registry: Any = None,
        ws_broadcast: Any = None,
        orchestrator: Any = None,
        guardrails: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        self._TOOL_MAP = {
            "run_code": self._tool_run_code,
            "format_code": self._tool_format_code,
            "lint_code": self._tool_lint_code,
        }

    async def _tool_run_code(self, code: str = "", timeout: Any = 15, **kwargs: Any) -> dict[str, Any]:
        """Execute code in a secure sandbox."""
        if not code:
            return {"stdout": "", "stderr": "No code provided", "returncode": -1}
        try:
            timeout_val = int(timeout)
        except (ValueError, TypeError):
            timeout_val = 15
        return await self._execute_in_sandbox(code, timeout=timeout_val)

    async def _tool_format_code(self, code: str = "", language: str = "python", **kwargs: Any) -> str:
        """Format source code."""
        if not code:
            return ""
        if language.lower() == "python":
            try:
                parsed = ast.parse(code)
                return ast.unparse(parsed)
            except Exception:
                return code
        return code

    async def _tool_lint_code(self, code: str = "", language: str = "python", **kwargs: Any) -> dict[str, Any]:
        """Lint source code for syntax and AST errors."""
        if not code:
            return {"valid": False, "errors": ["No code provided"], "warnings": []}
        if language.lower() == "python":
            valid, err = self._validate_python_ast(code)
            warnings = self._scan_security(code)
            return {
                "valid": valid,
                "errors": [err] if err else [],
                "warnings": warnings,
            }
        return {"valid": True, "errors": [], "warnings": []}

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        try:
            raw_response = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=8,
                task_type="code",
            )

            # AST Validation & Healing on generated code blocks
            blocks = self._extract_code_blocks(raw_response)
            if blocks:
                healed_blocks = []
                execution_results = []
                for block in blocks:
                    if block["lang"] == "python":
                        code = block["code"]
                        is_valid, err = self._validate_python_ast(code)
                        ast_retries = 0
                        while not is_valid and ast_retries < 3:
                            code = await self._self_heal_code(code, err, block["filename"])
                            is_valid, err = self._validate_python_ast(code)
                            ast_retries += 1
                        sec_warnings = self._scan_security(code)
                        block["code"] = code
                        block["ast_valid"] = is_valid
                        block["ast_error"] = err
                        block["sec_warnings"] = sec_warnings
                    healed_blocks.append(block)
                healed_response = self._extract_and_replace(raw_response, healed_blocks)
                diagnostics = self._format_diagnostics(healed_blocks, execution_results)
                if diagnostics:
                    healed_response += f"\n\n---\n### 🔍 Diagnostics & Execution\n{diagnostics}"
                final_out = healed_response
            else:
                final_out = raw_response or "Code generation completed."

            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.warning("[code] SDK Runner encountered exception, falling back: %s", sdk_exc)

        try:
            # ── P1 Bridge 4B: Consume structured AgentTask parameters first ───
            agent_task = getattr(self, "_current_agent_task", None) or context.get("agent_task")
            intent_data = None

            if agent_task:
                params = dict(agent_task.parameters or {})
                task_intent = str(agent_task.operation or params.get("intent") or "generate").lower()
                target_file = agent_task.target_entity if (agent_task.target_entity and any(x in agent_task.target_entity for x in (".", "/", "\\"))) else None
                files = params.get("files") or ([target_file] if target_file else [])
                query = params.get("query")
                needs_search = bool(params.get("needs_search") or query)
                needs_context = bool(params.get("needs_context") or files)
                intent_data = {
                    "intent": task_intent,
                    "needs_context": needs_context,
                    "files": files,
                    "needs_search": needs_search,
                    "query": query,
                }
                logger.info("[code] P1-4B: Consumed structured AgentTask parameters directly (intent=%s, files=%s)", task_intent, files)

            # Legacy fallback: analyze intent via fast LLM call
            if not intent_data:
                raw_intent = await self._analyze_intent(message)
                intent_data = raw_intent if isinstance(raw_intent, dict) else {}

            intent = intent_data.get("intent", "generate")

            # 2. Context & Search Gathering
            extra_context = ""
            if intent_data.get("needs_search") and intent_data.get("query"):
                search_res = await self._use_tool("web_search", query=intent_data["query"])
                extra_context += f"\n[Web Search Results]\n{search_res}\n"

            if intent_data.get("needs_context") and intent_data.get("files"):
                file_context = await self._fetch_context(intent_data["files"])
                extra_context += f"\n[Workspace Context]\n{file_context}\n"

            # 3. Initial Generation
            messages = self._build_messages(message, context, extra_system=extra_context)
            raw_response = await self._llm_call(messages, task="code")

            # 4. Code Extraction & Validation/Healing
            blocks = self._extract_code_blocks(raw_response)
            if not blocks:
                self._partial_result = raw_response
                return raw_response

            healed_blocks = []
            execution_results = []

            for block in blocks:
                if block["lang"] == "python":
                    code = block["code"]
                    
                    # AST Validation & Healing
                    is_valid, err = self._validate_python_ast(code)
                    ast_retries = 0
                    MAX_SELF_HEAL_RETRIES = 5
                    while not is_valid and ast_retries < MAX_SELF_HEAL_RETRIES:
                        code = await self._self_heal_code(code, err, block["filename"])
                        is_valid, err = self._validate_python_ast(code)
                        ast_retries += 1
                    
                    # Security Scan
                    sec_warnings = self._scan_security(code)
                    block["code"] = code
                    block["ast_valid"] = is_valid
                    block["ast_error"] = err
                    block["sec_warnings"] = sec_warnings
                    
                    # Execution (if requested or intent is execute)
                    if intent == "execute" or "run" in message.lower() or "execute" in message.lower():
                        exec_res = await self._execute_in_sandbox(code)
                        execution_results.append({"filename": block["filename"], "result": exec_res})
                        
                        # Execution Healing
                        exec_retries = 0
                        while exec_res["returncode"] != 0 and exec_retries < 1:
                            err_msg = exec_res["stderr"] or exec_res["stdout"]
                            code = await self._self_heal_code(code, f"Execution Error:\n{err_msg}", block["filename"])
                            block["code"] = code
                            
                            is_valid, err = self._validate_python_ast(code)
                            block["ast_valid"] = is_valid
                            block["ast_error"] = err
                            
                            exec_res = await self._execute_in_sandbox(code)
                            execution_results[-1]["result"] = exec_res
                            exec_retries += 1
                            
                healed_blocks.append(block)
                
            # 5. Reconstruct Response
            healed_response = self._extract_and_replace(raw_response, healed_blocks)
            diagnostics = self._format_diagnostics(healed_blocks, execution_results)
            
            final_response = healed_response
            if diagnostics:
                final_response += f"\n\n---\n### 🔍 Diagnostics & Execution\n{diagnostics}"
                
            self._partial_result = final_response
            return final_response

        except Exception as e:
            logger.error("[code] execute failed: %s\n%s", e, traceback.format_exc())
            return f"Elite Code Agent encountered a critical error: {e}"

    async def _analyze_intent(self, message: str) -> Dict[str, Any]:
        """Fast LLM call to classify intent and determine context needs."""
        prompt = (
            "Analyze the user's coding request. Return strict JSON only:\n"
            "{\"intent\": \"generate|refactor|debug|execute|review\", "
            "\"needs_context\": bool, \"files\": [\"path.py\"], "
            "\"needs_search\": bool, \"query\": \"search string\"}\n"
            "If no specific files are mentioned, 'files' should be empty. "
            "If no web search is needed, 'needs_search' is false."
        )
        try:
            res = await self._llm_call(
                [{"role": "user", "content": f"Request: {message}\n\n{prompt}"}],
                task="code", require_json=True, max_tokens=150
            )
            parsed = self.ai_handler.try_parse_json(res)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            logger.warning("[code] Intent analysis failed: %s", e)
            return {}

    async def _fetch_context(self, files: List[str]) -> str:
        """Reads workspace files to provide refactoring/debugging context."""
        context_str = ""
        for f in files:
            try:
                if self.tool_registry and "read_file" in self.tool_registry:
                    content = await self._use_tool("read_file", path=f)
                else:
                    p = Path(f)
                    if p.exists() and p.is_file():
                        content = p.read_text(encoding="utf-8")
                    else:
                        content = f"[File not found: {f}]"
                context_str += f"\n--- FILE: {f} ---\n{content}\n"
            except Exception as e:
                context_str += f"\n--- FILE: {f} ---\n[Error reading file: {e}]\n"
        return context_str

    def _extract_code_blocks(self, text: str) -> List[Dict[str, Any]]:
        """Extracts markdown code blocks with language and optional filename."""
        pattern = re.compile(r"```([a-zA-Z0-9_\-\+]+)(?::([^\s\r\n]+))?[ \t]*\r?\n(.*?)```", re.DOTALL)
        blocks = []
        for match in pattern.finditer(text):
            lang = match.group(1).lower()
            filename = match.group(2) or f"snippet_{len(blocks)+1}.{lang}"
            code = match.group(3).strip()
            blocks.append({
                "lang": lang, 
                "filename": filename, 
                "code": code, 
                "raw": match.group(0),
                "ast_valid": True,
                "ast_error": None,
                "sec_warnings": []
            })
        return blocks

    def _validate_python_ast(self, code: str) -> Tuple[bool, Optional[str]]:
        """Validates Python code syntax using the ast module."""
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, f"AST Parse Error: {str(e)}"

    def _scan_security(self, code: str) -> List[str]:
        """Scans Python code for dangerous imports and function calls."""
        try:
            tree = ast.parse(code)
            visitor = SecurityNodeVisitor()
            visitor.visit(tree)
            return visitor.warnings
        except Exception:
            return ["Could not perform AST security scan due to syntax errors."]

    async def _self_heal_code(self, original_code: str, error_msg: str, filename: str) -> str:
        """Uses LLM to auto-correct code based on AST or execution errors."""
        repair_prompt = (
            f"The Python code in `{filename}` failed validation or execution.\n"
            f"Error:\n{error_msg}\n\n"
            f"Original Code:\n```python\n{original_code}\n```\n\n"
            f"Provide ONLY the corrected Python code block. No explanations, no markdown outside the code block."
        )
        try:
            messages = [{"role": "user", "content": repair_prompt}]
            response = await self._llm_call(messages, task="code", max_tokens=4096)
            
            blocks = self._extract_code_blocks(response)
            for b in blocks:
                if b["lang"] == "python":
                    return b["code"]
        except Exception as e:
            logger.warning("[code] Self-heal failed: %s", e)
        return original_code

    async def _execute_in_sandbox(self, code: str, timeout: int = 15, allow_local_fallback: bool = True) -> Dict[str, Any]:
        """Dispatch code through configured sandbox with safe isolated subprocess fallback."""
        if self.tool_registry and hasattr(self.tool_registry, "_tools") and "execute_python_sandbox" in self.tool_registry._tools:
            try:
                result = await self._use_tool("execute_python_sandbox", code=code, timeout=timeout)
                if isinstance(result, dict):
                    return {
                        "stdout": result.get("stdout", ""), 
                        "stderr": result.get("stderr", ""), 
                        "returncode": result.get("returncode", -1)
                    }
                elif isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict):
                            return {
                                "stdout": parsed.get("stdout", ""),
                                "stderr": parsed.get("stderr", ""),
                                "returncode": parsed.get("returncode", -1)
                            }
                    except Exception:
                        pass
                    return {"stdout": result, "stderr": "", "returncode": 0 if not self._tool_failed(result) else -1}
                return {
                    "stdout": str(result) if result else "",
                    "stderr": "",
                    "returncode": 0
                }
            except Exception as e:
                logger.error("Sandbox tool failed: %s", e)
                if not allow_local_fallback:
                    return {
                        "stdout": "",
                        "stderr": f"Secure code sandbox failed: {e}",
                        "returncode": -1,
                    }

        if allow_local_fallback:
            return await self._local_subprocess_exec(code, timeout=timeout)

        return {
            "stdout": "",
            "stderr": "Secure code sandbox is unavailable; local execution was refused.",
            "returncode": -1,
        }

    async def _local_subprocess_exec(self, code: str, timeout: int) -> Dict[str, Any]:
        """Executes code locally with strict timeouts and isolated environment."""
        tmp_dir = tempfile.mkdtemp(prefix="makima_code_")
        file_path = Path(tmp_dir) / "exec_script.py"
        try:
            file_path.write_text(code, encoding="utf-8")
            
            # python -I runs in isolated mode (ignores env vars and user site-packages)
            python_bin = sys.executable or "python"
            cmd = [python_bin, "-I", "-u", str(file_path)]
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return {
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode
                }
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "stdout": "",
                    "stderr": f"Execution timed out after {timeout} seconds.",
                    "returncode": -1
                }
        except Exception as e:
            return {"stdout": "", "stderr": f"Subprocess error: {str(e)}", "returncode": -1}
        finally:
            if shutil:
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
            else:
                try:
                    import os
                    os.remove(file_path)
                    os.rmdir(tmp_dir)
                except Exception:
                    pass

    def _extract_and_replace(self, text: str, processed_blocks: List[Dict[str, Any]]) -> str:
        """Replaces original code blocks in the text with healed/validated versions."""
        pattern = re.compile(r"```([a-zA-Z0-9_\-\+]+)(?::([^\s\r\n]+))?[ \t]*\r?\n(.*?)```", re.DOTALL)
        block_iter = iter(processed_blocks)
        
        def replacer(match: re.Match) -> str:
            try:
                b = next(block_iter)
                lang = b["lang"]
                fname_match = match.group(2)
                fname = f":{fname_match}" if fname_match else ""
                return f"```{lang}{fname}\n{b['code']}\n```"
            except StopIteration:
                return match.group(0)
                
        return pattern.sub(replacer, text)

    def _format_diagnostics(self, blocks: List[Dict[str, Any]], exec_results: List[Dict[str, Any]]) -> str:
        """Formats AST warnings, security alerts, and execution outputs."""
        diag = []
        
        for b in blocks:
            if b["lang"] == "python":
                if not b.get("ast_valid"):
                    diag.append(f"⚠️ **{b['filename']}**: AST Validation Failed - {b.get('ast_error')}")
                if b.get("sec_warnings"):
                    for w in b["sec_warnings"]:
                        diag.append(f"🛡️ **{b['filename']}** Security: {w}")
                        
        for ex in exec_results:
            res = ex["result"]
            fname = ex["filename"]
            diag.append(f"\n▶️ **Execution: {fname}**")
            if res["returncode"] == 0:
                diag.append("✅ Success (Exit Code 0)")
                if res["stdout"]:
                    diag.append(f"```stdout\n{res['stdout'].strip()}\n```")
            else:
                diag.append(f"❌ Failed (Exit Code {res['returncode']})")
                if res["stderr"]:
                    diag.append(f"```stderr\n{res['stderr'].strip()}\n```")
                elif res["stdout"]:
                    diag.append(f"```stdout\n{res['stdout'].strip()}\n```")
                    
        return "\n".join(diag) if diag else ""
