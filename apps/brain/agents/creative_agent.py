"""Makima v7.2 Elite Creative Design & Visual Asset Orchestration Engine."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Optional, Dict, List, Tuple

try:
    import yaml
except ImportError:
    yaml = None

try:
    from pydantic import BaseModel, ValidationError
except ImportError:
    BaseModel = object  # type: ignore
    ValidationError = Exception  # type: ignore

try:
    import tiktoken
except ImportError:
    tiktoken = None

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.creative")

class CreativeFormat(Enum):
    UI_MOCKUP = auto()
    DIAGRAM = auto()
    VISUAL_PROMPT = auto()
    STYLED_MARKDOWN = auto()
    HAIKU = auto()
    POEM = auto()
    SCREENPLAY = auto()
    SONG = auto()
    SOCIAL_POST = auto()
    ESSAY = auto()
    SHORT_STORY = auto()
    GENERAL = auto()

@dataclass
class CreativeConstraints:
    max_tokens: Optional[int] = None
    target_words: Optional[int] = None
    aspect_ratio: str = "16:9"
    color_palette: Optional[List[str]] = None
    ui_framework: str = "tailwind"
    is_revision: bool = False
    detected_format: CreativeFormat = CreativeFormat.GENERAL

@dataclass
class FormatConfig:
    guidance: str
    temperature: float
    top_p: float
    addendum: str

_PATTERNS: List[Tuple[CreativeFormat, re.Pattern]] = [
    (CreativeFormat.UI_MOCKUP, re.compile(r"\b(ui|wireframe|mockup|layout|dashboard|frontend|tailwind)\b", re.I)),
    (CreativeFormat.DIAGRAM, re.compile(r"\b(diagram|flowchart|sequence|architecture|mermaid|plantuml|uml)\b", re.I)),
    (CreativeFormat.VISUAL_PROMPT, re.compile(r"\b(image prompt|midjourney|dall-e|stable diffusion|visualize)\b", re.I)),
    (CreativeFormat.STYLED_MARKDOWN, re.compile(r"\b(styled markdown|rich text|documentation|readme|wiki)\b", re.I)),
    (CreativeFormat.HAIKU, re.compile(r"\bhaiku\b", re.I)),
    (CreativeFormat.POEM, re.compile(r"\bpoem|poetry|verse|sonnet\b", re.I)),
    (CreativeFormat.SCREENPLAY, re.compile(r"\bscreenplay|script|scene\b", re.I)),
    (CreativeFormat.SONG, re.compile(r"\bsong|lyrics|chorus\b", re.I)),
    (CreativeFormat.SOCIAL_POST, re.compile(r"\btweet|caption|instagram|linkedin post\b", re.I)),
    (CreativeFormat.ESSAY, re.compile(r"\bessay|argumentative|persuasive|article\b", re.I)),
    (CreativeFormat.SHORT_STORY, re.compile(r"\bstory|short story|flash fiction|narrative\b", re.I)),
]

_REVISION_RE = re.compile(r"\b(shorter|longer|rewrite|revise|tweak|make it more|make it less)\b", re.I)
_WORD_RE = re.compile(r"\b(\d+)\s*(?:-|\s)?word", re.I)
_RATIO_RE = re.compile(r"\b(\d+:\d+)\b")
_COLOR_RE = re.compile(r"\b(?:colors?|palette)\s*[:=]\s*([a-zA-Z0-9,\s#]+)", re.I)
_UI_RE = re.compile(r"\b(tailwind|bootstrap|material|chakra|shadcn|mui)\b", re.I)

_CONFIGS: Dict[CreativeFormat, FormatConfig] = {
    CreativeFormat.UI_MOCKUP: FormatConfig("Semantic HTML5 + Tailwind CSS. Include ARIA & responsive patterns.", 0.4, 0.9, "Elite UI/UX Engineer."),
    CreativeFormat.DIAGRAM: FormatConfig("Precise Mermaid.js syntax. Correct nodes/edges/subgraphs.", 0.2, 0.8, "Systems Architect."),
    CreativeFormat.VISUAL_PROMPT: FormatConfig("Detailed Midjourney v6/DALL-E 3 prompt. Subject, medium, lighting, params.", 0.8, 0.95, "Expert AI Art Director."),
    CreativeFormat.STYLED_MARKDOWN: FormatConfig("Rich GFM, HTML callouts, tables, Mermaid. Clear H2/H3 structure.", 0.6, 0.9, "Technical Writer."),
    CreativeFormat.HAIKU: FormatConfig("5-7-5 syllables. Single vivid image.", 0.95, 0.98, "Master Poet."),
    CreativeFormat.POEM: FormatConfig("Form serves meaning. Concrete imagery, effective meter.", 0.9, 0.95, "Acclaimed Poet."),
    CreativeFormat.SCREENPLAY: FormatConfig("Standard conventions: INT./EXT., present tense action, caps for characters.", 0.8, 0.9, "Professional Screenwriter."),
    CreativeFormat.SONG: FormatConfig("Clear verse/chorus. Rhythm and singability.", 0.85, 0.95, "Hit Songwriter."),
    CreativeFormat.SOCIAL_POST: FormatConfig("Tight, scannable, strong hook. Platform-native formatting.", 0.7, 0.9, "Viral Social Strategist."),
    CreativeFormat.ESSAY: FormatConfig("Clear thesis, evidence-driven body, strong conclusion.", 0.6, 0.85, "Distinguished Essayist."),
    CreativeFormat.SHORT_STORY: FormatConfig("Scene over summary. Enter late, sensory details, narrative tension.", 0.85, 0.95, "Master Storyteller."),
    CreativeFormat.GENERAL: FormatConfig("High-quality, comprehensive, engaging response.", 0.7, 0.9, "Makima Creative Engine."),
}

def _detect_formats(msg: str) -> List[CreativeFormat]:
    found = [fmt for fmt, pat in _PATTERNS if pat.search(msg)]
    return found if found else [CreativeFormat.GENERAL]

def _extract_constraints(msg: str, fmt: CreativeFormat) -> CreativeConstraints:
    c = CreativeConstraints(detected_format=fmt)
    if m := _WORD_RE.search(msg):
        c.target_words = int(m.group(1))
        c.max_tokens = max(64, int(c.target_words * 1.5))
    if m := _RATIO_RE.search(msg): 
        c.aspect_ratio = m.group(1)
    if m := _COLOR_RE.search(msg): 
        c.color_palette = [x.strip() for x in m.group(1).split(",") if x.strip()]
    if m := _UI_RE.search(msg): 
        c.ui_framework = m.group(1).lower()
    c.is_revision = bool(_REVISION_RE.search(msg))
    return c

def _post_process(raw: str, fmt: CreativeFormat, c: CreativeConstraints) -> str:
    out = raw.strip()
    if fmt == CreativeFormat.DIAGRAM and "```mermaid" not in out and "graph " in out:
        out = re.sub(r"(graph\s+\w+|sequenceDiagram|classDiagram|stateDiagram|erDiagram|flowchart)", r"```mermaid\n\1", out, count=1)
        if "```mermaid" in out and not out.endswith("```"): 
            out += "\n```"
        out = f"> [!NOTE] **Architectural Diagram**\n\n{out}"
    if fmt == CreativeFormat.VISUAL_PROMPT:
        if c.aspect_ratio and f"--ar {c.aspect_ratio}" not in out: 
            out += f" --ar {c.aspect_ratio}"
        out = f"### 🎨 Visual Asset Prompt\n\n```text\n{out}\n```\n\n*Copy into your image generation tool.*"
    if fmt == CreativeFormat.UI_MOCKUP and c.ui_framework == "tailwind" and "<html" not in out and "<div" in out:
        out = f"```html\n<!DOCTYPE html>\n<html lang='en'>\n<head><script src='https://cdn.tailwindcss.com'></script></head>\n<body class='bg-gray-50 p-8'>\n{out}\n</body>\n</html>\n```"
    return out

class CreativeAgent(BaseAgent):
    """Elite Creative Design, UI Mockup, and Visual Asset Orchestration Engine."""

    AGENT_NAME = "creative"
    DESCRIPTION = "Creative writing, storytelling, poetry, essays, brainstorming, and content generation."

    SYSTEM_PROMPT = """You are Makima's Elite Creative Agent — an expert AI Art Director, copywriter, and storyteller.
You craft vivid image-generation prompts, compelling copy, immersive stories, and clean UI mockups.

CREATIVE DOMAINS:
1. Image Prompt Engineering: Generate rich prompts tailored for Midjourney v6, DALL-E 3, or Stable Diffusion (lighting, composition, camera optics, textures).
2. Copywriting & Content: Write engaging marketing copy, newsletters, taglines, scripts, and long-form prose.
3. Storytelling & Narrative: Develop world-building, dialogue, character arcs, and atmospheric scenes.
4. UI/UX Ideation: Design modern, glassmorphic, and aesthetically pleasing component/screen concepts.""" + "\n" + TOOL_DISCIPLINE_BLOCK

    def __init__(
        self,
        ai_handler=None,
        memory=None,
        tool_registry=None,
        ws_broadcast=None,
        orchestrator=None,
        guardrails=None,
        **kwargs
    ):
        super().__init__(ai_handler, memory, tool_registry, ws_broadcast, orchestrator, guardrails)
        self._cache: Dict[str, str] = {}

    async def _generate_content(self, prompt: str, cfg: FormatConfig, constraints: CreativeConstraints, context: Optional[Dict[str, Any]] = None) -> str:
        """Routes to LLM Gateway with full system persona, skill guides, and memory context."""
        fmt = constraints.detected_format
        llm_prompt = f"{cfg.guidance}\n\nPrompt: {prompt}\n\nConstraints: {constraints}\n\nAddendum: {cfg.addendum}"
        messages = self._build_messages(llm_prompt, context or {})
        return await self._llm_call(messages, task="creative")

    async def _orchestrate_parallel(self, msg: str, formats: List[CreativeFormat], context: Optional[Dict[str, Any]] = None) -> List[str]:
        """Executes multiple creative generation tasks concurrently."""
        async def _run_single(f: CreativeFormat) -> str:
            c = _extract_constraints(msg, f)
            cfg = _CONFIGS[f]
            raw = await self._generate_content(msg, cfg, c, context=context)
            return _post_process(raw, f, c)
            
        results = await asyncio.gather(*[_run_single(f) for f in formats], return_exceptions=True)
        return [r if not isinstance(r, Exception) else f"Generation Error: {r}" for r in results]

    async def execute(self, task_id: str, message: str, context: Dict[str, Any], entities: Optional[Dict[str, Any]] = None) -> str:
        """Executes the creative orchestration pipeline."""
        self._reset_state()
        # ── P1 Bridge 4B: Consume structured AgentTask parameters directly ────
        agent_task = getattr(self, "_current_agent_task", None) or (context.get("agent_task") if isinstance(context, dict) else None)
        if agent_task and hasattr(agent_task, "goal") and agent_task.goal:
            message = message or agent_task.goal
        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            formats = _detect_formats(message)
            primary_fmt = formats[0]
            constraints = _extract_constraints(message, primary_fmt)
            cfg = _CONFIGS[primary_fmt]

            req_hash = hashlib.md5(f"{message}:{primary_fmt.name}".encode()).hexdigest()
            if req_hash in self._cache and not constraints.is_revision:
                raw_output = self._cache[req_hash]
                final_output = _post_process(raw_output, primary_fmt, constraints)
                self._partial_result = final_output
                return final_output

            extra_sys = f"Format Guidance: {cfg.guidance}\nRole Persona: {cfg.addendum}"
            raw_output = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=3,
                task_type="creative",
                extra_system=extra_sys,
                input_guardrails=[],
                output_guardrails=[],
            )
            if len(self._cache) >= 256:
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[req_hash] = raw_output
            final_output = _post_process(raw_output, primary_fmt, constraints)
            self._partial_result = final_output
            return final_output
        except Exception as sdk_exc:
            logger.warning("[creative] SDK Runner encountered exception, falling back: %s", sdk_exc)

        try:
            formats = _detect_formats(message)
            primary_fmt = formats[0]
            constraints = _extract_constraints(message, primary_fmt)
            cfg = _CONFIGS[primary_fmt]
            
            req_hash = hashlib.md5(f"{message}:{primary_fmt.name}".encode()).hexdigest()
            
            if req_hash in self._cache and not constraints.is_revision:
                raw_output = self._cache[req_hash]
                final_output = _post_process(raw_output, primary_fmt, constraints)
            elif len(formats) > 1:
                parallel_results = await self._orchestrate_parallel(message, formats, context=context)
                final_output = "\n\n---\n\n".join(parallel_results)
            else:
                raw_output = await self._generate_content(message, cfg, constraints, context=context)
                if len(self._cache) >= 256:
                    # Evict oldest entry
                    self._cache.pop(next(iter(self._cache)), None)
                self._cache[req_hash] = raw_output
                final_output = _post_process(raw_output, primary_fmt, constraints)
            
            self._partial_result = final_output
            return final_output
        except Exception as e:
            logger.error(f"CreativeAgent execution failed: {e}", exc_info=True)
            return f"Creative generation pipeline encountered an unexpected error: {e}"

