"""
Makima v7.2 — Elite Research Agent
Upgrades: Concurrent multi-hop query decomposition, algorithmic source credibility scoring, 
chain-of-thought executive synthesis, and zero-crash resilience.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from .base_agent import BaseAgent, TOOL_DISCIPLINE_BLOCK

logger = logging.getLogger("makima.agents.research")


@dataclass
class SubQuery:
    """Represents a decomposed atomic search query."""
    query: str
    intent: str
    target_tlds: list[str] = field(default_factory=list)


@dataclass
class ResearchSnippet:
    """Represents a single piece of retrieved evidence."""
    url: str
    title: str
    content: str
    credibility_score: float = 0.0
    relevance_score: float = 0.0
    citation_id: int = 0


class ResearchAgent(BaseAgent):
    """
    Elite Research Agent capable of deep, multi-hop web research.
    Decomposes complex queries, executes concurrent searches, algorithmically 
    scores source credibility, and synthesizes executive-grade reports with citations.
    """
    
    AGENT_NAME = "research"
    DESCRIPTION = "Concurrent multi-hop web research, algorithmic credibility scoring, executive synthesis"
    CAPABILITIES = ["web_search", "query_decomposition", "credibility_scoring", "information_synthesis", "academic_research"]
    AGENT_TOOLS = ["web_search", "fetch_url"]
    TAGS = ["research", "web", "search", "synthesis", "nemotron"]

    SYSTEM_PROMPT = """You are Makima's Elite Research Agent.
Your mandate is to execute rigorous web research, evaluate source credibility, and synthesize clear, beautifully formatted reports aligned with modern ChatGPT and Google Gemini presentation standards, using bracketed citations [1], [2].

SYNTHESIS & REPORTING STANDARDS:
1. Presentation Hierarchy (ChatGPT & Gemini Standard):
   - Use clean `## Section Headers` (e.g. ## Executive Summary, ## Technical Breakdown, ## Comparison, ## Key Takeaways, ## References).
   - Use bullet points with bold lead-ins for all technical enumerations:
     - **Mechanism / Feature**: 1–2 focused sentences explaining the impact.
   - Keep paragraphs compact (2–3 sentences max) with clean line breaks so text is easy to scan.
2. Structured Thinking: Perform deep query decomposition and source evaluation internally before synthesizing findings.
3. Visual Formatting: Include clean Markdown tables for comparisons, Mermaid diagrams where helpful, and blockquote callouts (> 💡 **Key Takeaway**:).
4. Direct Answers: Lead with the core answer first. Base all facts on live search results.""" + "\n" + TOOL_DISCIPLINE_BLOCK

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
        try:
            from ..web_search_tool import web_search, fetch_url
            self._TOOL_MAP = {
                "web_search": web_search,
                "fetch_url": fetch_url,
            }
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # Core Execution Contract
    # ──────────────────────────────────────────────

    def _score_research_depth(self, message: str) -> str:
        """Scores query research depth: 'shallow' | 'medium' | 'deep'."""
        msg = message.lower().strip()
        word_count = len(msg.split())
        
        deep_keywords = ["thesis", "paper", "exhaustive", "comprehensive", 
                         "in-depth", "full report", "whitepaper", "detailed analysis",
                         "detailed report", "multi-page", "extensive", "benchmark comparison", "deep dive"]
        if any(kw in msg for kw in deep_keywords) or word_count > 25:
            return "deep"

        # Explicitly shallow only if short query AND user explicitly requested a quick/brief summary
        explicit_shallow_keywords = ["quick summary", "brief summary", "in 1 sentence", "in one line", "quick overview", "tl;dr", "tldr"]
        if any(kw in msg for kw in explicit_shallow_keywords) or (word_count <= 4 and any(kw in msg for kw in ("who is", "what is", "define"))):
            return "shallow"

        return "medium"

    async def execute(
        self, task_id: str, message: str, context: dict[str, Any],
        entities: dict[str, Any],
    ) -> str:
        """Adaptive Research Depth Execution — Shallow (4s), Medium (10s), Deep (30s+)."""
        self._reset_state()
        # ── P1 Bridge 4B: Consume structured AgentTask parameters first ───────
        agent_task = getattr(self, "_current_agent_task", None) or context.get("agent_task")
        depth = None
        if agent_task and hasattr(agent_task, "parameters") and agent_task.parameters:
            raw_depth = str(agent_task.parameters.get("depth") or agent_task.parameters.get("research_depth") or "").lower().strip()
            if raw_depth in ("shallow", "medium", "deep"):
                depth = raw_depth
                logger.info("[research] P1-4B: Consumed structured depth from AgentTask: %s", depth)

        if not depth:
            depth = self._score_research_depth(message)
        logger.info(f"[research] Adaptive research depth selected: {depth.upper()}")


        if depth == "deep":
            try:
                enriched = self._enrich_query(message, context, entities)
                sub_queries = await self._decompose_query(enriched)
                if not sub_queries:
                    sub_queries = [SubQuery(query=message, intent="general")]
                snippets = await self._execute_concurrent_searches(sub_queries)
                ranked = self._score_and_rank_snippets(snippets, message)
                if ranked:
                    report = await self._synthesize_executive_report(message, ranked)
                    if report:
                        self._partial_result = report
                        return report
            except Exception as e:
                logger.warning(f"[research] Deep pipeline fallback to ReAct: {e}")
        else:
            # Shallow & Medium fast-path: 1-shot search + direct synthesis (under 3-4s total)
            try:
                raw_results = await self._use_tool("web_search", query=message, max_results=4)
                if raw_results and "no results" not in str(raw_results).lower():
                    sq = SubQuery(query=message, intent="general")
                    snippets = self._parse_search_results(raw_results, sq)
                    ranked = self._score_and_rank_snippets(snippets, message)
                    if ranked:
                        report = await self._synthesize_executive_report(message, ranked)
                        if report:
                            self._partial_result = report
                            return report
            except Exception as e:
                logger.warning(f"[research] Fast-path search fallback to ReAct: {e}")

        # Shallow / Medium or Deep Fallback: Adaptive ReAct loop
        configs = {
            "shallow": {"max_tokens": 800, "max_turns": 3, "prompt_suffix": "Keep the summary concise and high-signal (< 300 words)."},
            "medium":  {"max_tokens": 1800, "max_turns": 5, "prompt_suffix": "Provide a well-structured summary with sections and inline citations."},
            "deep":    {"max_tokens": 4000, "max_turns": 8, "prompt_suffix": "Provide an exhaustive, in-depth academic analysis with full citations and data tables."},
        }
        cfg = configs.get(depth, configs["medium"])

        extra_system = (
            f"[RESEARCH DEPTH: {depth.upper()}]\n"
            "You have web_search and fetch_url tools available.\n"
            "ALWAYS use web_search first to find live search results.\n"
            f"{cfg['prompt_suffix']}\n"
            "Format: Use markdown headers, bullet points, and bold key findings."
        )

        # ── OpenAI Agents SDK Runner Execution ────────────────────────────────
        try:
            final_out = await self.run_sdk_execution(
                task_id=task_id,
                message=message,
                context=context,
                max_turns=cfg["max_turns"],
                task_type="research",
                extra_system=extra_system,
                input_guardrails=[],
                output_guardrails=[],
            )
            self._partial_result = final_out
            return final_out
        except Exception as sdk_exc:
            logger.error("[research] SDK error: %s", sdk_exc)
            return f"Task complete nahi hua: {str(sdk_exc)}"

    # ──────────────────────────────────────────────
    # Phase 1: Enrichment & Decomposition
    # ──────────────────────────────────────────────

    def _enrich_query(self, message: str, context: dict[str, Any], entities: dict[str, Any]) -> str:
        """Injects relevant context and extracted entities into the research objective."""
        enriched = message
        if entities:
            entity_str = ", ".join([f"{k}: {v}" for k, v in list(entities.items())[:5]])
            enriched += f"\n\n[Known Entities: {entity_str}]"
        if context.get("domain_context"):
            enriched += f"\n\n[Domain Context: {context['domain_context']}]"
        return enriched

    async def _decompose_query(self, message: str) -> list[SubQuery]:
        """Uses LLM to break down the primary objective into atomic sub-queries."""
        decomp_prompt = f"""Deconstruct the following research objective into 2 focused search queries.
Respond with a JSON object in this format:
```json
{{
    "sub_queries": [
        {{"query": "focused search query string", "intent": "benchmark or architecture"}}
    ]
}}
```

Objective: {message}
"""
        try:
            raw_response = await self._llm_call(
                [{"role": "user", "content": decomp_prompt}],
                task="research_decomp",
                require_json=True,
                max_tokens=800,
                temperature=0.2
            )
            parsed = self.ai_handler.try_parse_json(raw_response) or {}
            sq_list = parsed.get("sub_queries") if isinstance(parsed, dict) and isinstance(parsed.get("sub_queries"), list) else []
            sub_queries = []
            for item in sq_list[:2]:  # Limit to 2 max for speed — prevents 4-8 searches per task
                if isinstance(item, dict) and "query" in item:
                    sub_queries.append(SubQuery(
                        query=str(item["query"]),
                        intent=str(item.get("intent", "general")),
                        target_tlds=list(item.get("target_tlds", []))
                    ))
            return sub_queries
        except Exception as e:
            logger.error("[research] Query decomposition failed: %s", e)
            return []

    # ──────────────────────────────────────────────
    # Phase 2: Concurrent Search & Parsing
    # ──────────────────────────────────────────────

    async def _execute_concurrent_searches(self, sub_queries: list[SubQuery]) -> list[ResearchSnippet]:
        """Executes web searches concurrently using asyncio.gather for high performance."""
        tasks = [self._fetch_and_parse_subquery(sq) for sq in sub_queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_snippets: list[ResearchSnippet] = []
        for res in results:
            if isinstance(res, list):
                all_snippets.extend(res)
            elif isinstance(res, Exception):
                logger.error("[research] Subquery search task failed: %s", res)
                
        return all_snippets

    async def _fetch_and_parse_subquery(self, sub_query: SubQuery) -> list[ResearchSnippet]:
        """Fetches results for a single subquery and parses them into ResearchSnippets."""
        try:
            # 1. Primary search: Clean subquery (NO site:*.edu / site:*.gov query pollution)
            clean_query = re.sub(r'site:\S+', '', sub_query.query).strip()
            raw_result = None
            try:
                raw_result = await self._use_tool("web_search", query=clean_query, max_results=3)
            except Exception:
                pass
            if not raw_result or "not found in registry" in str(raw_result).lower() or str(raw_result).startswith("[Failed]"):
                from ..web_search_tool import web_search
                raw_result = await web_search(query=clean_query, max_results=3)
            
            # 2. If clean query returned no results and target TLDs exist, try site filtering as fallback
            if (not raw_result or "no results" in str(raw_result).lower()) and sub_query.target_tlds:
                tld_suffix = " ".join([f"site:{tld.lstrip('*')}" for tld in sub_query.target_tlds[:2]])
                retry_query = f"{clean_query} {tld_suffix}".strip()
                try:
                    raw_result = await self._use_tool("web_search", query=retry_query, max_results=3)
                except Exception:
                    pass
                if not raw_result or "not found in registry" in str(raw_result).lower() or str(raw_result).startswith("[Failed]"):
                    from ..web_search_tool import web_search
                    raw_result = await web_search(query=retry_query, max_results=3)

            if not raw_result or "no results" in str(raw_result).lower():
                return []

            return self._parse_search_results(raw_result, sub_query)
        except Exception as e:
            logger.error("[research] Fetch/Parse failed for query '%s': %s", sub_query.query, e)
            return []

    def _parse_search_results(self, raw_result: Any, sub_query: SubQuery) -> list[ResearchSnippet]:
        """Robustly parses the raw tool output into structured snippets."""
        snippets = []
        
        # Attempt JSON parsing first (if the tool returns structured JSON)
        if isinstance(raw_result, str):
            parsed_json = self._extract_json(raw_result)
            if parsed_json and isinstance(parsed_json, list):
                raw_result = parsed_json
            elif parsed_json and isinstance(parsed_json, dict) and "results" in parsed_json:
                raw_result = parsed_json["results"]

        if isinstance(raw_result, list):
            for item in raw_result:
                if isinstance(item, dict):
                    url_val = item.get("url") or item.get("link") or ""
                    title_val = item.get("title") or "Untitled"
                    content_val = item.get("snippet") or item.get("content") or item.get("description") or ""
                    snippets.append(ResearchSnippet(
                        url=str(url_val).strip(),
                        title=str(title_val).strip(),
                        content=str(content_val).strip()
                    ))
        elif isinstance(raw_result, str):
            # Fallback: Regex extraction for text-based tool outputs
            # Assumes format like: Title: ... \n URL: ... \n Snippet: ...
            blocks = re.split(r'\n\s*[-=]{3,}\s*\n|\n\n+', raw_result)
            for block in blocks:
                title_match = re.search(r'(?:Title|Heading):\s*(.+)', block, re.IGNORECASE)
                url_match = re.search(r'(?:URL|Link|Source):\s*(https?://\S+)', block, re.IGNORECASE)
                snippet_match = re.search(r'(?:Snippet|Content|Description):\s*(.+)', block, re.IGNORECASE | re.DOTALL)
                
                if url_match:
                    snippets.append(ResearchSnippet(
                        url=url_match.group(1).strip(),
                        title=title_match.group(1).strip() if title_match else "Extracted Source",
                        content=snippet_match.group(1).strip() if snippet_match else block.strip()
                    ))

        return [s for s in snippets if s.url and s.url != "None" and s.content and s.content != "None"]

    # ──────────────────────────────────────────────
    # Phase 3: Algorithmic Credibility Scoring
    # ──────────────────────────────────────────────

    def _score_and_rank_snippets(self, snippets: list[ResearchSnippet], original_query: str) -> list[ResearchSnippet]:
        """Applies heuristic credibility scoring and ranks the snippets."""
        # KAMI-16 FIX: Strip prompt enrichment boilerplate before computing keyword relevance
        clean_query = original_query.split("[Known Entities:")[0].split("[Domain Context:")[0].strip()

        citation_counter = 1
        for snippet in snippets:
            snippet.credibility_score = self._calculate_heuristic_credibility(snippet.url, snippet.content)
            snippet.relevance_score = self._calculate_keyword_relevance(clean_query, snippet.content)
            snippet.citation_id = citation_counter
            citation_counter += 1

        # Sort by a weighted composite score (60% credibility, 40% relevance)
        snippets.sort(
            key=lambda s: (s.credibility_score * 0.6) + (s.relevance_score * 0.4), 
            reverse=True
        )
        
        # Keep top 8 snippets to prevent context window overflow during synthesis
        return snippets[:8]

    def _calculate_heuristic_credibility(self, url: str, content: str) -> float:
        """Calculates a 0.0 to 1.0 credibility score based on URL structure and content heuristics."""
        score = 0.5  # Base score
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # TLD Boosts/Penalties
            if domain.endswith(('.edu', '.gov', '.mil', '.ac.uk')):
                score += 0.35
            elif domain.endswith(('.org', '.int')):
                score += 0.15
            elif domain.endswith(('.com', '.net', '.io', '.co')):
                score += 0.05
                
            # Subdomain/Path Heuristics (KAMI-16 FIX: match 'medium.com' without trailing slash)
            if any(sub in domain for sub in ['blog.', 'forum.', 'reddit.', 'quora.', 'medium.com']):
                score -= 0.25
            if any(sub in domain for sub in ['scholar.', 'research.', 'science.', 'ncbi.', 'ieee.']):
                score += 0.25
                
            # Content Depth Proxy
            word_count = len(content.split())
            if word_count > 300:
                score += 0.1
            elif word_count < 30:
                score -= 0.15
                
            # Penalty for excessive URL parameters (often indicates low-quality aggregation)
            if len(parsed.query) > 150:
                score -= 0.1
                
        except Exception:
            pass  # Failsafe if URL parsing breaks

        return max(0.0, min(1.0, score))

    def _calculate_keyword_relevance(self, query: str, content: str) -> float:
        """Simple but effective TF-based keyword overlap scoring."""
        try:
            if not query or not content:
                return 0.5
            query_words = set(re.findall(r'\b\w+\b', str(query).lower()))
            content_words = set(re.findall(r'\b\w+\b', str(content).lower()))
            
            # Remove stop words for better signal
            stop_words = {"the", "and", "is", "in", "it", "of", "to", "a", "for", "on", "with", "as", "by"}
            query_words -= stop_words
            
            if not query_words:
                return 0.5
                
            overlap = query_words.intersection(content_words)
            return len(overlap) / len(query_words)
        except Exception:
            return 0.0

    # ──────────────────────────────────────────────
    # Phase 4: Executive Synthesis
    # ──────────────────────────────────────────────

    async def _synthesize_executive_report(self, original_message: str, snippets: list[ResearchSnippet]) -> str:
        """Generates a comprehensive, academic research report from verified snippets."""
        snippet_context = self._format_snippets_for_prompt(snippets)
        logger.info("[research] Synthesizing comprehensive research report for %d snippets...", len(snippets))

        # Check if long-form / deep dive is requested
        msg_lower = original_message.lower()
        is_deep = any(k in msg_lower for k in ["long", "comprehensive", "deep dive", "exhaustive", "benchmark", "detailed"])
        max_tokens = 2200 if is_deep else 1500

        synthesis_prompt = f"""You are Makima's Elite Research Synthesizer.
Research Objective: {original_message}

Verified Live Search Findings & Sources:
{snippet_context}

SYNTHESIS & REPORTING REQUIREMENTS (ChatGPT & Gemini Presentation Standard):
1. **## Executive Summary**: Direct, high-signal 1–2 paragraphs summarizing the findings without boilerplate.
2. **## Technical Breakdown**: Thematic breakdown with bold lead-in bullet points (**Mechanism**: Explanation).
3. **## Comparative Analysis & Benchmark Table**: Structured Markdown comparison table evaluating specifications, performance, and tradeoffs.
4. **## Visual Diagram**: Include at least one valid Mermaid diagram (e.g. ```mermaid graph TD```). ALWAYS enclose node labels with quotes.
5. **## Strategic Takeaways**: Actionable bulleted takeaways with callouts (`> 💡 **Key Takeaway**: ...`).
6. **## References**: Clean numbered references with clickable markdown links (`[[1] Title](URL)`).
"""
        return (await self._llm_call(
            [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": synthesis_prompt}],
            task="research",
            max_tokens=max_tokens,
            temperature=0.25,
        )).strip()

    def _format_snippets_for_prompt(self, snippets: list[ResearchSnippet]) -> str:
        """Formats snippets into a clean, token-efficient string for the LLM prompt."""
        formatted = []
        for s in snippets:
            formatted.append(
                f"[Citation {s.citation_id}] | Credibility: {s.credibility_score:.2f} | Relevance: {s.relevance_score:.2f}\n"
                f"Title: {s.title}\n"
                f"URL: {s.url}\n"
                f"Content: {s.content[:800]}...\n"  # Truncate to prevent context overflow
            )
        return "\n---\n".join(formatted)

    # ──────────────────────────────────────────────
    # Utility & Fallback Methods
    # ──────────────────────────────────────────────

    def _extract_json(self, text: str) -> Optional[dict | list]:
        """Robustly extracts JSON from LLM responses using unified try_parse_json."""
        if not text:
            return None
        if self.ai_handler and hasattr(self.ai_handler, "try_parse_json"):
            return self.ai_handler.try_parse_json(text)
        try:
            return json.loads(text)
        except Exception:
            return None

    async def _generate_fallback_response(self, message: str, context: dict[str, Any]) -> str:
        """Generates a direct LLM response when web search yields zero results."""
        fallback_prompt = f"""The web search tool returned no results for the following query. 
Please provide the best possible answer using your internal knowledge base. 
Clearly state that live web data was unavailable and cite your internal knowledge limitations.

Query: {message}
"""
        try:
            return await self._llm_call(
                [{"role": "user", "content": fallback_prompt}],
                task="research_fallback",
                max_tokens=1500
            )
        except Exception as e:
            return f"I was unable to retrieve live web data for your query, and the fallback generation failed: {e}"

    def _format_error_report(self, message: str, error_detail: str) -> str:
        """Formats a graceful, user-facing error report."""
        return (
            "══════════════════════════════════════════════════\n"
            "            RESEARCH PIPELINE ERROR\n"
            "══════════════════════════════════════════════════\n\n"
            f"**Objective:** {message}\n\n"
            "**Status:** The elite research pipeline encountered a critical exception during execution.\n"
            f"**Diagnostic:** `{error_detail}`\n\n"
            "Please review the system logs for a full stack trace or retry the query."
        )
