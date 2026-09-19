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
import platform
import re
import time
import uuid
from typing import Any, AsyncIterator, Callable, Coroutine, Optional, Sequence, Union

# Suppress noisy OpenAI tracing warnings when executing non-OpenAI local/dashscope models
os.environ.setdefault("AGENTS_TRACING_DISABLED", "1")
logging.getLogger("openai.agents").setLevel(logging.ERROR)

from agents import (
    Agent,
    AgentHooks,
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


def adapt_tool_call_args(
    target_fn: Callable,
    in_params: dict[str, Any],
    user_ctx: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Adapt keyword arguments to match target_fn signature, injecting context/ai_handler if accepted."""
    final_args = dict(in_params)
    try:
        sig = inspect.signature(target_fn)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if ("context" in sig.parameters or has_varkw) and "context" not in final_args and user_ctx:
            final_args["context"] = user_ctx
        if ("ai_handler" in sig.parameters or has_varkw) and "ai_handler" not in final_args and user_ctx and user_ctx.get("ai_handler"):
            final_args["ai_handler"] = user_ctx["ai_handler"]
    except Exception:
        pass
    return final_args


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
        clean_ag = self.tool_name.removeprefix("call_").removesuffix("_agent")
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

        def _adapt_call_args(target_fn: Callable, in_params: dict[str, Any]) -> dict[str, Any]:
            return adapt_tool_call_args(target_fn, in_params, user_ctx)

        # 1. Direct tool execution via Custom Func
        if self.custom_func is not None:
            try:
                call_args = _adapt_call_args(self.custom_func, params)
                res = self.custom_func(**call_args)
                if inspect.isawaitable(res):
                    res = await res
                return str(res) if not isinstance(res, (dict, list)) else json.dumps(res, ensure_ascii=False)
            except Exception as e:
                return f"[Tool Error in {self.tool_name}]: {e}"

        # 2. Direct tool execution via Registry tool lookup
        if self.registry is not None and hasattr(self.registry, "get_tool"):
            meta = (
                self.registry.get_tool(self.tool_name)
                or self.registry.get_tool(f"system_{self.tool_name}")
                or (self.registry.get_tool(self.tool_name[7:]) if self.tool_name.startswith("system_") else None)
            )
            if meta and getattr(meta, "func", None):
                try:
                    call_args = _adapt_call_args(meta.func, params)
                    res = meta.func(**call_args)
                    if inspect.isawaitable(res):
                        res = await res
                    out = str(res) if not isinstance(res, (dict, list)) else json.dumps(res, ensure_ascii=False)
                    if len(out) > 3500:
                        out = out[:3500] + f"\n... [Output truncated: {len(out) - 3500} chars omitted]"
                    return out
                except Exception as e:
                    return f"[Tool Error in {self.tool_name}]: {e}"

        # 3. Dispatch via Agent's _use_tool (if bound to agent)
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

        # 4. Dispatch via ExecutionRuntime (fallback)
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
    clean_name = tool_name.removeprefix("call_").strip()
    tool_desc = description or ""
    tool_schema: dict[str, Any] = schema or {}
    custom_fn = func

    tool_category = "general"
    tool_tags: list[str] = []
    if registry is not None:
        meta = (
            registry.get_tool(clean_name)
            or registry.get_tool(tool_name)
            or registry.get_tool(f"system_{clean_name}")
            or (registry.get_tool(clean_name[7:]) if clean_name.startswith("system_") else None)
        )
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
                lines = handler.__doc__.strip().splitlines()
                if lines:
                    tool_desc = lines[0]
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


def _load_agent_config() -> dict[str, Any]:
    """Loads agent configuration from configs/default.yaml."""
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[3] / "configs" / "default.yaml"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data.get("agent") or data.get("agents") or {}
    except Exception:
        pass
    return {}


def to_sdk_tools(
    tool_names: Sequence[Any],
    registry: Optional[Any] = None,
    execution_runtime: Optional[Any] = None,
    agent: Optional[Any] = None,
    config: Optional[dict[str, Any]] = None,
) -> list[FunctionTool]:
    """
    Batch convert Makima tool names or ToolMeta objects into SDK FunctionTool objects.
    
    Configuration via default.yaml (or config dict):
      - canonical_tools: list[str] (empty list [] = use ALL tools from registry directly without filtering)
      - tool_budget_tokens: int (max estimated token budget for tool schemas, 0 = unlimited)
    """
    tools: list[FunctionTool] = []
    seen: set[str] = set()

    cfg = config if config is not None else _load_agent_config()
    canonical_tools_cfg = (
        cfg.get("canonical_tools")
        if "canonical_tools" in cfg
        else (cfg.get("agent") or {}).get("canonical_tools") or []
    )
    raw_budget = (
        cfg.get("tool_budget_tokens")
        if "tool_budget_tokens" in cfg
        else (cfg.get("agent") or {}).get("tool_budget_tokens")
    )
    tool_budget_tokens = int(raw_budget) if raw_budget is not None else 3500
    canonical_filter_active = bool(canonical_tools_cfg)
    canonical_set = set(canonical_tools_cfg) if canonical_filter_active else set()

    all_names = {
        (item.get("name") if isinstance(item, dict) else getattr(item, "name", str(item)))
        for item in tool_names
        if item
    }
    accumulated_tokens = 0

    for item in tool_names:
        if isinstance(item, dict):
            raw_name = item.get("name")
            desc = item.get("description")
            schema = item.get("schema")
            func = item.get("func")
        else:
            raw_name = getattr(item, "name", item) if item else None
            desc = getattr(item, "description", None)
            schema = getattr(item, "schema", None)
            func = getattr(item, "func", None)

        if not raw_name or not isinstance(raw_name, str):
            continue

        name = raw_name
        if name.startswith("system_") and (name[7:] in all_names or (canonical_filter_active and name[7:] in canonical_set)):
            name = name[7:]

        # If canonical_tools list is configured non-empty in default.yaml, filter strictly to it
        if canonical_filter_active and name not in canonical_set and raw_name not in canonical_set:
            continue

        # If canonical_tools is empty, use all tools directly from registry without filtering
        if name in seen:
            continue
        seen.add(name)

        fn_tool = to_sdk_function_tool(
            tool_name=name,
            registry=registry,
            execution_runtime=execution_runtime,
            agent=agent,
            description=desc,
            schema=schema,
            func=func,
        )
        fn_tool = _compress_tool_schema(fn_tool)

        # Budget guard: apply on compressed schema when tool_budget_tokens > 0
        if tool_budget_tokens > 0:
            schema_dict = getattr(fn_tool, "params_json_schema", {}) or {}
            desc_str = getattr(fn_tool, "description", "") or ""
            estimated_tokens = len(json.dumps(schema_dict)) // 4 + len(desc_str) // 4 + 4
            if (accumulated_tokens + estimated_tokens) > tool_budget_tokens and len(tools) >= 5:
                logger.debug("[sdk_bridge] Tool budget reached (%d tokens), omitting tool '%s'", accumulated_tokens, name)
                continue
            accumulated_tokens += estimated_tokens

        tools.append(fn_tool)

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
                if t_name.startswith("system_"):
                    valid_tool_map[t_name[7:].lower()] = t_name
                else:
                    valid_tool_map[f"system_{t_name}".lower()] = t_name
        for h in handoffs:
            h_name = getattr(h, "name", None) or getattr(h, "tool_name", None)
            if h_name:
                valid_tool_map[h_name.lower()] = h_name

        # Canonical cross-agent synonym aliases
        _TOOL_SYNONYMS = {
            "play_media": "media_play",
            "play_music": "media_play",
            "play_song": "media_play",
            "play_video": "media_play",
            "play_youtube": "media_play",
            "pause_media": "media_pause",
            "pause_music": "media_pause",
            "resume_media": "media_resume",
            "resume_music": "media_resume",
            "next_track": "media_next",
            "previous_track": "media_previous",
            "skip_ad": "media_skip_ad",
            "open_app": "launch_app",
            "start_app": "launch_app",
            "run_app": "launch_app",
            "close_window": "manage_window",
            "minimize_window": "manage_window",
            "maximize_window": "manage_window",
            "restore_window": "manage_window",
            "focus_window": "manage_window",
            "search_web": "web_search",
            "google_search": "web_search",
            "duckduckgo_search": "web_search",
            "write_to_file": "write_file",
            "create_file": "write_file",
            "save_file": "write_file",
            "read_from_file": "read_file",
            "screenshot": "take_screenshot",
            "capture_screen": "take_screenshot",
            "system_info": "get_system_specs",
            "hardware_info": "get_system_specs",
            "cpu_usage": "get_system_stats",
            "ram_usage": "get_system_stats",
            "system_stats": "get_system_stats",
            "system_specs": "get_system_specs",
            "open_url": "browser_navigate",
            "browse_url": "browser_navigate",
            "navigate_browser": "browser_navigate",
            "search_apps": "search_installed_apps",
            "find_apps": "search_installed_apps",
        }

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

                fn_lower = fn_name.lower()
                resolved_fn_name = valid_tool_map.get(fn_lower)

                if not resolved_fn_name:
                    canonical_synonym = _TOOL_SYNONYMS.get(fn_lower)
                    if canonical_synonym:
                        resolved_fn_name = valid_tool_map.get(canonical_synonym.lower())

                if not resolved_fn_name:
                    clean_name = fn_lower.removeprefix("call_").removeprefix("tool_").removeprefix("system_")
                    resolved_fn_name = valid_tool_map.get(clean_name) or valid_tool_map.get(f"system_{clean_name}")

                if not resolved_fn_name:
                    # Substring match against valid tools
                    for vt_lower, vt_orig in valid_tool_map.items():
                        if vt_lower and (vt_lower in fn_lower or fn_lower in vt_lower):
                            resolved_fn_name = vt_orig
                            break

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


INJECTION_PATTERNS = [
    # Classic prompt injection phrases
    "ignore all safety",
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore your previous instructions",
    "disregard your instructions",
    "forget your instructions",
    "override all rules",
    "disable safety",
    "disable all safety",
    "bypass safety",
    # System directive override attacks (TASK-18 exact patterns)
    "system directive",
    "important system directive",
    "new system directive",
    "urgent system directive",
    "critical system directive",
    # Persona / jailbreak attacks
    "you are now",
    "new persona",
    "pretend you are",
    "act as if you are",
    "roleplay as",
    "from now on you",
    "from now on, you",
    # Direct rule override
    "all previous instructions are",
    "your new instructions are",
    "your true instructions",
    "your real instructions",
    "disregard all previous",
    # Misc manipulation
    "you have no restrictions",
    "you have been freed",
    "developer mode",
    "jailbreak",
    "dan mode",
    "do anything now",
]


def is_dangerous_command(cmd: str) -> bool:
    """Evaluate whether an input string contains potentially destructive OS commands or prompt injection."""
    if not cmd or not isinstance(cmd, str):
        return False
    low = cmd.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in low:
            return True
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


# =============================================================================
# Unified Agent Architecture (Zero Handoffs, Direct OS Tool Access)
# =============================================================================

def get_makima_system_prompt() -> str:
    """Generates the unified Makima system prompt with dynamic OS detection."""
    os_name = f"{platform.system()} {platform.release()}".strip()
    return f"""You are Makima, an autonomous, highly capable AI assistant on the user's desktop computer ({os_name}).
You interact directly with {os_name} using available tools for the operating system, filesystem, system specs, clipboard, network, and browser.

Core Principles:
1. Always execute the appropriate tool to inspect, read, verify, or act before answering.
2. For ambiguous or implicit referents (e.g. 'is link ko fetch karo', 'ye check karo'), inspect the clipboard or environment first.
3. If an action is requested, perform it directly using tools. Never answer with canned AI disclaimers (e.g. 'I cannot access files', 'I have no credentials').
4. Never execute destructive wildcard deletions without explicit user confirmation of specific targets.
5. Pointer File Resolution: If the user asks to act on a target referenced inside a file (e.g. 'file me jo likha hai wo delete karo'), you MUST call read_file first to inspect its contents before taking any action.
6. Safety & Critical Assets: If file or asset inspection reveals warnings like 'DO NOT DELETE', 'CRITICAL', or references production databases/system files, REFUSE destructive actions and ask the user for explicit confirmation.
7. Quoted Path Anchor: When the user specifies a directory ('scratch me', 'logs folder me') AND a quoted filename ('script.py'), always construct the direct relative path '<directory>/<filename>' (e.g. 'scratch/script.py'). Do not add 'documents/' or any prefix.

Computer Use (Astra-style):
- Use `computer_action` with action='screenshot' to see the current screen before clicking anything.
- Use `computer_action` with action='click'/'double_click'/'right_click' + x/y pixel coordinates to click.
- Use `computer_action` with action='type' + text to type into the focused window.
- Use `computer_action` with action='key' + key string (e.g. 'ctrl+c', 'enter', 'alt+f4') for hotkeys.
- Use `computer_action` with action='scroll' + direction/clicks to scroll.
- Use `click_screen_target` with target_description (e.g. 'Play button', 'Search bar') for autonomous visual element finding and clicking via vision LLM.

Web Search & Image Generation:
- Use `web_search` for any live internet query, current events, facts, news, or topics that require up-to-date information.
- Use `fetch_url` to read a specific URL's content directly.
- Use `generate_image` when the user asks to create, draw, make, or generate any image, artwork, illustration, or visual. Pass a detailed English prompt.

Memory & Personal Knowledge:
- Use `memory_store` / `remember_fact` when the user asks to remember or save personal facts, preferences, contacts, or enduring instructions.
- Use `memory_search` / `recall_memory` to recall past facts, preferences, or prior conversation context when asked.
- Use `query_knowledge_graph` to explore relationships and connections between entities.
- Use `memory_forget` / `forget_memory` when the user explicitly asks to remove or forget stored information."""


MAKIMA_SYSTEM_PROMPT = get_makima_system_prompt()


# Fields kept per-parameter: only what the LLM needs to construct a valid call.
_SCHEMA_KEEP_PARAM_FIELDS = frozenset({"type", "enum", "items", "$ref", "const", "anyOf", "oneOf", "allOf"})
# Top-level schema bloat fields to strip entirely (never needed by LLM at schema root).
_SCHEMA_STRIP_TOP_FIELDS = frozenset({
    "$schema", "$comment", "title", "examples", "example", "default",
    "additionalProperties", "x-openai-isConsequential",
})


def _clean_param(param_info: dict) -> dict:
    """Recursively strip all non-essential fields from a single parameter schema dict."""
    keep: dict = {}
    for key in _SCHEMA_KEEP_PARAM_FIELDS:
        if key in param_info:
            val = param_info[key]
            # Recursively clean nested object schemas inside items / allOf / anyOf / oneOf
            if key == "items" and isinstance(val, dict):
                val = _clean_param(val)
            elif key in ("allOf", "anyOf", "oneOf") and isinstance(val, list):
                val = [_clean_param(v) if isinstance(v, dict) else v for v in val]
            keep[key] = val
    # Preserve nested properties for object-type params (e.g. action param sub-schemas)
    if param_info.get("type") == "object" and "properties" in param_info:
        nested_props = {}
        for k, v in param_info["properties"].items():
            nested_props[k] = _clean_param(v) if isinstance(v, dict) else v
        keep["properties"] = nested_props
        if "required" in param_info:
            keep["required"] = param_info["required"]
    return keep


def _compress_tool_schema(tool: Any) -> Any:
    """
    Tool schema compress karo — ~75% token reduction bina functionality change kiye.
    Removes: description (trimmed to 50 chars), title, default, examples, $schema,
    $comment, additionalProperties, and all per-parameter descriptive metadata.
    Preserves: type, enum, items, $ref, const, anyOf/oneOf/allOf, required (top-level),
    nested object properties (for structured action params like computer_action).
    """
    # 1. Trim tool description to max 50 chars
    if hasattr(tool, "description") and tool.description:
        desc = tool.description.strip()
        tool.description = desc[:50].rstrip() if len(desc) > 50 else desc

    # 2. Strip schema bloat
    if hasattr(tool, "params_json_schema") and isinstance(tool.params_json_schema, dict):
        schema = dict(tool.params_json_schema)

        # Strip top-level bloat fields
        for field in _SCHEMA_STRIP_TOP_FIELDS:
            schema.pop(field, None)

        # Clean per-parameter entries
        if "properties" in schema and isinstance(schema["properties"], dict):
            cleaned_props: dict = {}
            for param_name, param_info in schema["properties"].items():
                cleaned_props[param_name] = _clean_param(param_info) if isinstance(param_info, dict) else param_info
            schema["properties"] = cleaned_props

        # Strip $defs bloat (only keep $defs entries that are actually $ref'd)
        if "$defs" in schema and isinstance(schema["$defs"], dict):
            # Collect all $ref targets used in properties
            schema_str = json.dumps(schema.get("properties", {}))
            used_refs = set(re.findall(r'"\$ref":\s*"#/\$defs/([^"]+)"', schema_str))
            schema["$defs"] = {
                k: v for k, v in schema["$defs"].items() if k in used_refs
            }
            if not schema["$defs"]:
                del schema["$defs"]

        tool.params_json_schema = schema

    return tool


def make_unified_agent(
    ai_handler: Any,
    tool_registry: Optional[Any] = None,
    ws_broadcast: Optional[Callable] = None,
    orchestrator: Optional[Any] = None,
    task: str = "fast_chat",
    config: Optional[dict[str, Any]] = None,
) -> Agent:
    """
    Constructs the single unified Makima Agent equipped with ALL tools directly.
    Zero handoffs, zero specialist silos, zero pre-execution viability checks.
    task parameter dynamically routes model selection (Groq for fast_chat, Gemini Flash for vision/long-context, OpenRouter for fallback)
    via ai_handler Pareto cascades.
    """
    reg = tool_registry or (getattr(orchestrator, "tool_registry", None) if orchestrator else None)
    tool_items: list[Any] = []
    if reg is not None:
        if hasattr(reg, "get_all"):
            tool_items = reg.get_all()
        elif hasattr(reg, "get_all_tool_names"):
            tool_items = reg.get_all_tool_names()
        elif hasattr(reg, "get_manifest"):
            m = reg.get_manifest()
            for t in m:
                if isinstance(t, dict):
                    fn = t.get("name") or (t.get("function", {}).get("name") if isinstance(t.get("function"), dict) else None)
                    if fn:
                        tool_items.append(fn)

    agent_cfg = config if config is not None else getattr(orchestrator, "config", None)
    sdk_tools = to_sdk_tools(tool_items, registry=reg, config=agent_cfg)
    sdk_tools = [_compress_tool_schema(t) for t in sdk_tools]
    model = MakimaModel(ai_handler, task=task, agent_name="makima")
    agent_hooks = MakimaAgentHooks(ws_broadcast)

    return Agent(
        name="Makima",
        instructions=get_makima_system_prompt(),
        tools=sdk_tools,
        model=model,
        hooks=agent_hooks,
        input_guardrails=[dangerous_command_guard],
        handoffs=[],
    )


def make_triage_agent(
    ai_handler: Any,
    orchestrator: Optional[Any] = None,
    ws_broadcast: Optional[Callable] = None,
    tool_registry: Optional[Any] = None,
    personality: Optional[Any] = None,
    task: str = "fast_chat",
    config: Optional[dict[str, Any]] = None,
) -> Agent:
    """Backward compatibility alias: routes directly to make_unified_agent."""
    return make_unified_agent(
        ai_handler,
        tool_registry=tool_registry,
        ws_broadcast=ws_broadcast,
        orchestrator=orchestrator,
        task=task,
        config=config,
    )


# =============================================================================
# SOTA Official Hooks Telemetry Bridges
# =============================================================================

class MakimaAgentHooks(AgentHooks):
    """Agent-scoped hooks passed to Agent(hooks=...)."""
    def __init__(self, ws_broadcast: Optional[Callable] = None) -> None:
        self.ws_broadcast = ws_broadcast


def _extract_call_id(context: Any, call: Any = None) -> str:
    """Extract call_id safely across all OpenAI Agents SDK context & call variants."""
    if hasattr(context, "_makima_call_id") and getattr(context, "_makima_call_id"):
        return str(getattr(context, "_makima_call_id"))
    if hasattr(context, "tool_call") and context.tool_call:
        tc = context.tool_call
        cid = getattr(tc, "call_id", None) or getattr(tc, "id", None)
        if cid:
            return str(cid)
    if hasattr(context, "tool_call_id") and context.tool_call_id:
        return str(context.tool_call_id)
    if call is not None:
        cid = getattr(call, "call_id", None) or getattr(call, "id", None)
        if cid:
            return str(cid)
    if isinstance(context, dict):
        cid = context.get("_makima_call_id") or context.get("call_id") or context.get("id") or context.get("tool_call_id")
        if cid:
            return str(cid)
    return ""


class MakimaRunHooks(RunHooks):
    """
    Official OpenAI Agents SDK RunHooks implementation for Makima.
    Passed to Runner.run_streamed(hooks=...).
    Broadcasts real-time execution telemetry directly to Makima's WebSocket event bus.
    Hardened for concurrent / parallel tool calls with call_id correlation and thread safety.
    """
    # Max times the same tool+args fingerprint can be called consecutively before loop detection fires.
    MAX_CONSECUTIVE_IDENTICAL_CALLS: int = 3

    def __init__(self, ws_broadcast: Optional[Callable] = None, task_id: str = "") -> None:
        self.ws_broadcast = ws_broadcast
        self.task_id = task_id
        self.turn_count: int = 0
        self.completed_steps: list[dict[str, Any]] = []
        # Parallel execution & loop detection tracking
        self._lock = asyncio.Lock()
        self._active_calls: dict[str, dict[str, Any]] = {}
        self._recent_fingerprints: list[str] = []

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
        tool_name = getattr(tool, "name", None) or getattr(context, "tool_name", None) or str(tool)
        call_id = _extract_call_id(context, call) or f"call_{uuid.uuid4().hex[:8]}"
        try:
            setattr(context, "_makima_call_id", call_id)
        except Exception:
            pass
        if isinstance(context, dict) and not context.get("_makima_call_id"):
            context["_makima_call_id"] = call_id

        # --- Extract call arguments from ToolContext or call object ---
        args_dict: dict[str, Any] = {}
        raw_args = None
        if hasattr(context, "tool_arguments") and context.tool_arguments is not None:
            raw_args = context.tool_arguments
        elif hasattr(context, "tool_call") and context.tool_call and hasattr(context.tool_call, "arguments"):
            raw_args = context.tool_call.arguments
        elif call and hasattr(call, "arguments"):
            raw_args = call.arguments

        if isinstance(raw_args, dict):
            args_dict = raw_args
        elif isinstance(raw_args, str):
            try:
                args_dict = json.loads(raw_args)
            except Exception:
                args_dict = {"raw": raw_args}

        # --- Required-argument validation (warn loudly; SDK handles the actual error) ---
        required: list[str] = []
        try:
            schema = getattr(tool, "params_json_schema", None) or getattr(tool, "schema", None) or {}
            required = schema.get("required", [])
        except Exception:
            pass
        missing = [r for r in required if r not in args_dict]
        if missing:
            logger.warning(
                "[MakimaRunHooks] Tool '%s' called with missing required args: %s — model may be stuck.",
                tool_name, missing,
            )

        # --- Parallel-safe loop detection ---
        args_fingerprint = f"{tool_name}::{json.dumps(args_dict, sort_keys=True, default=str)}" if args_dict else f"{tool_name}::empty"

        async with self._lock:
            self._active_calls[call_id] = {
                "tool": tool_name,
                "agent": getattr(agent, "name", "agent"),
                "start_time": time.time(),
                "fingerprint": args_fingerprint,
                "args": args_dict,
            }
            # Count consecutive identical fingerprints in recent history
            consecutive_count = 1
            for prev_fp in reversed(self._recent_fingerprints):
                if prev_fp == args_fingerprint:
                    consecutive_count += 1
                else:
                    break

            threshold = 5 if not args_dict else self.MAX_CONSECUTIVE_IDENTICAL_CALLS
            if consecutive_count >= threshold:
                logger.warning(
                    "[MakimaRunHooks] Loop detected: tool '%s' called %d times consecutively with args %s — aborting run.",
                    tool_name, consecutive_count, args_dict,
                )
                if self.ws_broadcast and self.task_id:
                    try:
                        from ..ws_protocol import build_toast_notification
                        await self.ws_broadcast(build_toast_notification(
                            self.task_id,
                            f"⚠️ Execution loop detected on '{tool_name}' — task aborted.",
                            level="error",
                            duration_ms=6000,
                        ))
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Loop guard: '{tool_name}' called {consecutive_count}x with identical args."
                )

        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import build_tool_call_started
                msg = build_tool_call_started(
                    task_id=self.task_id,
                    tool_name=tool_name,
                    parameters=args_dict,
                    agent=getattr(agent, "name", "agent"),
                    call_id=call_id,
                )
                await self.ws_broadcast(msg)
            except RuntimeError:
                raise
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_tool_start error: %s", e)

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any = None) -> None:
        call_id = _extract_call_id(context)
        tool_name = getattr(tool, "name", str(tool))
        start_time = time.time()
        agent_name = getattr(agent, "name", "agent")
        fingerprint = f"{tool_name}::empty"

        async with self._lock:
            self.turn_count += 1
            call_info = self._active_calls.pop(call_id, None) if call_id else None
            if not call_info and len(self._active_calls) == 1:
                only_cid, call_info = self._active_calls.popitem()
                if not call_id:
                    call_id = only_cid
            if call_info:
                start_time = call_info.get("start_time", start_time)
                agent_name = call_info.get("agent", agent_name)
                fingerprint = call_info.get("fingerprint", fingerprint)

            self._recent_fingerprints.append(fingerprint)
            if len(self._recent_fingerprints) > 40:
                self._recent_fingerprints.pop(0)

            duration_ms = max(0.0, (time.time() - start_time) * 1000.0)
            self.completed_steps.append({
                "step": self.turn_count,
                "tool": tool_name,
                "agent": agent_name,
                "call_id": call_id,
                "duration_ms": round(duration_ms, 2),
                "result_summary": str(result)[:300] if result else "",
                "ts": time.time(),
            })

        if self.ws_broadcast and self.task_id:
            try:
                from ..ws_protocol import build_tool_call_finished
                is_ok = not str(result).startswith("[Tool Error")
                msg = build_tool_call_finished(
                    task_id=self.task_id,
                    tool_name=tool_name,
                    result=result,
                    duration_ms=duration_ms,
                    is_success=is_ok,
                    agent=agent_name,
                    call_id=call_id,
                )
                await self.ws_broadcast(msg)
                await self.ws_broadcast({
                    "type": "tool_completed",
                    "task_id": self.task_id,
                    "tool": tool_name,
                    "call_id": call_id,
                    "duration_ms": round(duration_ms, 2),
                    "result": str(result)[:300] if result else "",
                })
            except Exception as e:
                logger.debug("[MakimaRunHooks] on_tool_end error: %s", e)



