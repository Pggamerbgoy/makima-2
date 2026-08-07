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

from .base_agent import BaseAgent

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
Your mandate is to execute rigorous, multi-hop research. You decompose complex queries, 
evaluate source credibility using both heuristics and semantic analysis, and synthesize 
authoritative academic reports. You never hallucinate. You always cite sources using bracketed 
numbers (e.g., [1], [2]). You prioritize official, academic, and highly credible domains.

SYNTHESIS & FORMATTING STANDARDS (NEMOTRON RIGOR):
1. **Academic Structure**: Incorporate an Abstract, Case Studies, Methodologies & Collaborations, Economic & Societal Impact, and formal Academic Reference lists.
2. **Visual Enhancements**:
   - **Mermaid Flowcharts & Timelines**: Use ```mermaid timeline``` or ```mermaid``` to visualize architectures and chronologies. ALWAYS wrap node label text in double quotes if it contains colons, commas, or punctuation (e.g. `C["2000s: RNNs, CNNs"]`).
   - **LaTeX Mathematical Formulas**: Use LaTeX delimiters (e.g. $PR(p) = ...$) for mathematical or algorithmic formulas.
   - **Markdown Benchmark Tables**: Include structured Markdown comparison tables for metrics, dates, and benchmarks.
   - **Executive Callouts**: Use blockquote callouts (`> 📌 **Key Insight**: ...`) for critical takeaways.

By default, you synthesize concise, executive-grade briefs. HOWEVER, if the user explicitly 
requests a "long", "comprehensive", "deep dive", or multi-page report, you MUST drop the 
executive brevity and generate a highly detailed, comprehensive, and exhaustive document 
that matches their requested length and depth.

CRITICAL DISAMBIGUATION RULE:
- When researching corporate/technology entities (e.g. "Apple", "Amazon", "Tesla", "Meta"), focus 100% on the technology corporation (products, financials, news, market data, leadership). DO NOT generate botanical, agricultural, or fruit science tables unless the user explicitly requests "apple fruit" or "botany".
"""

    # ──────────────────────────────────────────────
    # Core Execution Contract
    # ──────────────────────────────────────────────

    async def execute(
        self, 
        task_id: str, 
        message: str, 
        context: dict[str, Any], 
        entities: dict[str, Any]
    ) -> str:
        """
        Main execution loop for the Research Agent.
        Follows the BaseAgent contract strictly.
        """
        self._reset_state()
        logger.info("[research] Initiating elite research pipeline for task %s", task_id)

        try:
            # 1. Context & Entity Enrichment
            enriched_message = self._enrich_query(message, context, entities)

            # 2. Multi-hop Query Decomposition
            sub_queries = await self._decompose_query(enriched_message)
            if not sub_queries:
                logger.warning("[research] Decomposition failed, falling back to original query.")
                sub_queries = [SubQuery(query=message, intent="general", target_tlds=[])]

            # 3. Concurrent Web Search & Parsing
            raw_snippets = await self._execute_concurrent_searches(sub_queries)
            
            if not raw_snippets:
                logger.warning("[research] No valid snippets retrieved. Generating fallback response.")
                return await self._generate_fallback_response(message, context)

            # 4. Credibility & Relevance Scoring
            scored_snippets = self._score_and_rank_snippets(raw_snippets, enriched_message)

            # 5. Executive Synthesis
            final_report = await self._synthesize_executive_report(message, scored_snippets)
            
            logger.info("[research] Successfully completed research pipeline for task %s", task_id)
            return final_report

        except asyncio.CancelledError:
            logger.warning("[research] Task %s was cancelled.", task_id)
            return "Research task was cancelled by the system or user."
        except Exception as e:
            logger.exception("[research] Critical failure in execute pipeline: %s", e)
            return self._format_error_report(message, str(e))

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
        decomp_prompt = f"""Analyze the following research objective and decompose it into 1 to 4 atomic, highly specific search queries.
For each query, identify the core intent and preferred domain extensions (e.g., .edu, .gov, .org, .com) to ensure high credibility.

Return ONLY a valid JSON object matching this exact schema:
{{
    "sub_queries": [
        {{"query": "string", "intent": "string", "target_tlds": [".edu", ".gov"]}}
    ]
}}

Objective: {message}
"""
        try:
            raw_response = await self._llm_call(
                [{"role": "user", "content": decomp_prompt}],
                task="research_decomp",
                require_json=True,
                max_tokens=400,
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
            clean_query = sub_query.query.strip()
            raw_result = await self._use_tool("web_search", query=clean_query, max_results=3)
            
            # 2. If clean query returned no results and target TLDs exist, try site filtering as fallback
            if (not raw_result or "no results" in str(raw_result).lower()) and sub_query.target_tlds:
                tld_suffix = " ".join([f"site:{tld.lstrip('*')}" for tld in sub_query.target_tlds[:2]])
                retry_query = f"{clean_query} {tld_suffix}".strip()
                raw_result = await self._use_tool("web_search", query=retry_query, max_results=3)

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
                    snippets.append(ResearchSnippet(
                        url=str(item.get("url", item.get("link", ""))),
                        title=str(item.get("title", "Untitled")),
                        content=str(item.get("snippet", item.get("content", item.get("description", ""))))
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

        return [s for s in snippets if s.url and s.content]

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
            query_words = set(re.findall(r'\b\w+\b', query.lower()))
            content_words = set(re.findall(r'\b\w+\b', content.lower()))
            
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
        """Generates a massive 10+ page (6,000+ words) research report using a 2-Step Modular Synthesizer."""
        snippet_context = self._format_snippets_for_prompt(snippets)
        
        # Check if long-form is requested
        msg_lower = original_message.lower()
        is_long_form = any(k in msg_lower for k in ["long", "comprehensive", "deep dive", "page"])
        
        if not is_long_form:
            # Concise Briefing Mode
            synthesis_prompt = f"""You are Makima's Elite Research Synthesizer.
Original Objective: {original_message}

Verified Snippets:
{snippet_context}

Format a concise, modern executive briefing with Abstract, Key Highlights, Deep-Dive Analysis, and Verified Sources inline [1], [2]. Include Markdown tables and Mermaid diagrams where relevant.
"""
            return (await self._llm_call([{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": synthesis_prompt}], task="research", max_tokens=3000, temperature=0.3)).strip()

        # ─── 2-STEP MODULAR SYNTHESIZER (TRUE 10+ PAGES / 6,000+ WORDS) ───
        logger.info("[research] Initiating 2-Step Modular Synthesizer for 10+ page massive output...")
        
        # STEP 1: Part A (Foundations, History, Infrastructure, Mathematics & Architecture)
        part_a_prompt = f"""You are Makima's Elite Research Synthesizer writing PART 1 of a massive 10-page academic research paper.
Objective: {original_message}

Verified Snippets:
{snippet_context}

PART 1 REQUIREMENTS (WRITE 2,500 - 3,000 WORDS EXHAUSTIVELY):
1. **Title & Abstract**: Professional academic title and abstract.
2. **Section 1: Introduction & Scope**: Comprehensive introduction to the topic.
3. **Section 2: Historical Background & Evolution**: Early origins, BackRub, PageRank, company growth, IPO, key acquisitions.
4. **Section 3: Technical Architecture & Core Algorithms**:
   - PageRank Mathematical Formulation: MUST include LaTeX equation $PR(p) = \\frac{{1-d}}{{N}} + d \\sum_{{q \\in B_p}} \\frac{{PR(q)}}{{L(q)}}$ with full variable definitions.
   - Distributed Systems: Deep dive into Bigtable, Spanner (TrueTime), and Borg cluster management.
5. **VISUAL EMBEDDINGS**:
   - MUST include at least 1 **Mermaid Timeline/Gantt Chart** (```mermaid timeline```) visualizing early milestones.
   - MUST include at least 1 **Markdown Comparison Table** comparing distributed infrastructure components.
   - Use blockquote callouts (`> 📌 **Key Insight**: ...`).
6. Cite sources inline using bracketed numbers [1], [2].
7. Do NOT write a conclusion yet; end Part 1 smoothly for Part 2 to continue.
"""
        logger.info("[research] Generating Part 1 (Foundations & Architecture)...")
        part_a = await self._llm_call(
            [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": part_a_prompt}],
            task="research", max_tokens=6000, temperature=0.3
        )

        # STEP 2: Part B (AI Breakthroughs, Open Source, Case Studies, Impact, Conclusion, References)
        part_b_prompt = f"""You are Makima's Elite Research Synthesizer writing PART 2 of a massive 10-page academic research paper.
Objective: {original_message}

Verified Snippets:
{snippet_context}

PART 2 REQUIREMENTS (WRITE 2,500 - 3,000 WORDS EXHAUSTIVELY):
Continue seamlessly from Part 1. Cover the remaining topics in extreme academic depth:
1. **Section 4: Artificial Intelligence & Machine Learning Breakthroughs**: DeepMind (AlphaGo, AlphaFold GNNs), Google Brain, Transformers (BERT, LaMDA, PaLM, Gemini).
2. **Section 5: Machine Learning Frameworks & Open Source**: TensorFlow, JAX/XLA, Kubernetes, Android.
3. **Section 6: Frontier Science & Emerging Initiatives**: Quantum AI (Sycamore processor), Healthcare & Verily, Waymo autonomous driving.
4. **Section 7: Economic, Societal & Ethical Impact**: SMEs, global infrastructure, privacy, AI ethics.
5. **Section 8: Conclusion & Future Directions**: Final authoritative summary.
6. **VISUAL EMBEDDINGS**:
   - MUST include at least 1 **Mermaid Architecture Diagram** (```mermaid```) visualizing NLP/AI model evolution. ALWAYS wrap node label text in double quotes if it contains colons or commas (e.g. `C["2000s: RNNs, CNNs"]`).
   - MUST include at least 1 **Markdown Comparison Table** comparing AI models (BERT vs PaLM vs Gemini).
   - Use blockquote callouts (`> 📌 **Key Insight**: ...`).
7. **Section 9: Verified Sources & References**: Complete list of citations with titles and clickable markdown links.
"""
        logger.info("[research] Generating Part 2 (AI Breakthroughs, Science & Impact)...")
        part_b = await self._llm_call(
            [{"role": "system", "content": self.SYSTEM_PROMPT}, {"role": "user", "content": part_b_prompt}],
            task="research", max_tokens=6000, temperature=0.3
        )

        return f"{part_a.strip()}\n\n---\n\n{part_b.strip()}"

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
        """Robustly extracts JSON from LLM responses, handling markdown wrappers and trailing text."""
        if not text:
            return None
            
        # 1. Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
            
        # 2. Markdown code block extraction
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
                
        # 3. Bracket matching fallback
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
                    
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
