# 🔌 Ruflo MCP Integration Guide (Zero-API-Key Architecture)

This document explains how the **Ruflo MCP Server** is integrated into Makima OS to provide multi-agent swarm coordination, Raft consensus, and vector memory with **zero external API keys**.

---

## 1. What Works 100% Locally Without Any API Key

The following Ruflo MCP capabilities operate entirely in-process using local SQLite (`sql.js`), local transformers embeddings (`Xenova/all-MiniLM-L6-v2`), and local AST parsing:

| Subsystem | MCP Tools | Local Mechanism |
|---|---|---|
| **Swarm Coordination** | `swarm_init`, `swarm_status`, `swarm_health`, `swarm_shutdown` | Local hierarchical mesh routing in Node.js daemon. |
| **Agent Registration** | `agent_spawn`, `agent_list`, `agent_status` | In-memory agent registry & role attribution. |
| **Hive-Mind & Consensus** | `hive-mind_init`, `hive-mind_spawn`, `hive-mind_consensus`, `hive-mind_broadcast` | Raft consensus algorithm with leader election and voting quorum. |
| **Vector Memory & AgentDB** | `memory_store`, `memory_search`, `embeddings_generate` | 384-dimensional HNSW vector index persisted in local SQLite. |
| **Static Code & Risk Analysis**| `analyze_file-risk`, `analyze_diff`, `policy_evaluate` | Local AST parsing, file risk scoring, and rule evaluation. |
| **Task Management** | `task_create`, `task_list`, `task_complete` | Internal durable task queue. |

---

## 2. What Requires an External API Key (And How We Bypass It)

- **`agent_execute` / `wasm_agent_prompt`**: Attempts to make external HTTP calls to `api.anthropic.com` or `openrouter.ai`. Fails with `No LLM provider configured` if keys are missing.
- **The Zero-Key Solution**: Instead of using `agent_execute`, **Antigravity Gemini native subagents** act as the cognitive execution workers. The subagents read code, perform reasoning, and record the results into Ruflo's Raft consensus ledger and HNSW memory.

---

## 3. Tool Reference & Signatures

### A. Swarm Initialization (`swarm_init`)
```json
{
  "topology": "hierarchical",
  "maxAgents": 5,
  "strategy": "specialized"
}
```

### B. Agent Registration (`agent_spawn`)
```json
{
  "name": "ruflo-orchestrator-lead",
  "role": "orchestrator",
  "capabilities": ["system-orchestration"]
}
```

### C. Raft Consensus Proposal (`hive-mind_consensus`)
```json
{
  "action": "vote",
  "hiveId": "hive-1790433946013",
  "proposalId": "proposal-xxx",
  "vote": "approve"
}
```

### D. HNSW Vector Memory Store (`memory_store`)
```json
{
  "namespace": "audits",
  "key": "audit_orchestration_engine",
  "value": { "status": "verified", "critical_bugs": 5 }
}
```

### E. File Risk Analysis (`analyze_file-risk`)
```json
{
  "path": "apps/brain/core/orchestration_engine.py",
  "status": "modified",
  "additions": 50,
  "deletions": 20
}
```
