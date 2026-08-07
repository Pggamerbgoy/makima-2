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
import tempfile
import traceback
from ast import NodeVisitor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Zero-crash resilience: Optional high-performance event loop
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

# Zero-crash resilience: Optional shutil for temp dir cleanup
try:
    import shutil
except ImportError:
    shutil = None

from .base_agent import BaseAgent

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

    SYSTEM_PROMPT = """You are Makima's Elite Code Agent, an expert software engineer.
Capabilities:
- Generate production-ready, highly optimized, and secure code.
- Refactor existing codebases with multi-file precision.
- Debug complex tracebacks and resolve architectural flaws.
- Execute code securely and interpret the results.

Strict Rules:
1. ALWAYS use markdown code blocks with language tags (e.g., ```python).
2. For multi-file outputs, specify the filename in the tag (e.g., ```python:src/main.py).
3. Ensure all Python code is syntactically valid and passes AST parsing.
4. Never use dangerous functions (eval, exec, os.system) unless explicitly requested for a specific secure sandbox context.
5. Provide brief, high-signal architectural explanations. Avoid fluff.
6. If fixing an error, output ONLY the corrected code blocks and a 1-sentence summary of the fix.
"""

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        self._reset_state()
        try:
            # 1. Intent Analysis
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
                    while not is_valid and ast_retries < 2:
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
        pattern = re.compile(r"```(\w+)(?::([^\s\n]+))?\n(.*?)```", re.DOTALL)
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

    async def _execute_in_sandbox(self, code: str, timeout: int = 15) -> Dict[str, Any]:
        """Dispatches code to a secure sandbox or falls back to restricted local execution."""
        if self.tool_registry and hasattr(self.tool_registry, "_tools") and "execute_python_sandbox" in self.tool_registry._tools:
            try:
                result = await self._use_tool("execute_python_sandbox", code=code, timeout=timeout)
                return {
                    "stdout": result.get("stdout", ""), 
                    "stderr": result.get("stderr", ""), 
                    "returncode": result.get("returncode", -1)
                }
            except Exception as e:
                logger.warning("Sandbox tool failed, falling back to local restricted execution: %s", e)

        return await self._local_subprocess_exec(code, timeout)

    async def _local_subprocess_exec(self, code: str, timeout: int) -> Dict[str, Any]:
        """Executes code locally with strict timeouts and isolated environment."""
        tmp_dir = tempfile.mkdtemp(prefix="makima_code_")
        file_path = Path(tmp_dir) / "exec_script.py"
        try:
            file_path.write_text(code, encoding="utf-8")
            
            # python -I runs in isolated mode (ignores env vars and user site-packages)
            cmd = ["python", "-I", "-u", str(file_path)]
            
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
        pattern = re.compile(r"```(\w+)(?::([^\s\n]+))?\n(.*?)```", re.DOTALL)
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
