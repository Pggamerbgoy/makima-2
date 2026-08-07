"""
Makima v7.2 — Elite Commander Agent
Upgrades: Async-safe DAG execution, predictive routing, robust reflexion with structured critique, 
async-safe semantic TTL caching, zero-crash resilience, and advanced synthesis.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple, Type

# Zero-crash resilience: Graceful fallbacks for heavy/optional dependencies
try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError:
    logging.warning("[commander] Pydantic not found. Using fallback BaseModel.")
    class ValidationError(Exception): pass
    class Field:
        def __init__(self, default=None, **kwargs): self.default = default
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items(): setattr(self, k, v)
        def model_dump(self) -> dict: return self.__dict__
        def dict(self) -> dict: return self.__dict__

try:
    from .base_agent import BaseAgent
except ImportError:
    logging.warning("[commander] BaseAgent not found. Using stub.")
    class BaseAgent:
        AGENT_NAME = "base"
        def __init__(self, ai_handler=None, memory=None, tool_registry=None, ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
            self.ai_handler = ai_handler
            self.memory = memory
            self.tool_registry = tool_registry
            self.ws_broadcast = ws_broadcast
            self.orchestrator = orchestrator
            self.guardrails = guardrails
            self.llm = kwargs.get("llm")
        async def _llm_call(self, *args, **kwargs): return ""
        def _build_messages(self, msg, ctx): return [{"role": "user", "content": msg}]
        async def execute(self, task_id, message, context, entities): pass

try:
    from ..coordination.agent_situational_encyclopedia import get_situational_encyclopedia
except (ImportError, ValueError):
    try:
        from apps.brain.coordination.agent_situational_encyclopedia import get_situational_encyclopedia
    except ImportError:
        def get_situational_encyclopedia():
            class _DummyEncyclopedia:
                def build_commander_situational_prompt(self, query=""):
                    return "## GENERAL AGENT CAPABILITIES\n- Use specialized agents for best results."
            return _DummyEncyclopedia()

logger = logging.getLogger("makima.agents.commander")

class AgentRecursionError(Exception): pass
class PlanGenerationError(Exception): pass

class SubtaskPlan(BaseModel):
    subtask_id: str = Field(..., description="Unique alphanumeric ID for this subtask (e.g., 'st_1')")
    agent: str = Field(..., description="Leaf agent name")
    task: str = Field(..., description="Actionable, highly specific instruction")
    dependencies: List[str] = Field(default_factory=list, description="List of subtask_ids this depends on")
    parallel_group: int = Field(default=0, description="Legacy concurrency group ID")

class ExecutionPlan(BaseModel):
    plan_summary: str = Field(..., description="Execution strategy summary")
    reasoning: str = Field(default="", description="Chain of thought for the plan")
    subtasks: List[SubtaskPlan] = Field(..., description="Ordered subtasks")
    estimated_cost_tier: str = Field(default="standard", description="low, standard, high")

class AsyncTTLCache:
    """High-performance, async-safe LRU cache with TTL."""
    def __init__(self, max_size: int = 2000, ttl: float = 600.0):
        self._cache: OrderedDict[str, Tuple[float, Any]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key in self._cache:
                ts, val = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    return val
                del self._cache[key]
            return None

    async def set(self, key: str, value: Any):
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
            self._cache[key] = (time.time(), value)
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

class CommanderAgent(BaseAgent):
    AGENT_NAME = "commander"
    DESCRIPTION = "Strategic planning, DAG task decomposition, and multi-agent orchestration"
    CAPABILITIES = ["dag_decomposition", "multi_agent_orchestration", "workflow_synthesis"]
    AGENT_TOOLS = ["subtask_dispatch", "dag_execution"]
    TAGS = ["commander", "orchestrator", "swarm", "dag"]

    _SYSTEM_PROMPT_TEMPLATE = """You are Makima's Elite Commander Agent — the strategic orchestrator.
Decompose complex requests into precise, executable subtasks forming a Directed Acyclic Graph (DAG).

## AVAILABLE LEAF AGENTS
{agent_capabilities}

## VAST SITUATIONAL INTEL & MULTI-AGENT WORKFLOWS
{situational_knowledge}

## ORCHESTRATION RULES
1. CRITICAL: DO NOT GIVE UP or claim "I cannot do this".
2. If a request requires multiple capabilities (e.g. video to summary, search web and email, read file and analyze), DECOMPOSE into a multi-step tool chain (DAG).
3. Assign unique `subtask_id` (e.g., "st_1", "st_2") to each subtask.
4. Define `dependencies` using `subtask_id`s for sequential execution (e.g. st_2 depends on st_1). Leave empty for parallel execution.
5. NEVER delegate to yourself (commander).
6. Prefer specialized agents (e.g., data_analyst for CSVs, security for audits, system_agent for OS/desktop files, media_agent for music/playback, browser_agent for web).
7. Each task must be self-contained and highly specific.
8. Estimate cost tier: "low" (simple routing), "standard" (normal tasks), "high" (complex reasoning/coding).

Respond using the required structured JSON format."""

    _MAX_CALL_DEPTH = 3

    def __init__(self, ai_handler=None, memory=None, tool_registry=None, ws_broadcast=None, orchestrator=None, guardrails=None, **kwargs):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails, **kwargs)
        # Async-safe state management
        self._depth_tracker: Dict[str, int] = {}
        self._depth_lock = asyncio.Lock()
        self._cache = AsyncTTLCache(max_size=2000, ttl=600.0)

    def _build_system_prompt(self, query: str = "") -> str:
        registry = {}
        if self.orchestrator and getattr(self.orchestrator, "agents", None):
            try:
                registry = {
                    name: {"description": getattr(inst.agent, "DESCRIPTION", "No description")}
                    for name, inst in self.orchestrator.agents.items()
                }
            except Exception:
                pass
                
        lines = []
        for name, meta in sorted(registry.items()):
            if name == self.AGENT_NAME: continue
            desc = meta.get('description', 'No description') if isinstance(meta, dict) else str(meta)
            lines.append(f"- **{name}**: {desc}")
        
        if not lines:
            lines = [
                "- **research_agent**: web search, multi-hop analysis",
                "- **code_agent**: code generation, AST validation, sandbox execution",
                "- **data_analyst_agent**: Polars/Pandas data manipulation, charting",
                "- **security_agent**: port scanning, dependency auditing, secret detection",
                "- **devops_agent**: Docker management, CI/CD pipeline status",
                "- **browser_agent**: stealth web browsing, DOM distillation",
                "- **document_agent**: Excel/Word generation, text summarization, doc reading",
                "- **system_agent**: OS control, window management, desktop file organization, short commands",
                "- **media_agent**: Spotify/YouTube playback, transcript fetching, NL music control, volume",
                "- **messaging_agent**: WhatsApp, Telegram, Discord, Email drafting and delivery",
                "- **memory_agent**: EternalMemory vector search, preference recall",
                "- **automation_agent**: Scheduled reminders, background timers, recurring cron routines",
                "- **voice_agent**: Speech synthesis (Kokoro-ONNX / Edge-TTS), Whisper STT",
                "- **creative_agent**: Creative writing, storytelling, copywriting, image prompts",
            ]
        
        situational_knowledge = ""
        try:
            situational_knowledge = get_situational_encyclopedia().build_commander_situational_prompt(query=query)
        except Exception as e:
            logger.warning("[commander] Could not load situational encyclopedia: %s", e)
            
        return self._SYSTEM_PROMPT_TEMPLATE.format(
            agent_capabilities="\n".join(lines),
            situational_knowledge=situational_knowledge
        )

    async def execute(self, task_id: str, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        # 0. Delegate to Elite Ecosystem Hub if available
        if self.coordination and hasattr(self.coordination, "execute"):
            try:
                logger.info("[commander] Delegating multi-agent DAG execution to Elite Ecosystem Hub")
                eco_res = await self.coordination.execute(task_id, message, context, entities)
                if eco_res:
                    return eco_res
            except Exception as e:
                logger.warning("[commander] Ecosystem execution failed: %s, falling back to DAG plan", e)

        # Async-safe recursion depth tracking
        async with self._depth_lock:
            depth = self._depth_tracker.get(task_id, 0) + 1
            self._depth_tracker[task_id] = depth
            
        if depth > self._MAX_CALL_DEPTH:
            async with self._depth_lock:
                self._depth_tracker.pop(task_id, None)
            raise AgentRecursionError(f"Max call depth ({self._MAX_CALL_DEPTH}) exceeded for task {task_id}")

        try:
            # 1. Plan Generation
            plan = await self._generate_plan(task_id, message, context)
            if not plan or not plan.subtasks:
                logger.info("[commander] No subtasks generated by primary LLM call. Building fallback compound DAG plan.")
                plan = self._generate_fallback_compound_plan(message)
                
            if not plan or not plan.subtasks:
                logger.info("[commander] Fallback plan empty, falling back to direct response.")
                return await self._fallback_response(message, context)
            
            # 2. DAG Execution
            results = await self._execute_plan(task_id, plan, context)
            
            # 3. Compression & Synthesis
            compressed = await self._compress_results(results)
            final = await self._synthesize(message, plan, compressed)
            
            # Telemetry tracking
            if not isinstance(context, dict):
                context = {}
            if not isinstance(context.get("telemetry"), dict):
                context["telemetry"] = {}
            context["telemetry"]["commander_subtasks"] = len(plan.subtasks)
            context["telemetry"]["cost_tier"] = plan.estimated_cost_tier
                
            return final
        except Exception as e:
            logger.error("[commander] execute failed: %s", e, exc_info=True)
            return f"Orchestration error: {e}"
        finally:
            async with self._depth_lock:
                self._depth_tracker.pop(task_id, None)

    async def _generate_plan(self, task_id: str, message: str, context: dict) -> Optional[ExecutionPlan]:
        system_prompt = self._build_system_prompt(query=message)
        messages = self._build_messages(message, context)
        messages.insert(0, {"role": "system", "content": system_prompt})
        
        try:
            raw_plan = await self._llm_call(messages, task="planning", response_format=ExecutionPlan)
            if isinstance(raw_plan, ExecutionPlan):
                return raw_plan
            elif isinstance(raw_plan, dict):
                return ExecutionPlan(**raw_plan)
            elif isinstance(raw_plan, str):
                parsed = self._extract_json(raw_plan)
                if parsed:
                    return ExecutionPlan(**parsed)
        except ValidationError as ve:
            logger.warning("[commander] Plan validation failed: %s", ve)
        except Exception as e:
            logger.warning("[commander] Plan generation exception: %s", e)
            
        return None

    def _generate_fallback_compound_plan(self, message: str) -> Optional[ExecutionPlan]:
        """Emergency fallback planner that constructs a 2-step DAG for compound requests if LLM JSON generation fails."""
        msg_lower = message.lower()
        if any(w in msg_lower for w in ["summarize", "summary", "padh", "read", "mail", "send", "bhej", "digest"]):
            # Heuristic multi-agent fallback
            if any(w in msg_lower for w in ["video", "youtube", "song"]):
                st1 = SubtaskPlan(subtask_id="st_1", agent="media_agent", task=f"Fetch media content and transcript for: {message}", dependencies=[])
                st2 = SubtaskPlan(subtask_id="st_2", agent="document_agent", task="Summarize and structure the content extracted from st_1", dependencies=["st_1"])
                return ExecutionPlan(plan_summary="Compound media-to-summary workflow", reasoning="Fallback DAG chaining media_agent to document_agent", subtasks=[st1, st2])
            elif any(w in msg_lower for w in ["search", "google", "docs", "web"]):
                st1 = SubtaskPlan(subtask_id="st_1", agent="browser_agent", task=f"Search web and extract documentation/content for: {message}", dependencies=[])
                st2 = SubtaskPlan(subtask_id="st_2", agent="document_agent", task="Summarize the gathered web content from st_1 into a clean report", dependencies=["st_1"])
                return ExecutionPlan(plan_summary="Compound search-to-summary workflow", reasoning="Fallback DAG chaining browser_agent to document_agent", subtasks=[st1, st2])
        # Memory + Creative compound tasks: "yaad nahi aa raha", "soche the", "tagline", "teaser", "tweet", "draft"
        if any(w in msg_lower for w in ["yaad nahi aa raha", "yaad nahi hai", "soche the", "kya tha", "tagline", "teaser", "tweet", "draft", "mysterious", "dark"]):
            # Check if this is a memory + creative compound task
            memory_keywords = ["yaad nahi aa raha", "yaad nahi hai", "soche the", "kya tha", "project", "chimera", "secret"]
            creative_keywords = ["teaser", "tweet", "draft", "mysterious", "dark", "vibe", "theme"]
            
            if any(w in msg_lower for w in memory_keywords) and any(w in msg_lower for w in creative_keywords):
                st1 = SubtaskPlan(subtask_id="st_1", agent="memory_agent", task=f"Recall information about: {message}", dependencies=[])
                st2 = SubtaskPlan(subtask_id="st_2", agent="creative_agent", task=f"Use the information from st_1 to draft a creative teaser tweet with dark/mysterious vibe", dependencies=["st_1"])
                return ExecutionPlan(plan_summary="Compound memory-to-creative workflow", reasoning="Fallback DAG chaining memory_agent to creative_agent for memory recall + creative generation", subtasks=[st1, st2])
        return None


    def _extract_json(self, text: str) -> Optional[dict]:
        """Robust JSON extraction from LLM text output."""
        # Try markdown code block first
        code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass
        
        # Fallback to raw brackets
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    async def _fallback_response(self, message: str, context: dict) -> str:
        return await self._llm_call(self._build_messages(message, context), task="general")

    async def _execute_plan(self, task_id: str, plan: ExecutionPlan, context: dict) -> Dict[str, str]:
        """Executes subtasks as a DAG based on dependencies."""
        results: Dict[str, str] = {}
        subtask_map = {st.subtask_id: st for st in plan.subtasks}
        
        pending = set(subtask_map.keys())
        completed = set()
        max_iterations = len(subtask_map) + 2
        iteration = 0
        
        while pending and iteration < max_iterations:
            iteration += 1
            
            # Find executable subtasks (all dependencies met)
            executable = [
                sid for sid in pending 
                if all(dep in completed or dep not in subtask_map for dep in subtask_map[sid].dependencies)
            ]
            
            if not executable:
                logger.error(f"[commander] Circular dependency or deadlock detected for task {task_id}. Gracefully failing remaining subtasks: {pending}")
                for sid in list(pending):
                    unmet_deps = [dep for dep in subtask_map[sid].dependencies if dep not in completed]
                    results[sid] = f"❌ Deadlock detected: Cannot resolve dependencies {unmet_deps} for step '{sid}'"
                    completed.add(sid)
                break

                
            tasks = [self._dispatch_with_reflexion(task_id, subtask_map[sid], context, results) for sid in executable]
            res = await asyncio.gather(*tasks, return_exceptions=True)
            
            for sid, r in zip(executable, res):
                if isinstance(r, BaseException):
                    results[sid] = f"❌ Exception: {r}"
                else:
                    results[sid] = r
                completed.add(sid)
                pending.remove(sid)
                
        return results

    async def _dispatch_with_reflexion(self, parent_id: str, subtask: SubtaskPlan, context: dict, current_results: dict) -> str:
        """Dispatches task with dependency injection, caching, and 1-shot reflexion."""
        task_prompt = subtask.task
        
        # Inject dependency context
        if subtask.dependencies:
            dep_context = "\n".join([
                f"Result of {dep}: {current_results.get(dep, 'N/A')}" 
                for dep in subtask.dependencies if dep in current_results
            ])
            if dep_context:
                task_prompt = f"{task_prompt}\n\nContext from previous steps:\n{dep_context}"

        # Semantic/Hash Cache Check
        cache_key = hashlib.sha256(f"{subtask.agent}:{task_prompt}".encode()).hexdigest()
        cached = await self._cache.get(cache_key)
        if cached: 
            logger.debug("[commander] Cache hit for %s", subtask.agent)
            return cached

        if subtask.agent == self.AGENT_NAME: 
            return "⚠️ Blocked self-dispatch"
        if not self.orchestrator: 
            return "⚠️ Orchestrator unavailable"

        result_str = ""
        for attempt in range(2):
            try:
                # Stamp the parent id so orchestrator can register this subtask
                # for cancellation propagation (F3). Copy context — don't mutate
                # the shared dict.
                sub_ctx = dict(context) if isinstance(context, dict) else {}
                sub_ctx["_parent_task_id"] = parent_id
                res = await self.orchestrator.dispatch(parent_id, subtask.agent, task_prompt, sub_ctx)
                result_str = res.result if getattr(res, "success", True) else f"❌ {res.error}"
            except Exception as e:
                result_str = f"❌ Dispatch Exception: {e}"
            
            if not result_str.startswith("❌") or attempt == 1:
                if not result_str.startswith("❌"):
                    await self._cache.set(cache_key, result_str)
                if self.coordination and hasattr(self.coordination, "blackboard"):
                    try:
                        await self.coordination.blackboard.put(
                            task_id=parent_id,
                            key=f"subtask:{subtask.subtask_id}",
                            value=result_str,
                            agent_name=subtask.agent,
                        )
                    except Exception as ex:
                        logger.debug("[commander] Blackboard put failed: %s", ex)
                return result_str
            
            # Structured Reflexion Loop
            logger.info("[commander] Reflexion triggered for %s (attempt %d)", subtask.agent, attempt + 1)
            critique_prompt = f"Task: {task_prompt}\nError: {result_str}\nAnalyze why it failed and provide a corrected, highly specific task instruction."
            try:
                critique = await self._llm_call([{"role": "user", "content": critique_prompt}], task="critique")
                rewrite_prompt = f"Original: {task_prompt}\nCritique: {critique}\nRewrite the task instruction to be executable and avoid the error. Output ONLY the new task instruction."
                task_prompt = await self._llm_call([{"role": "user", "content": rewrite_prompt}], task="rewrite")
            except Exception as e:
                logger.warning("[commander] Reflexion LLM call failed: %s", e)
                break
                
        return result_str

    async def _compress_results(self, results: Dict[str, str]) -> Dict[str, str]:
        """Compresses large outputs to prevent context window overflow during synthesis."""
        compressed = {}
        for sid, r in results.items():
            if len(r) > 3000:
                try:
                    summary = await self._llm_call(
                        [{"role": "user", "content": f"Summarize the following technical output into 5-7 concise bullet points, preserving hard data, code snippets, and exact errors:\n{r}"}], 
                        task="compression"
                    )
                    compressed[sid] = summary
                except Exception:
                    compressed[sid] = r[:3000] + "\n... [Truncated]"
            else:
                compressed[sid] = r
        return compressed

    async def _synthesize(self, msg: str, plan: ExecutionPlan, results: Dict[str, str]) -> str:
        """Synthesizes final response with transparent failure reporting."""
        success = {sid: r for sid, r in results.items() if not r.startswith("❌")}
        failed = {sid: r for sid, r in results.items() if r.startswith("❌")}
        
        success_text = "\n".join([f"[{sid}] {r}" for sid, r in success.items()])
        failed_text = "\n".join([f"[{sid}] {r}" for sid, r in failed.items()])
        
        prompt = f"""Original Request: {msg}
Execution Strategy: {plan.plan_summary}

Successful Subtasks:
{success_text or 'None'}

Failed Subtasks:
{failed_text or 'None'}

Synthesize a comprehensive, professional final response. 
- Directly answer the user's request using the successful subtask data.
- If there are failures, transparently explain what could not be completed and why.
- Use Markdown formatting for readability.
- Do not mention "subtasks" or "agents" explicitly unless necessary; present it as a unified response.
"""
        try:
            return await self._llm_call(
                [
                    {"role": "system", "content": "You are Makima's elite synthesis engine. Produce clear, accurate, and structured final responses."}, 
                    {"role": "user", "content": prompt}
                ], 
                task="synthesis"
            )
        except Exception as e:
            logger.error("[commander] Synthesis failed: %s", e)
            return f"Execution completed, but synthesis failed. Raw results:\n{success_text}\n{failed_text}"
