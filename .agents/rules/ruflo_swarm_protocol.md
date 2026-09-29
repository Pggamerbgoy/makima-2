# MANDATORY: Ruflo Swarm & MCP Zero-API-Key Execution Protocol

## Core Mandate (Strict — No Exceptions)

Ruflo in Makima is configured as a **Zero-API-Key Swarm Coordination Platform**. 
You must NEVER invoke external paid LLM endpoints (Anthropic, OpenAI, OpenRouter) or call Ruflo's internal `agent_execute` / `wasm_agent_prompt`.

All multi-agent reasoning, code writing, and audits must be executed by **Antigravity Native Execution Workers** using the prompts and tools managed by **Ruflo Local MCP & Bridge**.

---

## 1. The Two-Plane Zero-Key Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  PLANE 1: RUFLO LOCAL COORDINATION & MCP (100% Offline / Local)        │
│  • Swarm Topologies: swarm_init (Hierarchical, Mesh, Adaptive)         │
│  • Raft / Byzantine Consensus: hive-mind_consensus, hive-mind_init     │
│  • 384d HNSW Vector Memory: memory_store, memory_search (local SQLite) │
│  • AST Code Analysis & Graph: analyze_boundaries (MinCut), complexity  │
│  • AI Defense & Guardrails: aidefence_has_pii, security channel-scan   │
│  • 77 Agent Prompt Library: .agents/ruflo_swarm/catalog/ & registry/   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Zero external API calls)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PLANE 2: ANTIGRAVITY COGNITIVE EXECUTION (Zero Paid API Key)         │
│  • Native Gemini Model acts as the swarm worker node.                  │
│  • Loads agent prompt: python .agents/ruflo_swarm/bridge.py --prompt   │
│  • Inspects code line-by-line, runs tests, drafts patches.             │
│  • Records findings & votes back into Ruflo MCP Raft & HNSW Memory.    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Hard Anti-Patterns (Strictly Banned)

- ❌ **NEVER call `ruflo agent spawn` with external provider** or run `agent_execute` (fails with `No LLM provider configured`).
- ❌ **NEVER modify Makima core application code** (`apps/brain/`, `apps/chat_ui/`) for swarm orchestration. All swarm assets live strictly in `.agents/ruflo_swarm/`.
- ❌ **NEVER perform an audit without running caller/callee cross-inspection** (unit test passes alone are never proof of correctness).
- ❌ **NEVER guess agent prompts from memory** — always query `python .agents/ruflo_swarm/bridge.py --prompt <agent_id>`.

---

## 3. How to Use Ruflo Agents (Zero-Key Workflow)

When executing any task requiring a specialized agent role:

### Step 1: Identify the Agent & Dependencies
Check the catalog of 77 agents:
```powershell
# List all 77 agents with system and MCP tool requirements
python .agents/ruflo_swarm/bridge.py --list-all

# Check specific agent dependencies
python .agents/ruflo_swarm/bridge.py --deps <agent_id>
```

### Step 2: Load the System Prompt
Fetch the exact, battle-tested system prompt from the catalog:
```powershell
python .agents/ruflo_swarm/bridge.py --prompt <agent_id>
```
The Antigravity subagent adopts this prompt's exact objectives and guidelines.

### Step 3: Execute Local Tools & Verification
Execute the local Ruflo tools corresponding to the stage:
- AST Boundaries (MinCut blast radius): `python .agents/ruflo_swarm/bridge.py --boundaries <path>`
- AST Complexity: `python .agents/ruflo_swarm/bridge.py --complexity <path>`
- PII / Secret Scan: `python .agents/ruflo_swarm/bridge.py --scan-pii <path>`

---

## 4. How to Use the 7 Official Workflow DAGs

Ruflo provides 7 standard workflow templates. Always check the sequence before executing multi-stage work:
```powershell
# List all 7 workflows
python .agents/ruflo_swarm/bridge.py --list-workflows

# Inspect stage-by-stage handoff sequence for any workflow
python .agents/ruflo_swarm/bridge.py --workflow <development|sparc|security-audit|code-review|refactoring|testing|research>
```

### Official Flow & Handoff Standard:

1. **`development`**:
   - `Planning` (`planner`) ➔ `Implementation` (`coder`) ➔ `Testing` (`tester`) ➔ `Review` (`reviewer`) ➔ `Integration` (`pr-manager` + `raft-manager`).
2. **`sparc`**:
   - `Specification` (`specification`) ➔ `Pseudocode` (`pseudocode`) ➔ `Architecture` (`architecture`) ➔ `Refinement` (`refinement`) ➔ `Completion` (`production-validator` + `raft-manager`).
3. **`security-audit`**:
   - `Threat Model` (`security-manager`) ➔ `Static Analysis` (`ruflo-security-auditor`) ➔ `Dynamic Analysis` (`byzantine-coordinator`) ➔ `Report` (`ruflo-orchestrator-lead`).
4. **`code-review`**:
   - `Initial Review` (`reviewer`) ➔ `Security Check` (`ruflo-security-auditor`) ➔ `Quality Analysis` (`ruflo-code-auditor`) ➔ `Feedback` (`code-review-swarm`).
5. **`refactoring`**:
   - `Analysis` (`architecture`) ➔ `Planning` (`planner`) ➔ `Refactor` (`coder`) ➔ `Validation` (`tester`).
6. **`testing`**:
   - `Unit Tests` (`tdd-london-swarm`) ➔ `Integration Tests` (`tester`) ➔ `E2E Tests` (`production-validator`) ➔ `Performance Tests` (`ruflo-perf-auditor`).
7. **`research`**:
   - `Discovery` (`researcher`) ➔ `Analysis` (`code-analyzer`) ➔ `Synthesis` (`ruflo-orchestrator-lead`) ➔ `Documentation` (`api-docs`).

---

## 5. How to Use Ruflo MCP Tools in-Session

| MCP Tool | Purpose | Zero-Key Mechanism |
|---|---|---|
| `swarm_init` | Setup topology (`hierarchical` / `mesh`) | In-memory mesh routing in Node.js daemon |
| `hive-mind_consensus` | Submit Raft quorum vote on audit/code | Local distributed consensus state machine |
| `memory_store` | Save verified decision receipt | In-process SQLite HNSW 384d vector index |
| `memory_search` | Query past architectures & patterns | Local vector similarity search |
| `analyze_file-risk` | Git diff risk score (0-100) | Local AST syntax tree diffing |
| `aidefence_has_pii` | Scan text for leaked API keys / PII | Local regex and heuristic filters |

---

## 6. Fast Verification Gate

Before completing any task, verify the integrity of the swarm bundle:
```powershell
python .agents/ruflo_swarm/bridge.py --verify
```
Must return `Bundle Valid: True` with all 34 MCP schemas, 5 core auditors, 74 catalog agents, and 4 modules green.
