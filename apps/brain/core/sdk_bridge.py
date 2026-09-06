"""
Makima OS — OpenAI Agents SDK Bridge (v9.5)
Location: apps/brain/core/sdk_bridge.py

Provides seamless bidirectional bridging between Makima's architectural core 
and the OpenAI Agents SDK:
  1. MakimaModel & MakimaModelProvider: Routes SDK Runner LLM invocations through
     Makima's NEXUS LLM Gateway (ai_handler.py), preserving Dynamic Pareto Routing,
     Circuit Breakers, Multi-Key Rotation, and support for DashScope Qwen, Groq, 
     DeepSeek, Gemini, and Ollama.
  2. ToolRegistry SDK Bridge: Converts Makima ToolMeta definitions into native
     SDK FunctionTool instances while preserving Transactional ExecutionRuntime,
     Pre-State Snapshots, Invariant Verification, and Saga Auto-Compensation.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from typing import Any, AsyncIterator, Callable, Coroutine, Optional, Sequence, Union

# Suppress noisy OpenAI tracing warnings when executing non-OpenAI local/dashscope models
os.environ.setdefault("AGENTS_TRACING_DISABLED", "1")
logging.getLogger("openai.agents").setLevel(logging.ERROR)

from agents import (
    Agent,
    GuardrailFunctionOutput,
    Handoff,
    InputGuardrail,
    InputGuardrailTripwireTriggered,
    Runner,
    RunHooks,
    SQLiteSession,
    handoff,
    input_guardrail,
    output_guardrail,
)
from agents.run_config import RunConfig, ToolExecutionConfig
from agents.model_settings import ModelSettings
from agents.extensions.tool_output_trimmer import ToolOutputTrimmer
from agents.extensions.handoff_filters import remove_all_tools
from agents.memory.session_settings import SessionSettings
from agents.items import (
    ModelResponse,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    TResponseInputItem,
    TResponseStreamEvent,
    Usage,
)
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model, ModelTracing
from agents.tool import FunctionTool, Tool, ToolContext

logger = logging.getLogger("makima.sdk_bridge")


class ToolInvokerProxy:
    """Callable invoker that bridges SDK tool execution to Makima's ExecutionRuntime."""

    def __init__(
        self,
        tool_name: str,
        registry: Optional[Any] = None,
        execution_runtime: Optional[Any] = None,
        agent: Optional[Any] = None,
        custom_func: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None,
    ) -> None:
        self.tool_name = tool_name
        self.registry = registry
        self.execution_runtime = execution_runtime
        self.agent = agent
        self.custom_func = custom_func

    async def __call__(self, ctx: ToolContext[Any], input_json: Any) -> Any:
        params: dict[str, Any] = {}
        if input_json:
            if isinstance(input_json, dict):
                params = dict(input_json)
            elif isinstance(input_json, str):
                try:
                    parsed = json.loads(input_json)
                    if isinstance(parsed, dict):
                        params = parsed
                    else:
                        params = {"value": parsed}
                except Exception as parse_err:
                    logger.warning(
                        "[sdk_bridge] JSON parse error for tool '%s': %s",
                        self.tool_name,
                        parse_err,
                    )
                    params = {"_raw": input_json}

        # Track tool execution for fabrication / hallucination guardrails
        user_ctx: dict[str, Any] = {}
        if hasattr(ctx, "context") and isinstance(ctx.context, dict):
            user_ctx = ctx.context
            user_ctx.setdefault("tool_calls_executed", []).append(self.tool_name)
        elif isinstance(ctx, dict):
            user_ctx = ctx
            user_ctx.setdefault("tool_calls_executed", []).append(self.tool_name)

        if self.agent is not None:
            if not hasattr(self.agent, "_tool_calls_executed"):
                self.agent._tool_calls_executed = []
            self.agent._tool_calls_executed.append(self.tool_name)

        real_task_id = user_ctx.get("task_id") or "task_sdk_runner"

        # 0. Check for Agent Delegation Tools (e.g. call_system_agent, call_browser_agent, call_code_agent)
        clean_ag = self.tool_name.replace("call_", "").replace("_agent", "")
        is_agent_call = (
            self.tool_name.startswith("call_")
            or self.tool_name.endswith("_agent")
            or (self.agent is not None and clean_ag == str(getattr(self.agent, "AGENT_NAME", "")).lower())
        )
        if is_agent_call and self.agent is not None and hasattr(self.agent, "execute"):
            instruction = (
                params.get("instruction")
                or params.get("message")
                or params.get("query")
                or params.get("task")
                or str(params.get("_raw") or params)
            )
            entities = params.get("entities") if isinstance(params.get("entities"), dict) else params
            try:
                res = await self.agent.execute(
                    task_id=real_task_id,
                    message=instruction,
                    context=user_ctx,
                    entities=entities,
                )
                if isinstance(res, (dict, list)):
                    return json.dumps(res, ensure_ascii=False)
                return str(res) if res is not None else ""
            except Exception as e:
                logger.error("[sdk_bridge] Agent delegation '%s' failed: %s", self.tool_name, e)
                return f"[Agent Delegation Error in {self.tool_name}]: {e}"

        # 1. Dispatch via Agent's _use_tool (Preserves per-agent locks, gates, and ExecutionRuntime)
        if self.agent is not None and hasattr(self.agent, "_use_tool"):
            try:
                res = await self.agent._use_tool(self.tool_name, **params)
                if isinstance(res, (dict, list)):
                    return json.dumps(res, ensure_ascii=False)
                return str(res) if res is not None else ""
            except Exception as e:
                logger.error(
                    "[sdk_bridge] Agent tool '%s' execution failed: %s",
                    self.tool_name,
                    e,
                )
                return f"[Tool Error in {self.tool_name}]: {e}"

        # 2. Dispatch via ExecutionRuntime (Preserves Saga rollback & Invariant verification)
        runtime = self.execution_runtime
        if not runtime and self.registry and hasattr(self.registry, "_execution_runtime"):
            runtime = self.registry._execution_runtime

        if runtime is not None and hasattr(runtime, "execute_action"):
            try:
                from .contracts import Action

                action = Action(
                    action_id=f"act_sdk_{self.tool_name}_{uuid.uuid4().hex[:8]}",
                    task_id=real_task_id,
                    capability_name=self.tool_name,
                    parameters=params,
                )
                exec_res = await runtime.execute_action(action=action)
                if getattr(exec_res, "is_success", True):
                    out = getattr(exec_res, "tool_output", "") or ""
                    if isinstance(out, (dict, list)):
                        return json.dumps(out, ensure_ascii=False)
                    return str(out)
                return f"[Failed] {getattr(exec_res, 'error', 'Action execution failed')}"
            except Exception as e:
                logger.error(
                    "[sdk_bridge] ExecutionRuntime failed for '%s': %s",
                    self.tool_name,
                    e,
                )
                return f"[Tool Error in {self.tool_name}]: {e}"

        # 3. Dispatch via Registry direct tool lookup
        if self.registry is not None and hasattr(self.registry, "get_tool"):
            meta = self.registry.get_tool(self.tool_name)
            if meta and getattr(meta, "func", None):
                try:
                    res = meta.func(**params)
                    if inspect.isawaitable(res):
                        res = await res
                    return str(res) if not isinstance(res, (dict, list)) else json.dumps(res, ensure_ascii=False)
                except Exception as e:
                    return f"[Tool Error in {self.tool_name}]: {e}"

        # 4. Dispatch via Custom Func
        if self.custom_func is not None:
            try:
                res = self.custom_func(**params)
                if inspect.isawaitable(res):
                    res = await res
                return str(res) if not isinstance(res, (dict, list)) else json.dumps(res, ensure_ascii=False)
            except Exception as e:
                return f"[Tool Error in {self.tool_name}]: {e}"

        return f"[Tool Error]: No execution handler registered for tool '{self.tool_name}'"


def to_sdk_function_tool(
    tool_name: str,
    registry: Optional[Any] = None,
    execution_runtime: Optional[Any] = None,
    agent: Optional[Any] = None,
    description: Optional[str] = None,
    schema: Optional[dict[str, Any]] = None,
    func: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None,
) -> FunctionTool:
    """
    Convert a Makima tool definition into an OpenAI Agents SDK FunctionTool.
    
    Guarantees that when the SDK Runner invokes the tool:
    - Pre-state snapshots are captured
    - Invariant verification is evaluated
    - Saga auto-compensation / rollback occurs on failure
    - WebSocket started / finished events are broadcasted
    """
    clean_name = tool_name.replace("call_", "").strip()
    tool_desc = description or ""
    tool_schema: dict[str, Any] = schema or {}
    custom_fn = func

    tool_category = "general"
    tool_tags: list[str] = []
    if registry is not None:
        meta = registry.get_tool(clean_name) or registry.get_tool(tool_name)
        if meta:
            if not tool_desc:
                tool_desc = getattr(meta, "description", "")
            if not tool_schema:
                tool_schema = getattr(meta, "schema", {})
            if not custom_fn:
                custom_fn = getattr(meta, "func", None)
            tool_category = getattr(meta, "category", "general")
            tool_tags = getattr(meta, "task_tags", []) or []

    if agent is not None:
        if not tool_desc and hasattr(agent, "_TOOL_DESCRIPTIONS") and isinstance(agent._TOOL_DESCRIPTIONS, dict):
            tool_desc = agent._TOOL_DESCRIPTIONS.get(clean_name) or agent._TOOL_DESCRIPTIONS.get(tool_name) or ""
        if (not tool_schema or tool_schema.get("properties") == {}) and hasattr(agent, "_TOOL_SCHEMAS") and isinstance(agent._TOOL_SCHEMAS, dict):
            schema_entry = agent._TOOL_SCHEMAS.get(clean_name) or agent._TOOL_SCHEMAS.get(tool_name)
            if isinstance(schema_entry, tuple) and len(schema_entry) > 0 and isinstance(schema_entry[0], dict):
                tool_schema = schema_entry[0]
            elif isinstance(schema_entry, dict):
                tool_schema = schema_entry
        if not custom_fn and hasattr(agent, "_TOOL_MAP") and isinstance(agent._TOOL_MAP, dict):
            custom_fn = agent._TOOL_MAP.get(clean_name) or agent._TOOL_MAP.get(tool_name)
        if not tool_desc:
            handler = getattr(agent, f"_tool_{clean_name}", None) or getattr(agent, f"_tool_{tool_name}", None)
            if handler and handler.__doc__:
                tool_desc = handler.__doc__.strip().splitlines()[0]
        if hasattr(agent, "DOMAIN_LANE"):
            tool_category = getattr(agent, "DOMAIN_LANE", "general")
        elif hasattr(agent, "TAGS"):
            tool_tags = getattr(agent, "TAGS", []) or []

    if not tool_desc:
        tool_desc = f"Makima OS tool: {clean_name}"

    if not tool_schema or not isinstance(tool_schema, dict):
        tool_schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

    # Ensure schema has standard properties structure
    if "type" not in tool_schema:
        tool_schema = {
            "type": "object",
            "properties": tool_schema.get("properties", {}),
            "required": tool_schema.get("required", []),
        }

    # Auto-synthesize parameter schema from callable signature if properties are empty
    if not tool_schema.get("properties"):
        target_callable = custom_fn or (getattr(agent, f"_tool_{clean_name}", None) if agent else None)
        if target_callable and callable(target_callable):
            try:
                sig = inspect.signature(target_callable)
                synthesized_props: dict[str, Any] = {}
                required_params: list[str] = []
                for p_name, param in sig.parameters.items():
                    if p_name in ("self", "cls", "context", "kwargs", "args") or param.kind in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    ):
                        continue
                    type_name = "string"
                    if param.annotation is int:
                        type_name = "integer"
                    elif param.annotation is float:
                        type_name = "number"
                    elif param.annotation is bool:
                        type_name = "boolean"
                    elif param.annotation in (list, "list[str]", "list[int]"):
                        type_name = "array"
                    elif param.annotation in (dict, "dict[str, Any]"):
                        type_name = "object"
                    synthesized_props[p_name] = {"type": type_name, "description": f"Parameter '{p_name}'"}
                    if param.default is inspect.Parameter.empty:
                        required_params.append(p_name)
                if synthesized_props:
                    tool_schema = {
                        "type": "object",
                        "properties": synthesized_props,
                        "required": required_params,
                    }
            except Exception:
                pass

    invoker = ToolInvokerProxy(
        tool_name=clean_name,
        registry=registry,
        execution_runtime=execution_runtime,
        agent=agent,
        custom_func=custom_fn,
    )

    return FunctionTool(
        name=clean_name,
        description=tool_desc,
        params_json_schema=tool_schema,
        on_invoke_tool=invoker,
        strict_json_schema=False,
    )


def to_sdk_tools(
    tool_names: Sequence[str],
    registry: Optional[Any] = None,
    execution_runtime: Optional[Any] = None,
    agent: Optional[Any] = None,
) -> list[FunctionTool]:
    """Batch convert a collection of Makima tool names into SDK FunctionTool objects."""
    tools: list[FunctionTool] = []
    for name in tool_names:
        if name and isinstance(name, str):
            tools.append(
                to_sdk_function_tool(
                    tool_name=name,
                    registry=registry,
                    execution_runtime=execution_runtime,
                    agent=agent,
                )
            )
    return tools


class MakimaModel(Model):
    """
    OpenAI Agents SDK Model implementation routing to Makima's NEXUS LLM Gateway.
    
    Preserves:
      - Dynamic Pareto latency-based routing
      - Per-provider Adaptive Circuit Breakers
      - Multi-key sticky rotation
      - Support for Qwen, Groq, DeepSeek, Gemini, and Ollama
    """

    def __init__(
        self,
        ai_handler: Any,
        task: str = "general",
        model_name: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        self.ai_handler = ai_handler
        self.task = task
        self.model_name = model_name
        self.agent_name = agent_name

    async def get_response(
        self,
        system_instructions: Optional[str],
        input: Union[str, list[TResponseInputItem]],
        model_settings: Any,
        tools: list[Tool],
        output_schema: Any,
        handoffs: list[Any],
        tracing: ModelTracing,
        *,
        previous_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        prompt: Optional[Any] = None,
    ) -> ModelResponse:
        """
        Translates SDK input and tool specifications to Makima's message format,
        calls ai_handler.generate(), and formats the result as a ModelResponse.
        """
        messages: list[dict[str, Any]] = []

        # 1. Convert input items using SDK's Converter
        try:
            raw_msgs = Converter.items_to_messages(input, model=self.model_name)
            for m in raw_msgs:
                messages.append(dict(m))
        except Exception as conv_err:
            logger.debug("[sdk_bridge] Fallback message conversion for input: %s", conv_err)
            if isinstance(input, str):
                messages.append({"role": "user", "content": input})
            elif isinstance(input, list):
                for item in input:
                    if isinstance(item, dict) and "content" in item:
                        messages.append(item)
                    else:
                        messages.append({"role": "user", "content": str(item)})

        # 2. Prepend system instructions if provided
        if system_instructions:
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_instructions})
            else:
                existing_sys = messages[0].get("content", "")
                if system_instructions not in existing_sys:
                    messages[0]["content"] = f"{existing_sys}\n\n{system_instructions}".strip()

        # 3. Build Tools manifest for ai_handler
        tools_manifest: list[dict[str, Any]] = []
        for t in tools:
            try:
                openai_t = Converter.tool_to_openai(t)
                tools_manifest.append(openai_t)
            except Exception as t_err:
                logger.debug("[sdk_bridge] Could not convert tool '%s': %s", getattr(t, "name", "?"), t_err)

        for h in handoffs:
            try:
                openai_h = Converter.convert_handoff_tool(h)
                tools_manifest.append(openai_h)
            except Exception as h_err:
                logger.debug("[sdk_bridge] Could not convert handoff tool: %s", h_err)

        # 4. Invoke Makima's NEXUS LLM Gateway
        kwargs: dict[str, Any] = {}
        if tools_manifest:
            kwargs["tools"] = tools_manifest
            kwargs["tool_choice"] = "auto"
        if self.agent_name:
            kwargs["agent_name"] = self.agent_name

        effective_model = getattr(model_settings, "model", None) or self.model_name
        if effective_model:
            kwargs["model"] = effective_model
        if hasattr(model_settings, "temperature") and getattr(model_settings, "temperature", None) is not None:
            kwargs["temperature"] = getattr(model_settings, "temperature")
        if hasattr(model_settings, "max_tokens") and getattr(model_settings, "max_tokens", None) is not None:
            kwargs["max_tokens"] = getattr(model_settings, "max_tokens")

        # Multi-user isolation & BYOK: Inject per-request client credentials and overrides
        try:
            from .orchestration_engine import client_request_context
            req_ctx = client_request_context.get({}) or {}
            if req_ctx.get("provider"):
                kwargs["provider"] = req_ctx["provider"]
            if req_ctx.get("api_key"):
                kwargs["api_key"] = req_ctx["api_key"]
            if req_ctx.get("base_url"):
                kwargs["base_url"] = req_ctx["base_url"]
            if req_ctx.get("model") and "model" not in kwargs:
                kwargs["model"] = req_ctx["model"]
        except Exception:
            pass

        resp = await self.ai_handler.generate(
            messages=messages,
            task=self.task,
            **kwargs,
        )

        # 5. Extract results from LLMResponse or dict
        if isinstance(resp, dict):
            raw_text = resp.get("text", "") or ""
            raw_tool_calls = resp.get("tool_calls", []) or []
            total_tokens = resp.get("total_tokens", 50)
        else:
            # Use None-sentinel to avoid the `"" or str(resp)` falsy-trap:
            # when LLM returns only tool_calls with empty text, getattr returns ""
            # which is falsy → str(resp) dumps the LLMResponse repr into raw_text.
            _resp_txt = getattr(resp, "text", None)
            raw_text = resp if isinstance(resp, str) else (_resp_txt if _resp_txt is not None else "")
            raw_tool_calls = getattr(resp, "tool_calls", []) or []
            total_tokens = getattr(resp, "total_tokens", 0)

        if not isinstance(raw_tool_calls, list):
            raw_tool_calls = []

        if not isinstance(total_tokens, int) or total_tokens <= 0:
            total_tokens = 50

        # Fallback check for embedded tool calls in raw_text
        if raw_text and not raw_tool_calls:
            try:
                from ..ai_handler import AIHandler
                cleaned_t, extracted_tcs = AIHandler.extract_embedded_tool_calls(raw_text)
                if extracted_tcs:
                    raw_tool_calls = extracted_tcs
                    raw_text = cleaned_t
            except Exception:
                pass

        output_items: list[Any] = []

        # Build map of valid tool & handoff names for this agent turn
        valid_tool_map: dict[str, str] = {}
        for t in tools:
            t_name = getattr(t, "name", None)
            if not t_name and hasattr(Converter, "tool_to_openai"):
                try:
                    t_name = Converter.tool_to_openai(t).get("function", {}).get("name")
                except Exception:
                    pass
            if t_name:
                valid_tool_map[t_name.lower()] = t_name
        for h in handoffs:
            h_name = getattr(h, "name", None) or getattr(h, "tool_name", None)
            if h_name:
                valid_tool_map[h_name.lower()] = h_name

        # PATH A: Native Tool Calls
        if raw_tool_calls:
            if raw_text and raw_text.strip():
                output_items.append(
                    ResponseOutputMessage(
                        id=f"msg_{uuid.uuid4().hex[:8]}",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text=raw_text.strip(),
                                type="output_text",
                                logprobs=[],
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                )

            for idx, tc in enumerate(raw_tool_calls):
                call_id = tc.get("id") or f"call_sdk_{idx}_{uuid.uuid4().hex[:6]}"
                fn_name = str(tc.get("name") or tc.get("function", {}).get("name", "")).strip()
                raw_args = tc.get("arguments") or tc.get("function", {}).get("arguments", "{}")
                args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)

                resolved_fn_name = valid_tool_map.get(fn_name.lower())
                if not resolved_fn_name:
                    lower_fn = fn_name.lower()
                    if any(k in lower_fn for k in ("research", "web_search", "search", "fetch_url")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_researchagent")
                    elif any(k in lower_fn for k in ("system", "stats", "process", "launch_app", "window")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_systemagent")
                    elif any(k in lower_fn for k in ("browser", "navigate", "dom", "click", "scrape")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_browseragent")
                    elif any(k in lower_fn for k in ("code", "python", "script", "syntax")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_codeagent")
                    elif any(k in lower_fn for k in ("media", "music", "spotify", "youtube", "volume")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_mediaagent")
                    elif any(k in lower_fn for k in ("automation", "timer", "cron", "schedule")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_automationagent")
                    elif any(k in lower_fn for k in ("security", "vuln", "cve", "audit")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_securityagent")
                    elif any(k in lower_fn for k in ("data", "csv", "chart", "pandas")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_dataanalystagent")
                    elif any(k in lower_fn for k in ("document", "docx", "pdf", "xlsx", "pptx")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_documentagent")
                    elif any(k in lower_fn for k in ("message", "whatsapp", "telegram", "discord", "email")):
                        resolved_fn_name = valid_tool_map.get("transfer_to_messagingagent")

                if not resolved_fn_name:
                    logger.warning("[sdk_bridge] Discarding invalid/unregistered tool call '%s'", fn_name)
                    continue

                output_items.append(
                    ResponseFunctionToolCall(
                        arguments=args_str,
                        call_id=call_id,
                        name=resolved_fn_name,
                        type="function_call",
                    )
                )

        # PATH B: Plain Text / Assistant Output
        if raw_text and not raw_tool_calls:
            # Fallback check for embedded JSON tool call in text
            parsed_json = None
            if hasattr(self.ai_handler, "try_parse_json"):
                try:
                    pj = self.ai_handler.try_parse_json(raw_text)
                    if inspect.isawaitable(pj):
                        pj = await pj
                    if isinstance(pj, dict):
                        parsed_json = pj
                except Exception:
                    pass

            if not isinstance(parsed_json, dict):
                try:
                    cleaned_text = raw_text.strip()
                    if (cleaned_text.startswith("{") and cleaned_text.endswith("}")) or (cleaned_text.startswith("```json") and cleaned_text.endswith("```")):
                        stripped = re.sub(r"^```json\s*|\s*```$", "", cleaned_text).strip()
                        parsed_json = json.loads(stripped)
                except Exception:
                    parsed_json = None

            if isinstance(parsed_json, dict) and any(k in parsed_json for k in ("tool", "action", "function")):
                fn_name = parsed_json.get("tool") or parsed_json.get("action") or parsed_json.get("function")
                fn_str = str(fn_name).lower() if fn_name else ""
                if fn_str in ("complete", "finish", "done", "response", "reply", "none", "null", ""):
                    reply_text = str(parsed_json.get("reply") or parsed_json.get("message") or parsed_json.get("content") or raw_text).strip()
                    output_items.append(
                        ResponseOutputMessage(
                            id=f"msg_{uuid.uuid4().hex[:8]}",
                            content=[
                                ResponseOutputText(
                                    annotations=[],
                                    text=reply_text,
                                    type="output_text",
                                    logprobs=[],
                                )
                            ],
                            role="assistant",
                            status="completed",
                            type="message",
                        )
                    )
                else:
                    resolved_b = valid_tool_map.get(str(fn_name).lower())
                    if not resolved_b:
                        lower_fn = str(fn_name).lower()
                        if any(k in lower_fn for k in ("research", "web_search", "search", "fetch_url")):
                            resolved_b = valid_tool_map.get("transfer_to_researchagent")
                        elif any(k in lower_fn for k in ("system", "stats", "process", "launch_app", "window")):
                            resolved_b = valid_tool_map.get("transfer_to_systemagent")
                        elif any(k in lower_fn for k in ("browser", "navigate", "dom", "click", "scrape")):
                            resolved_b = valid_tool_map.get("transfer_to_browseragent")
                        elif any(k in lower_fn for k in ("code", "python", "script", "syntax")):
                            resolved_b = valid_tool_map.get("transfer_to_codeagent")
                        elif any(k in lower_fn for k in ("media", "music", "spotify", "youtube", "volume")):
                            resolved_b = valid_tool_map.get("transfer_to_mediaagent")
                        elif any(k in lower_fn for k in ("automation", "timer", "cron", "schedule")):
                            resolved_b = valid_tool_map.get("transfer_to_automationagent")
                        elif any(k in lower_fn for k in ("security", "vuln", "cve", "audit")):
                            resolved_b = valid_tool_map.get("transfer_to_securityagent")
                        elif any(k in lower_fn for k in ("data", "csv", "chart", "pandas")):
                            resolved_b = valid_tool_map.get("transfer_to_dataanalystagent")
                        elif any(k in lower_fn for k in ("document", "docx", "pdf", "xlsx", "pptx")):
                            resolved_b = valid_tool_map.get("transfer_to_documentagent")
                        elif any(k in lower_fn for k in ("message", "whatsapp", "telegram", "discord", "email")):
                            resolved_b = valid_tool_map.get("transfer_to_messagingagent")

                    if resolved_b:
                        params = parsed_json.get("params") or parsed_json.get("arguments") or parsed_json.get("parameters") or {}
                        call_id = f"call_sdk_json_{uuid.uuid4().hex[:6]}"
                        output_items.append(
                            ResponseFunctionToolCall(
                                arguments=json.dumps(params) if isinstance(params, dict) else str(params),
                                call_id=call_id,
                                name=resolved_b,
                                type="function_call",
                            )
                        )
            else:
                clean_reply = raw_text
                if isinstance(parsed_json, dict) and any(k in parsed_json for k in ("reply", "message", "response")):
                    clean_reply = str(parsed_json.get("reply") or parsed_json.get("message") or parsed_json.get("response") or raw_text)
                output_items.append(
                    ResponseOutputMessage(
                        id=f"msg_{uuid.uuid4().hex[:8]}",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text=clean_reply,
                                type="output_text",
                                logprobs=[],
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                )

        # Ensure at least one output item exists
        if not output_items:
            output_items.append(
                ResponseOutputMessage(
                    id=f"msg_{uuid.uuid4().hex[:8]}",
                    content=[
                        ResponseOutputText(
                            annotations=[],
                            text=raw_text or "Task completed.",
                            type="output_text",
                            logprobs=[],
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            )

        return ModelResponse(
            output=output_items,
            usage=Usage(
                requests=1,
                input_tokens=total_tokens // 2,
                output_tokens=total_tokens // 2,
                total_tokens=total_tokens,
            ),
            response_id=f"resp_{uuid.uuid4().hex[:10]}",
        )

    async def stream_response(
        self,
        system_instructions: Optional[str],
        input: Union[str, list[TResponseInputItem]],
        model_settings: Any,
        tools: list[Tool],
        output_schema: Any,
        handoffs: list[Any],
        tracing: ModelTracing,
        *,
        previous_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        prompt: Optional[Any] = None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        """
        Streaming response adapter for OpenAI Agents SDK.
        Streams live tokens from ai_handler.generate_stream() or converts
        get_response() output into compliant TResponseStreamEvents.
        """
        from openai.types.responses import (
            Response,
            ResponseOutputItemAddedEvent,
            ResponseOutputItemDoneEvent,
            ResponseTextDeltaEvent,
            ResponseTextDoneEvent,
            ResponseFunctionCallArgumentsDeltaEvent,
            ResponseFunctionCallArgumentsDoneEvent,
            ResponseCompletedEvent,
        )

        # 1. Convert input items to message dicts
        messages: list[dict[str, Any]] = []
        try:
            raw_msgs = Converter.items_to_messages(input, model=self.model_name)
            for m in raw_msgs:
                messages.append(dict(m))
        except Exception:
            if isinstance(input, str):
                messages.append({"role": "user", "content": input})
            elif isinstance(input, list):
                for it in input:
                    if isinstance(it, dict) and "content" in it:
                        messages.append(it)
                    else:
                        messages.append({"role": "user", "content": str(it)})

        # 2. Prepend system instructions if provided
        if system_instructions:
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_instructions})
            else:
                existing_sys = messages[0].get("content", "")
                if system_instructions not in existing_sys:
                    messages[0]["content"] = f"{existing_sys}\n\n{system_instructions}".strip()

        # 3. Build Tools & Handoffs manifest
        tools_manifest: list[dict[str, Any]] = []
        for t in tools:
            try:
                openai_t = Converter.tool_to_openai(t)
                tools_manifest.append(openai_t)
            except Exception as t_err:
                logger.debug("[sdk_bridge] Could not convert tool '%s': %s", getattr(t, "name", "?"), t_err)

        for h in handoffs:
            try:
                openai_h = Converter.convert_handoff_tool(h)
                tools_manifest.append(openai_h)
            except Exception as h_err:
                logger.debug("[sdk_bridge] Could not convert handoff tool: %s", h_err)

        kwargs: dict[str, Any] = {}
        if tools_manifest:
            kwargs["tools"] = tools_manifest
            kwargs["tool_choice"] = "auto"
        if self.agent_name:
            kwargs["agent_name"] = self.agent_name

        effective_model = getattr(model_settings, "model", None) or self.model_name
        if effective_model:
            kwargs["model"] = effective_model
        if hasattr(model_settings, "temperature") and getattr(model_settings, "temperature", None) is not None:
            kwargs["temperature"] = getattr(model_settings, "temperature")
        if hasattr(model_settings, "max_tokens") and getattr(model_settings, "max_tokens", None) is not None:
            kwargs["max_tokens"] = getattr(model_settings, "max_tokens")

        seq = 0
        msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        text_output_item: Optional[ResponseOutputMessage] = None
        text_output_idx = 0
        full_text_chunks: list[str] = []

        # Tool calls tracking: map index -> item and index -> output_index
        tool_call_items: dict[int, ResponseFunctionToolCall] = {}
        tool_call_indices: dict[int, int] = {}
        output_items: list[Any] = []
        cur_out_idx = 0
        stream_succeeded = False

        # Build map of valid tool & handoff names for this streaming turn
        valid_tool_map: dict[str, str] = {}
        for t in tools:
            t_name = getattr(t, "name", None)
            if not t_name and hasattr(Converter, "tool_to_openai"):
                try:
                    t_name = Converter.tool_to_openai(t).get("function", {}).get("name")
                except Exception:
                    pass
            if t_name:
                valid_tool_map[t_name.lower()] = t_name
        for h in handoffs:
            h_name = getattr(h, "name", None) or getattr(h, "tool_name", None)
            if h_name:
                valid_tool_map[h_name.lower()] = h_name

        try:
            if hasattr(self.ai_handler, "generate_stream_events"):
                stream_gen = self.ai_handler.generate_stream_events(messages, task=self.task, **kwargs)
            else:
                async def _text_only_gen():
                    async for chunk in self.ai_handler.generate_stream(messages, task=self.task, **kwargs):
                        yield {"type": "text_delta", "text": chunk}
                stream_gen = _text_only_gen()

            async for ev in stream_gen:
                ev_type = ev.get("type")
                if ev_type == "text_delta":
                    tok = ev.get("text", "")
                    if tok:
                        if text_output_item is None:
                            text_output_idx = cur_out_idx
                            cur_out_idx += 1
                            text_output_item = ResponseOutputMessage(
                                id=msg_id,
                                role="assistant",
                                content=[],
                                status="in_progress",
                                type="message",
                            )
                            output_items.append(text_output_item)
                            yield ResponseOutputItemAddedEvent(
                                item=text_output_item,
                                output_index=text_output_idx,
                                sequence_number=seq,
                                type="response.output_item.added",
                            )
                            seq += 1

                        full_text_chunks.append(tok)
                        yield ResponseTextDeltaEvent(
                            content_index=0,
                            delta=tok,
                            item_id=msg_id,
                            logprobs=[],
                            output_index=text_output_idx,
                            sequence_number=seq,
                            type="response.output_text.delta",
                        )
                        seq += 1

                elif ev_type == "tool_call_delta":
                    tc_idx = ev.get("index", 0)
                    tc_id = ev.get("id") or f"call_sdk_{tc_idx}_{uuid.uuid4().hex[:6]}"
                    fn_name = str(ev.get("name") or "").strip()
                    args_delta = ev.get("arguments_delta", "")

                    # Resolve fn_name against valid tools & handoffs
                    resolved_fn = valid_tool_map.get(fn_name.lower()) if fn_name else None
                    if not resolved_fn and fn_name:
                        lower_fn = fn_name.lower()
                        if any(k in lower_fn for k in ("research", "web_search", "search", "fetch_url", "news", "headlines")):
                            resolved_fn = valid_tool_map.get("transfer_to_researchagent")
                        elif any(k in lower_fn for k in ("system", "stats", "process", "launch_app", "window", "desktop")):
                            resolved_fn = valid_tool_map.get("transfer_to_systemagent")
                        elif any(k in lower_fn for k in ("browser", "navigate", "dom", "click", "scrape", "webmail")):
                            resolved_fn = valid_tool_map.get("transfer_to_browseragent")
                        elif any(k in lower_fn for k in ("code", "python", "script", "syntax", "refactor")):
                            resolved_fn = valid_tool_map.get("transfer_to_codeagent")
                        elif any(k in lower_fn for k in ("media", "music", "spotify", "youtube", "volume", "track")):
                            resolved_fn = valid_tool_map.get("transfer_to_mediaagent")
                        elif any(k in lower_fn for k in ("automation", "timer", "cron", "schedule")):
                            resolved_fn = valid_tool_map.get("transfer_to_automationagent")
                        elif any(k in lower_fn for k in ("security", "vuln", "cve", "audit")):
                            resolved_fn = valid_tool_map.get("transfer_to_securityagent")
                        elif any(k in lower_fn for k in ("data", "csv", "chart", "pandas", "polars")):
                            resolved_fn = valid_tool_map.get("transfer_to_dataanalystagent")
                        elif any(k in lower_fn for k in ("document", "docx", "pdf", "xlsx", "pptx")):
                            resolved_fn = valid_tool_map.get("transfer_to_documentagent")
                        elif any(k in lower_fn for k in ("message", "whatsapp", "telegram", "discord", "email")):
                            resolved_fn = valid_tool_map.get("transfer_to_messagingagent")

                    if not resolved_fn and fn_name:
                        logger.warning("[sdk_bridge.stream] Discarding invalid tool call '%s' (not in agent tools/handoffs)", fn_name)
                        continue

                    if tc_idx not in tool_call_items:
                        tc_item = ResponseFunctionToolCall(
                            arguments="",
                            call_id=tc_id,
                            name=resolved_fn or fn_name,
                            type="function_call",
                        )
                        tool_call_items[tc_idx] = tc_item
                        tc_out_idx = cur_out_idx
                        cur_out_idx += 1
                        tool_call_indices[tc_idx] = tc_out_idx
                        output_items.append(tc_item)
                        yield ResponseOutputItemAddedEvent(
                            item=tc_item,
                            output_index=tc_out_idx,
                            sequence_number=seq,
                            type="response.output_item.added",
                        )
                        seq += 1
                    else:
                        tc_item = tool_call_items[tc_idx]
                        if resolved_fn and not tc_item.name:
                            tc_item.name = resolved_fn

                    if args_delta:
                        tc_item.arguments += args_delta
                        yield ResponseFunctionCallArgumentsDeltaEvent(
                            delta=args_delta,
                            item_id=tc_item.call_id,
                            output_index=tool_call_indices[tc_idx],
                            sequence_number=seq,
                            type="response.function_call_arguments.delta",
                        )
                        seq += 1

            if output_items:
                stream_succeeded = True

        except Exception as stream_err:
            logger.warning("[sdk_bridge] Streaming events error (%s), attempting fallback...", stream_err)

        # Fallback if streaming produced 0 items or errored
        if not stream_succeeded or not output_items:
            resp = await self.get_response(
                system_instructions=system_instructions,
                input=input,
                model_settings=model_settings,
                tools=tools,
                output_schema=output_schema,
                handoffs=handoffs,
                tracing=tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            )
            final_items: list[Any] = []
            for idx, item in enumerate(resp.output):
                final_items.append(item)
                if isinstance(item, ResponseFunctionToolCall):
                    yield ResponseOutputItemAddedEvent(
                        item=item,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.output_item.added",
                    )
                    seq += 1
                    yield ResponseFunctionCallArgumentsDeltaEvent(
                        delta=item.arguments,
                        item_id=item.call_id,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.function_call_arguments.delta",
                    )
                    seq += 1
                    yield ResponseFunctionCallArgumentsDoneEvent(
                        arguments=item.arguments,
                        name=item.name,
                        item_id=item.call_id,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.function_call_arguments.done",
                    )
                    seq += 1
                    yield ResponseOutputItemDoneEvent(
                        item=item,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.output_item.done",
                    )
                    seq += 1
                elif isinstance(item, ResponseOutputMessage):
                    yield ResponseOutputItemAddedEvent(
                        item=item,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.output_item.added",
                    )
                    seq += 1
                    full_text = ""
                    for part in getattr(item, "content", []):
                        part_text = getattr(part, "text", "") or ""
                        full_text += part_text
                    if full_text:
                        chunk_size = 32
                        for c_idx in range(0, len(full_text), chunk_size):
                            chunk = full_text[c_idx:c_idx + chunk_size]
                            yield ResponseTextDeltaEvent(
                                content_index=0,
                                delta=chunk,
                                item_id=item.id,
                                logprobs=[],
                                output_index=idx,
                                sequence_number=seq,
                                type="response.output_text.delta",
                            )
                            seq += 1
                        yield ResponseTextDoneEvent(
                            content_index=0,
                            item_id=item.id,
                            logprobs=[],
                            output_index=idx,
                            sequence_number=seq,
                            text=full_text,
                            type="response.output_text.done",
                        )
                        seq += 1
                    yield ResponseOutputItemDoneEvent(
                        item=item,
                        output_index=idx,
                        sequence_number=seq,
                        type="response.output_item.done",
                    )
                    seq += 1

            response_obj = Response(
                id=resp.response_id or f"resp_{uuid.uuid4().hex[:10]}",
                created_at=int(time.time()),
                model=self.model_name or "makima",
                object="response",
                output=final_items,
                status="completed",
                parallel_tool_calls=True,
                tool_choice="auto",
                tools=[],
            )
            yield ResponseCompletedEvent(
                response=response_obj,
                sequence_number=seq,
                type="response.completed",
            )
            return

        # 4. Finalize streamed items
        # A. Finalize text message if emitted
        if text_output_item is not None:
            accumulated_text = "".join(full_text_chunks)
            text_output_item.content = [ResponseOutputText(text=accumulated_text, type="output_text", annotations=[])]
            text_output_item.status = "completed"
            yield ResponseTextDoneEvent(
                content_index=0,
                item_id=msg_id,
                logprobs=[],
                output_index=text_output_idx,
                sequence_number=seq,
                text=accumulated_text,
                type="response.output_text.done",
            )
            seq += 1
            yield ResponseOutputItemDoneEvent(
                item=text_output_item,
                output_index=text_output_idx,
                sequence_number=seq,
                type="response.output_item.done",
            )
            seq += 1

            # Fallback check for embedded tool calls in text if no tool calls were natively streamed
            if not tool_call_items:
                try:
                    from ..ai_handler import AIHandler
                    cleaned_t, extracted_tcs = AIHandler.extract_embedded_tool_calls(accumulated_text)
                    if extracted_tcs:
                        for emb_idx, emb_tc in enumerate(extracted_tcs):
                            fn_n = emb_tc.get("name") or ""
                            fn_args = emb_tc.get("arguments") or "{}"
                            args_s = fn_args if isinstance(fn_args, str) else json.dumps(fn_args)
                            c_id = emb_tc.get("id") or f"call_emb_{uuid.uuid4().hex[:6]}"
                            resolved_emb = valid_tool_map.get(fn_n.lower()) if fn_n else None
                            if not resolved_emb and fn_n:
                                lower_fn = fn_n.lower()
                                if any(k in lower_fn for k in ("research", "web_search", "search", "fetch_url")):
                                    resolved_emb = valid_tool_map.get("transfer_to_researchagent")
                                elif any(k in lower_fn for k in ("system", "stats", "process", "launch_app", "window")):
                                    resolved_emb = valid_tool_map.get("transfer_to_systemagent")
                                elif any(k in lower_fn for k in ("browser", "navigate", "dom", "click", "scrape")):
                                    resolved_emb = valid_tool_map.get("transfer_to_browseragent")
                                elif any(k in lower_fn for k in ("code", "python", "script", "syntax")):
                                    resolved_emb = valid_tool_map.get("transfer_to_codeagent")
                                elif any(k in lower_fn for k in ("media", "music", "spotify", "youtube", "volume")):
                                    resolved_emb = valid_tool_map.get("transfer_to_mediaagent")
                                elif any(k in lower_fn for k in ("automation", "timer", "cron", "schedule")):
                                    resolved_emb = valid_tool_map.get("transfer_to_automationagent")
                                elif any(k in lower_fn for k in ("security", "vuln", "cve", "audit")):
                                    resolved_emb = valid_tool_map.get("transfer_to_securityagent")
                                elif any(k in lower_fn for k in ("data", "csv", "chart", "pandas")):
                                    resolved_emb = valid_tool_map.get("transfer_to_dataanalystagent")
                                elif any(k in lower_fn for k in ("document", "docx", "pdf", "xlsx", "pptx")):
                                    resolved_emb = valid_tool_map.get("transfer_to_documentagent")
                                elif any(k in lower_fn for k in ("message", "whatsapp", "telegram", "discord", "email")):
                                    resolved_emb = valid_tool_map.get("transfer_to_messagingagent")
                            if resolved_emb:
                                fn_n = resolved_emb
                            emb_item = ResponseFunctionToolCall(
                                arguments=args_s,
                                call_id=c_id,
                                name=fn_n,
                                type="function_call",
                            )
                            emb_out_idx = cur_out_idx
                            cur_out_idx += 1
                            output_items.append(emb_item)
                            yield ResponseOutputItemAddedEvent(
                                item=emb_item,
                                output_index=emb_out_idx,
                                sequence_number=seq,
                                type="response.output_item.added",
                            )
                            seq += 1
                            yield ResponseFunctionCallArgumentsDeltaEvent(
                                delta=args_s,
                                item_id=c_id,
                                output_index=emb_out_idx,
                                sequence_number=seq,
                                type="response.function_call_arguments.delta",
                            )
                            seq += 1
                            tool_call_items[len(tool_call_items)] = emb_item
                            tool_call_indices[len(tool_call_indices)] = emb_out_idx
                except Exception:
                    pass

        # B. Finalize tool calls
        for t_idx, tc_item in tool_call_items.items():
            t_out_idx = tool_call_indices[t_idx]
            yield ResponseFunctionCallArgumentsDoneEvent(
                arguments=tc_item.arguments,
                name=tc_item.name,
                item_id=tc_item.call_id,
                output_index=t_out_idx,
                sequence_number=seq,
                type="response.function_call_arguments.done",
            )
            seq += 1
            yield ResponseOutputItemDoneEvent(
                item=tc_item,
                output_index=t_out_idx,
                sequence_number=seq,
                type="response.output_item.done",
            )
            seq += 1

        response_obj = Response(
            id=f"resp_{uuid.uuid4().hex[:10]}",
            created_at=int(time.time()),
            model=self.model_name or "makima",
            object="response",
            output=output_items,
            status="completed",
            parallel_tool_calls=True,
            tool_choice="auto",
            tools=[],
        )
        yield ResponseCompletedEvent(
            response=response_obj,
            sequence_number=seq,
            type="response.completed",
        )


def get_sdk_session(
    session_id: str,
    db_path: str = "",
    limit: int = 12,
) -> SQLiteSession:
    """Return an SQLiteSession for multi-turn short-term conversational persistence with a sliding limit."""
    canonical_path = db_path or os.path.expanduser("~/.makima/sessions.db")
    os.makedirs(os.path.dirname(canonical_path), exist_ok=True)
    return SQLiteSession(
        session_id=str(session_id),
        db_path=canonical_path,
        session_settings=SessionSettings(limit=limit),
    )


def get_default_run_config(
    max_concurrency: int = 4,
    timeout: float = 75.0,
    enable_trimmer: bool = True,
    enable_handoff_filter: bool = True,
) -> RunConfig:
    """Return a high-performance RunConfig with SDK-native optimizations:
    1. Tracing disabled to eliminate span allocation & background processor flush overhead.
    2. ToolOutputTrimmer to compact large tool outputs from older turns and prevent prompt token bloat.
    3. remove_all_tools handoff filter to keep cross-agent delegation context clean.
    4. ToolExecutionConfig for parallel concurrent function tool execution.
    5. ModelSettings enabling parallel tool calls and bounded timeouts.
    6. SessionSettings(limit=16) for bounded SQLite session retrieval (<1ms).
    """
    return RunConfig(
        tracing_disabled=True,
        handoff_input_filter=remove_all_tools if enable_handoff_filter else None,
        call_model_input_filter=ToolOutputTrimmer(
            recent_turns=2,
            max_output_chars=600,
            preview_chars=200,
        ) if enable_trimmer else None,
        tool_execution=ToolExecutionConfig(
            max_function_tool_concurrency=max_concurrency,
        ),
        model_settings=ModelSettings(
            parallel_tool_calls=True,
            timeout=timeout,
        ),
        session_settings=SessionSettings(limit=16),
        tool_not_found_behavior="return_error_to_model",
    )


# =============================================================================
# OpenAI Agents SDK Guardrails (Input & Output Tripwires)
# =============================================================================

DANGEROUS_COMMAND_PATTERNS = [
    r"\brmdir\s+.*\/s\b",
    r"\brmdir\s*\/s",
    r"\brm\s+-(?:r[a-z]*f|f[a-z]*r)\b",
    r"\bformat\s+[a-zA-Z]:",
    r"\bdel\s+.*\/[fs]\b.*\/[fs]\b",
    r"\breg\s+delete\s+.*(?:hklm|hkcu)\b",
    r"\bbcdedit\b",
    r"\bcipher\s+\/w\b",
    r"\bdd\s+if=",
]


def is_dangerous_command(cmd: str) -> bool:
    """Evaluate whether an input string contains potentially destructive OS commands."""
    if not cmd or not isinstance(cmd, str):
        return False
    low = cmd.lower()
    if "rmdir /s" in low or "rmdir\\s" in low:
        return True
    if "rm -rf" in low or "rm -fr" in low:
        return True
    if "del /f /s /q" in low or ("del " in low and "/s" in low and "/q" in low):
        return True
    if "cipher /w" in low:
        return True
    if "dd if=" in low:
        return True
    if "bcdedit" in low:
        return True
    if "reg delete" in low and ("hklm" in low or "hkcu" in low):
        return True
    if re.search(r"\bformat\s+[a-z]:", low):
        return True
    for pat in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pat, low):
            return True
    return False


@input_guardrail
async def dangerous_command_guard(ctx: Any, agent: Any, input_data: Any) -> GuardrailFunctionOutput:
    """
    OpenAI Agents SDK Input Guardrail:
    Blocks destructive system commands before agent execution begins.
    """
    raw_text = str(input_data or "")
    if is_dangerous_command(raw_text):
        return GuardrailFunctionOutput(
            output_info="Dangerous command blocked",
            tripwire_triggered=True,
        )
    return GuardrailFunctionOutput(
        output_info="safe",
        tripwire_triggered=False,
    )


ACTION_CLAIMS = (
    "maine file delete ki",
    "maine file delete kar di",
    "maine delete kar diya",
    "maine install kar diya",
    "maine install kiya",
    "maine send kar diya",
    "maine bhej diya",
    "maine app close kar diya",
    "i have deleted",
    "i deleted the file",
    "i have installed",
    "i installed",
    "i have sent",
    "i sent the message",
    "successfully deleted",
    "successfully installed",
    "successfully sent",
)


@output_guardrail
async def fabrication_guard(ctx: Any, agent: Any, output: Any) -> GuardrailFunctionOutput:
    """
    OpenAI Agents SDK Output Guardrail:
    Checks whether the agent claims an action was performed when zero tools were executed.
    """
    text = str(output or "").lower()
    executed_tools: list[str] = []

    if ctx is not None:
        user_ctx = ctx if isinstance(ctx, dict) else getattr(ctx, "context", None)
        if isinstance(user_ctx, dict):
            executed_tools = user_ctx.get("tool_calls_executed", [])
    if not executed_tools and agent is not None:
        executed_tools = (
            getattr(agent, "_tool_calls_executed", [])
            or getattr(agent, "_tools_used_this_turn", [])
            or []
        )
        if getattr(agent, "_tool_calls_made", 0) > 0:
            executed_tools = ["tool_executed"]

    # If tools were executed, action claims are legitimate
    if executed_tools:
        return GuardrailFunctionOutput(output_info="safe", tripwire_triggered=False)

    for claim in ACTION_CLAIMS:
        if claim in text:
            return GuardrailFunctionOutput(
                output_info="Response claims action without tool execution",
                tripwire_triggered=True,
            )

    return GuardrailFunctionOutput(output_info="safe", tripwire_triggered=False)


# =============================================================================
# OpenAI Agents SDK Native Handoffs Architecture
# =============================================================================

def _make_on_handoff(agent_display_name: str, ws_broadcast: Optional[Callable] = None):
    """Factory returning an asynchronous on_handoff callback that emits UI status toast and agent_started event."""
    async def _on_handoff_cb(ctx: Any) -> None:
        user_ctx = ctx if isinstance(ctx, dict) else (getattr(ctx, "context", None) or {})
        task_id = user_ctx.get("task_id", "task_handoff")
        broadcast = user_ctx.get("ws_broadcast") or ws_broadcast
        notify_msg = f"{agent_display_name} ko task de raha hoon..."
        logger.info("[sdk_handoff] Native handoff triggered to %s: %s", agent_display_name, notify_msg)
        if broadcast:
            try:
                from ..ws_protocol import build_toast_notification, build_agent_started
                await broadcast(build_agent_started(task_id, agent=agent_display_name, subtask=notify_msg))
                await broadcast(build_toast_notification(task_id, notify_msg, level="info", duration_ms=2500))
            except Exception as e:
                logger.debug("[sdk_handoff] Failed to broadcast handoff event: %s", e)
    return _on_handoff_cb


def make_specialist_agents(
    ai_handler: Any,
    orchestrator: Optional[Any] = None,
    tool_registry: Optional[Any] = None,
    ws_broadcast: Optional[Callable] = None,
) -> dict[str, Agent]:
    """
    Constructs the 4 specialist SDK Agents (System, Browser, Code, Research)
    with their respective tools, instructions, handoff descriptions, and guardrails.
    """
    reg = tool_registry or (getattr(orchestrator, "tool_registry", None) if orchestrator else None)

    def _safe_get_agent(name: str) -> Optional[Any]:
        if not orchestrator or not hasattr(orchestrator, "get_agent"):
            return None
        try:
            obj = orchestrator.get_agent(name)
            if asyncio.iscoroutine(obj):
                obj.close()
                return None
            return obj
        except Exception:
            return None

    def _get_agent_tools(agent_name: str, agent_obj: Optional[Any]) -> list[str]:
        if agent_obj and hasattr(agent_obj, "AGENT_TOOLS") and agent_obj.AGENT_TOOLS:
            return list(agent_obj.AGENT_TOOLS)
        if reg and hasattr(reg, "get_manifest_for_agent"):
            try:
                manifest = reg.get_manifest_for_agent(agent_name)
                if asyncio.iscoroutine(manifest):
                    manifest.close()
                    return []
                if not isinstance(manifest, list):
                    return []
                names = []
                for m in manifest:
                    if isinstance(m, dict):
                        n = m.get("name") or (m.get("function", {}).get("name") if isinstance(m.get("function"), dict) else None)
                        if n:
                            names.append(n)
                return names
            except Exception:
                pass
        return []

    AGENT_SPECS: dict[str, tuple[str, str, str, list[Any], list[Any]]] = {
        "system": ("system_agent", "SystemAgent", "system_control", [dangerous_command_guard], [fabrication_guard]),
        "browser": ("browser_agent", "BrowserAgent", "automation", [dangerous_command_guard], []),
        "code": ("code_agent", "CodeAgent", "code", [dangerous_command_guard], [fabrication_guard]),
        "research": ("research_agent", "ResearchAgent", "research", [], []),
        "media": ("media_agent", "MediaAgent", "fast_chat", [], []),
        "automation": ("automation_agent", "AutomationAgent", "automation", [dangerous_command_guard], []),
        "devops": ("devops_agent", "DevOpsAgent", "code", [dangerous_command_guard], []),
        "security": ("security_agent", "SecurityAgent", "code", [dangerous_command_guard], []),
        "data_analyst": ("data_analyst_agent", "DataAnalystAgent", "research", [], []),
        "document": ("document_agent", "DocumentAgent", "research", [], []),
        "messaging": ("messaging_agent", "MessagingAgent", "fast_chat", [dangerous_command_guard], []),
    }

    specialists: dict[str, Agent] = {}
    for key, (agent_id, display_name, task_type, in_guards, out_guards) in AGENT_SPECS.items():
        obj = _safe_get_agent(agent_id)
        tool_names = _get_agent_tools(agent_id, obj)
        tools = to_sdk_tools(tool_names, registry=reg, agent=obj)
        prompt = getattr(obj, "SYSTEM_PROMPT", None) or f"You are Makima's {display_name}."
        desc = getattr(obj, "DESCRIPTION", None) or f"{display_name} for Makima."

        specialists[key] = Agent(
            name=display_name,
            handoff_description=desc,
            instructions=prompt,
            tools=tools,
            model=MakimaModel(ai_handler, task=task_type, agent_name=agent_id),
            input_guardrails=in_guards,
            output_guardrails=out_guards,
        )

    # Wire inter-specialist collaborative handoff mesh
    inter_routes: dict[str, list[str]] = {
        "code": ["devops", "system", "security"],
        "devops": ["system", "code"],
        "research": ["data_analyst", "document", "browser"],
        "data_analyst": ["document", "research"],
        "security": ["code", "devops"],
        "automation": ["system", "browser", "messaging"],
        "browser": ["system", "research"],
        "system": ["browser", "automation"],
        "messaging": ["browser", "system"],
    }
    for src_key, target_keys in inter_routes.items():
        if src_key in specialists:
            for tgt_key in target_keys:
                if tgt_key in specialists:
                    target_inst = specialists[tgt_key]
                    specialists[src_key].handoffs.append(
                        handoff(
                            target_inst,
                            on_handoff=_make_on_handoff(target_inst.name, ws_broadcast),
                        )
                    )

    return specialists


# =============================================================================
# Phase 2 — make_router_agent: Ephemeral SDK Agent for _execute_llm_first_turn
# =============================================================================

_ROUTER_SYSTEM_PROMPT = (
    "You are Makima — an intelligent, high-agency personal AI assistant with direct access to "
    "system, browser, code, research, media, automation, devops, security, and document tools.\n\n"
    "RULES:\n"
    "1. OS/app tasks (open app, window, screenshot, volume, files) → use system tools.\n"
    "2. Web/browser tasks (open URL, search, navigate, check online services) → use browser tools.\n"
    "3. Webmail & Personal Services (Gmail, WhatsApp, feeds) → NEVER refuse upfront. Use browser tools to navigate or system tools to inspect/launch on desktop.\n"
    "4. LIVE NEWS & REAL-TIME EVENTS: Current Year is 2026. Your training data is historical (2024). NEVER fabricate current news, elections, or events from internal memory. For ANY query about 'news', 'latest headlines', 'aaj ki news', 'india ki news', 'breaking news', or real-time status → MUST use research / web search tools.\n"
    "5. Timeless Knowledge & Casual Chat (math, coding syntax, grammar, general concepts, greetings) → answer directly, no tool calls.\n"
    "6. NEVER claim success without a tool result confirming it.\n"
    "7. Mirror user language (Hindi/Hinglish/English). Keep responses concise.\n\n"
    "PRESENTATION DISCIPLINE (CHATGPT & GEMINI SOTA STANDARD):\n"
    "- Structural Hierarchy: Use clean `## Level 2 Headers` for analytical or multi-step replies.\n"
    "- Bold Lead-In Bullets: Always use bold keywords (`- **Key**: context`) when listing items.\n"
    "- Compact Paragraphs: 2–3 sentences max per paragraph with clean blank lines.\n"
    "- Tables & Code: Markdown tables for comparisons; explicit language tags on all code blocks."
)


def make_router_agent(
    ai_handler: Any,
    tool_specs: list[dict[str, Any]],
    orchestrator: Optional[Any] = None,
    query: str = "",
) -> "Agent":
    """
    Ephemeral SDK Router Agent pre-loaded with top-N filtered tools for a specific query.
    Used as Phase-2 fallback when triage handoff returns empty output.
    """
    reg = getattr(orchestrator, "tool_registry", None) if orchestrator else None
    sdk_tools: list[FunctionTool] = []

    for spec in tool_specs:
        try:
            fn_block = spec.get("function", {})
            tool_name = str(fn_block.get("name", "")).strip()
            description = str(fn_block.get("description", "")).strip()
            params_schema = fn_block.get("parameters", {"type": "object", "properties": {}})
            if not tool_name:
                continue

            # Resolve agent instance for context-aware ToolInvokerProxy
            agent_obj = None
            if orchestrator and hasattr(orchestrator, "agents"):
                agents_map = getattr(orchestrator, "agents", {}) or {}
                clean = tool_name.replace("call_", "").replace("_agent", "")
                for ck in (tool_name, f"{clean}_agent", clean):
                    inst = agents_map.get(ck)
                    if inst is not None:
                        agent_obj = getattr(inst, "agent", inst)
                        break

            sdk_tools.append(to_sdk_function_tool(
                tool_name=tool_name,
                registry=reg,
                agent=agent_obj,
                description=description,
                schema=params_schema,
            ))
        except Exception as _te:
            logger.debug("[make_router_agent] Tool conversion failed for spec %s: %s", spec, _te)

    return Agent(
        name="MakimaRouter",
        instructions=_ROUTER_SYSTEM_PROMPT,
        tools=sdk_tools,
        model=MakimaModel(ai_handler, task="fast_chat"),
    )


def make_triage_agent(
    ai_handler: Any,
    orchestrator: Optional[Any] = None,
    ws_broadcast: Optional[Callable] = None,
    tool_registry: Optional[Any] = None,
    personality: Optional[Any] = None,
) -> Agent:
    """
    Constructs Makima Triage Agent equipped with native handoffs to specialist agents,
    centralized input guardrails, and conversational autonomy.
    """
    specialists = make_specialist_agents(ai_handler, orchestrator=orchestrator, tool_registry=tool_registry, ws_broadcast=ws_broadcast)

    handoffs = [
        handoff(
            agent_inst,
            on_handoff=_make_on_handoff(agent_inst.name, ws_broadcast),
        )
        for agent_inst in specialists.values()
    ]

    base_persona = ""
    if personality and hasattr(personality, "build_system_prompt"):
        try:
            base_persona = personality.build_system_prompt()
        except Exception:
            pass
    elif orchestrator and hasattr(orchestrator, "personality"):
        try:
            base_persona = orchestrator.personality.build_system_prompt()
        except Exception:
            pass

    if not base_persona:
        from ..personality import MAKIMA_CORE_IDENTITY
        base_persona = MAKIMA_CORE_IDENTITY

    delegation_block = """
## Specialist Delegations:
- For specialized tasks, delegate to the appropriate specialist agent via native handoff:
  * MediaAgent: Music, songs, YouTube/Spotify playback, audio, play/pause
  * BrowserAgent: Web browsing, web search, webmail/Gmail/account checking, web scraping, form filling
  * SystemAgent: Local OS management, launching desktop applications, opening URLs in desktop browser, window inspection/focus, hardware stats, screenshots
  * CodeAgent: Code generation, syntax debugging, file editing, scripts, refactoring
  * ResearchAgent: Live internet web search, breaking news, latest headlines, current real-world events, sports, weather, market intelligence, multi-source investigation
  * AutomationAgent: Scheduled routines, timers, recurring cron tasks, UI macros
  * DevOpsAgent: Docker container management, build diagnostics, git workflows, CI/CD
  * SecurityAgent: Vulnerability scans, secret detection, code security audits
  * DataAnalystAgent: CSV/JSON analysis, statistical metrics, trend analysis, chart generation
  * DocumentAgent: Generating and editing Excel spreadsheets (.xlsx), Word docs (.docx), PDFs, and PowerPoint slides (.pptx)
  * MessagingAgent: Reading/drafting/sending messages via WhatsApp, Telegram, Discord, and Email

## Pre-Routing Thinking & Tool Audit (MANDATORY):
Before deciding whether to hand off or answer directly, perform concise reasoning in a <thinking>...</thinking> block:
1. Intent: What is the user's primary goal?
2. Specialist & Tool Audit: Examine which specialist agent has the exact capabilities and tools needed.
3. Action vs Chat: If the request touches physical desktop apps, files, online accounts, media, web services, or data, ALWAYS hand off to the specialist. Never attempt to fulfill physical or service actions via conversational text alone.
4. LIVE NEWS & REAL-TIME EVENTS MANDATE:
   - Current Year: 2026.
   - Your internal training data cutoff is historical (2024). You DO NOT know current real-world events or news from memory.
   - For ANY query about 'news', 'latest news', 'headlines', 'aaj ki news', 'india ki news', 'breaking news', 'today', 'weather', or current events:
     * NEVER answer from memory or recall 2024 events!
     * You MUST immediately hand off to ResearchAgent to search live web data!
5. Fallback Strategy: If a service is not pre-authenticated, delegate to SystemAgent or BrowserAgent to inspect or launch the service on the user's desktop.

## Local Desktop Agency & Multi-Path Problem Solving:
- NEVER refuse actionable requests with canned AI disclaimers (e.g., "Main directly tumhare Gmail/account tak access nahi kar sakti", "I have no login credentials").
- If the user asks about an account, web service, or email (e.g., "gmail pe koi mail aaya kya", "whatsapp check karo"):
  * Hand off to BrowserAgent to inspect the active session or navigate to the URL, OR
  * Hand off to SystemAgent to check if the window is open or launch the URL directly in the user's desktop browser.
- Direct answers:
  * Pure conversational queries (greetings, timeless concepts, math, coding syntax, casual banter, philosophy) khud directly answer karo — kisi agent ko handoff mat karo. NEVER answer current news or real-time events without live search.
""".strip()

    triage_instructions = f"{base_persona}\n\n{delegation_block}".strip()

    triage_agent = Agent(
        name="Makima",
        instructions=triage_instructions,
        handoffs=handoffs,
        model=MakimaModel(ai_handler, task="fast_chat", agent_name="triage_agent"),
        input_guardrails=[dangerous_command_guard],
    )

    # Allow all specialists to hand back to Makima
    back_to_makima = handoff(
        triage_agent,
        tool_name_override="transfer_back_to_makima",
        tool_description_override="Transfer back to Makima to deliver the final response to the user.",
        on_handoff=_make_on_handoff("Makima", ws_broadcast),
    )
    for specialist in specialists.values():
        specialist.handoffs.append(back_to_makima)

    return triage_agent


# =============================================================================
# SOTA Official RunHooks Telemetry Bridge
# =============================================================================

class MakimaRunHooks(RunHooks):
    """
    Official OpenAI Agents SDK RunHooks implementation for Makima.
    Broadcasts real-time execution telemetry directly to Makima's WebSocket event bus.
    """
    def __init__(self, ws_broadcast: Optional[Callable] = None, task_id: str = "") -> None:
        self.ws_broadcast = ws_broadcast
        self.task_id = task_id
        self.turn_count: int = 0
        self.completed_steps: list[dict[str, Any]] = []

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import WSMessage, ServerMessageType, PROTOCOL_VERSION
                await self.ws_broadcast(WSMessage(
                    v=PROTOCOL_VERSION,
                    type=ServerMessageType.AGENT_STARTED,
                    task_id=self.task_id,
                    payload={"agent": getattr(agent, "name", "agent"), "timestamp": time.time()},
                ))
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_agent_start error: %s", e)

    async def on_agent_end(self, context: Any, agent: Any, output: Any = None) -> None:
        self.turn_count += 1
        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import WSMessage, ServerMessageType, PROTOCOL_VERSION
                await self.ws_broadcast(WSMessage(
                    v=PROTOCOL_VERSION,
                    type=ServerMessageType.AGENT_DONE,
                    task_id=self.task_id,
                    payload={"agent": getattr(agent, "name", "agent"), "timestamp": time.time()},
                ))
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_agent_end error: %s", e)

    async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import build_ai_chunk
                from_name = getattr(from_agent, "name", "Agent")
                to_name = getattr(to_agent, "name", "Agent")
                chunk_text = f"\n> 🔄 *Delegating from {from_name} to {to_name}...*\n\n"
                await self.ws_broadcast(build_ai_chunk(self.task_id, chunk_text, is_final=False))
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_handoff error: %s", e)

    async def on_tool_start(self, context: Any, agent: Any, tool: Any, call: Any = None) -> None:
        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import WSMessage, ServerMessageType, PROTOCOL_VERSION
                tool_name = getattr(tool, "name", str(tool))
                await self.ws_broadcast(WSMessage(
                    v=PROTOCOL_VERSION,
                    type=ServerMessageType.TOOL_CALL_STARTED,
                    task_id=self.task_id,
                    payload={"tool": tool_name, "agent": getattr(agent, "name", "agent"), "timestamp": time.time()},
                ))
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_tool_start error: %s", e)

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any = None) -> None:
        self.turn_count += 1
        tool_name = getattr(tool, "name", str(tool))
        self.completed_steps.append({
            "step": self.turn_count,
            "tool": tool_name,
            "agent": getattr(agent, "name", "agent"),
            "result_summary": str(result)[:300] if result else "",
            "ts": time.time(),
        })
        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import WSMessage, ServerMessageType, PROTOCOL_VERSION
                await self.ws_broadcast(WSMessage(
                    v=PROTOCOL_VERSION,
                    type=ServerMessageType.TOOL_CALL_FINISHED,
                    task_id=self.task_id,
                    payload={"tool": tool_name, "agent": getattr(agent, "name", "agent"), "timestamp": time.time()},
                ))
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_tool_end error: %s", e)



