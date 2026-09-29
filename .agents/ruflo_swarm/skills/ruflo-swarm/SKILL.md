---
name: ruflo-swarm
description: Zero-API-key Swarm Orchestration combining Ruflo MCP (hierarchical mesh topology, Raft consensus, HNSW vector memory, code complexity analysis) with native Antigravity Gemini subagents as execution workers. Use this skill whenever orchestrating multi-agent swarms, running consensus audits, or managing agentdb vector memory without external API keys.
---

# 🐝 Ruflo Swarm (Zero-API-Key Native Engine) Skill

## 1. Overview
The **Ruflo Swarm Skill** provides an enterprise-grade multi-agent coordination system that operates **with ZERO external API keys (no Anthropic, no OpenAI, no Ollama)**.

It pairs:
1. **Ruflo MCP (Coordination & Memory Plane)**:
   - 15-Agent Hierarchical Mesh Topology (`swarm_init`, `swarm_status`)
   - Queen-Led Raft Consensus (`hive-mind_init`, `hive-mind_spawn`, `hive-mind_consensus`)
   - High-Speed HNSW Vector Embeddings & AgentDB (`memory_store`, `memory_search`)
   - Static Code Complexity & Risk Classification (`ruflo analyze complexity`, `analyze_file-risk`)
2. **Antigravity Gemini Subagents (Cognitive Execution Plane)**:
   - Custom `ruflo_agent` / `self` subagents acting as the swarm workers.
   - Deep adversarial code inspection, caller/callee tracing, and autonomous refactoring.

---

## 2. Triggering Rules
Activate this skill whenever:
- User requests running a multi-agent swarm, Ruflo swarm, or hive-mind audit.
- Orchestrating tasks requiring Raft voting, consensus approval, or HNSW vector memory persistence.
- Performing multi-perspective adversarial code audits without external API key consumption.

---

## 3. Operational Protocols

### Phase 1: Swarm Initialization
1. Check existing swarm status via Ruflo MCP `swarm_status`.
2. If uninitialized, call `swarm_init` with `topology="hierarchical"`, `maxAgents=5`, `strategy="specialized"`.
3. Register designated swarm workers via `agent_spawn`:
   - `ruflo-orchestrator-lead` (Role: `orchestrator`, Capabilities: `system-orchestration`)
   - `ruflo-arch-auditor` (Role: `architect`, Capabilities: `software-architecture`)
   - `ruflo-code-auditor` (Role: `code-reviewer`, Capabilities: `code-quality`)

### Phase 2: Hive-Mind & Raft Consensus Setup
1. Initialize collective via `hive-mind_init` with `consensusAlgorithm="raft"`.
2. Spawn worker nodes into the hive via `hive-mind_spawn`.
3. When conclusions or audits are ready, submit a Raft proposal via `hive-mind_consensus` for formal quorum voting.

### Phase 3: Cognitive Execution via Antigravity Native Subagents
1. **Crucial Rule**: NEVER call Ruflo's internal `agent_execute` without an external API key (it fails with `No LLM provider configured`).
2. Instead, invoke Antigravity native subagents (`ruflo_agent` or `self`) with the specialized prompts defined in `.agents/ruflo_swarm/agents/`.
3. The native subagents read files, analyze caller sites, run adversarial verification, and compile findings.

### Phase 4: Vector Memory & Ledger Persistence
1. Store audit results, decision logs, and code patterns into Ruflo's local HNSW database via `memory_store` under namespace `audits` or `swarm_knowledge`.
2. Persist consensus records in Ruflo's event store.

---

## 4. Ruflo CLI Integration Commands
Use `ruflo.cmd` in PowerShell for local static analysis:
```powershell
# Analyze code complexity (Cyclomatic & Cognitive metrics)
ruflo.cmd analyze complexity <file_or_dir> --threshold 10

# Classify file change risk score
ruflo.cmd analyze diff --risk

# Check swarm & daemon status
ruflo.cmd status
```

---

## 5. Directory Structure
All related artifacts, agent prompts, and bridge tools reside in:
`c:\code\makima\.agents\ruflo_swarm\`
- `README.md`: Architectural documentation and workflow diagrams.
- `agents/`: Dedicated prompt definitions for the swarm roles.
- `bridge.py`: Local automation harness bridging Ruflo MCP and native execution.
