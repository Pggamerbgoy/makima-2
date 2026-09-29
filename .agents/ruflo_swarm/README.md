# 🐝 Ruflo Swarm (Option 1: Master Zero-API-Key Native Toolkit)

A comprehensive, enterprise-grade multi-agent swarm platform that bundles **Ruflo's local coordination & memory plane** with **Antigravity Gemini cognitive execution**, requiring **ZERO external API keys (no Anthropic, no OpenAI, no Ollama)**.

---

## 🏛️ Architecture Breakdown

```
┌────────────────────────────────────────────────────────────────────────┐
│                   RUFLO LOCAL COORDINATION PLANE (MCP)                 │
│  • 15-Agent Hierarchical Mesh Topology (`swarm_init`)                  │
│  • Queen-Led Raft Consensus Ledger (`hive-mind_consensus`)             │
│  • Local HNSW 384d Vector Memory (`memory_store`, sql.js)              │
│  • AST Code Complexity & Symbol Extractor (`ruflo.cmd analyze`)        │
│  • Security Threat Modeling & Risk Scoring (`metaharness_threat_model`)│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Bypasses external LLM API calls)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               ANTIGRAVITY GEMINI EXECUTION PLANE (Brain)               │
│  • `ruflo-orchestrator-lead`: Synthesis & Raft proposal author         │
│  • `ruflo-arch-auditor`: Concurrency, GC, locks, and lifecycle audit   │
│  • `ruflo-code-auditor`: 5-point adversarial line-by-line inspection   │
│  • `ruflo-security-auditor`: PII detection & credential leak defense   │
│  • `ruflo-perf-auditor`: Algorithmic complexity & latency profiling    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📂 Master Folder Structure

```
.agents/ruflo_swarm/
├── README.md                      # Complete architecture & CLI user guide
├── bridge.py                      # Master Python CLI tool & SDK
│
├── agents/                        # 🤖 5 Specialized Swarm Worker Prompts
│   ├── orchestrator_lead.md       # 👑 Lead Coordinator & Consensus Author
│   ├── arch_auditor.md            # 🏗️ Concurrency, GC & Session Lifecycle
│   ├── code_auditor.md            # 🔍 Adversarial Line-by-Line Code Reviewer
│   ├── security_auditor.md        # 🛡️ PII, Secret Leak & Threat Modeling
│   └── perf_auditor.md            # ⚡ AST Complexity & Latency Profiler
│
├── modules/                       # 🐍 Python Submodules
│   ├── __init__.py
│   ├── analytics.py               # AST complexity, symbols & circular imports
│   ├── security.py                # Fast local regex scanner for leaked keys/PII
│   └── memory_graph.py            # AgentDB Causal Nodes & Graph Edges
│
├── mcp/                           # 🔌 Ruflo MCP Integration Assets
│   ├── mcp_config_snippet.json    # Exact IDE registration config
│   ├── ruflo_mcp_guide.md         # MCP tools, signatures & zero-key guide
│   └── schemas/                   # 34 Key Ruflo Tool Schemas (JSON)
│       ├── swarm_*.json           # Swarm topology & health
│       ├── hive-mind_*.json       # Raft consensus & broadcast
│       ├── memory_*.json          # HNSW vector storage & search
│       ├── agentdb_*.json         # Causal graph & hierarchical memory
│       ├── metaharness_*.json     # Threat models & security benchmarks
│       ├── aidefence_*.json       # PII detection & input guardrails
│       └── analyze_*.json         # Risk scoring & git diff analysis
│
└── skills/                        # 🎯 Core Operational Skills
    ├── ruflo-swarm/               # Swarm orchestration skill
    ├── master-workflow/           # Master execution pipeline
    ├── rigorous-code-development/ # Fact-check gates & coding rules
    ├── problem-reasoning/         # Root-cause analysis & hypothesis testing
    └── codebase-gap-analysis/     # Codebase audit & gap detection
```

---

## 🚀 CLI Commands & Usage

All features can be run directly via `bridge.py` without requiring any external keys:

| Action | Command |
|---|---|
| **Verify whole bundle** | `python .agents/ruflo_swarm/bridge.py --verify` |
| **List core native agents** | `python .agents/ruflo_swarm/bridge.py --list-agents` |
| **List all 77 catalog agents** | `python .agents/ruflo_swarm/bridge.py --list-all` |
| **Filter agents by category** | `python .agents/ruflo_swarm/bridge.py --category <consensus/sparc/github/core/...>` |
| **Show agent dependencies** | `python .agents/ruflo_swarm/bridge.py --deps <agent_id>` |
| **Show agent metadata JSON** | `python .agents/ruflo_swarm/bridge.py --info <agent_id>` |
| **List 7 official workflows** | `python .agents/ruflo_swarm/bridge.py --list-workflows` |
| **Show workflow DAG & handoffs**| `python .agents/ruflo_swarm/bridge.py --workflow <development/sparc/security-audit/...>` |
| **MinCut blast-radius boundary**| `python .agents/ruflo_swarm/bridge.py --boundaries <file_path>` |
| **Inspect system prompt** | `python .agents/ruflo_swarm/bridge.py --prompt <agent_name>` |
| **Extract code symbols (AST)** | `python .agents/ruflo_swarm/bridge.py --symbols <file_path>` |
| **Analyze code complexity** | `python .agents/ruflo_swarm/bridge.py --complexity <file_path>` |
| **Scan for PII / Leaked Keys** | `python .agents/ruflo_swarm/bridge.py --scan-pii <file_path>` |
| **Check Swarm & Daemon Status** | `python .agents/ruflo_swarm/bridge.py --status` |
