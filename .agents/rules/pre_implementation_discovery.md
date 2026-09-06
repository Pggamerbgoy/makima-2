---
trigger: always_on
---

# MANDATORY: Pre-Implementation Codebase Discovery & Zero-Duplication Protocol

## Core Mandate (Strict — No Exceptions)

Before creating ANY new feature, module, subsystem, class, function, tool, configuration parameter, or making architectural changes in Makima, **you MUST FIRST search and audit the entire existing codebase to verify whether the capability, logic, or structure already exists.**

Writing new code or creating new files without first thoroughly searching the project for existing implementations is **STRICTLY BANNED**.

---

## 1. Mandatory 3-Step Discovery Protocol (Before Writing Any Code)

Whenever a feature, fix, module, tool, or capability is requested:

1. **Step 1: Global Semantic & Exact Code Search**:
   - Run `grep_search` across `apps/brain/`, `apps/ui/`, `configs/`, `native/`, and scripts for relevant keywords, class names, function names, and capability descriptors.
   - Use `list_dir` to inspect relevant subsystem folders (e.g. `core/`, `agents/`, `tools/`, `utils/`) to see existing module boundaries.

2. **Step 2: Existing Implementation & Pattern Audit**:
   - Check if an existing module, class, handler, adapter, or tool already provides this functionality or a related abstraction.
   - Review how similar features are currently implemented in the repository (e.g., `ToolRegistry`, `ExecutionRuntime`, `BaseAgent`, `AgentCapabilityRegistry`, `ConnectorsRegistry`).

3. **Step 3: Reuse & Extend Assessment**:
   - **Reuse First**: If an existing function/class satisfies the requirement, use it directly.
   - **Extend Second**: If an existing function/class lacks a specific parameter, edge case, or capability, extend/refactor the existing code in-place rather than creating a separate alternative.
   - **Create ONLY when genuinely absent**: Create a new file or new top-level function only when the capability is 100% missing from the codebase.

---

## 2. Hard Anti-Duplication Rules (Strictly Prohibited Anti-Patterns)

- ❌ **No Parallel Utility Functions**: Never create a new helper (e.g. `parse_json_custom()`, `run_js()`, `get_app_path()`) if `ai_handler.try_parse_json()`, `media_agent._bc_dispatch()`, or `system_agent._find_app()` already exists.
- ❌ **No Duplicate Config Blocks**: Never invent new ad-hoc config keys without checking `configs/default.yaml` and `configs/user_settings.json` first.
- ❌ **No Shadow Subsystems**: Never create a separate dispatch or routing mechanism alongside `OrchestrationEngine`, `SemanticPlanner`, `AgentManager`, or `ToolRegistry`.
- ❌ **No Re-Inventing Protocol Messages**: Always check `apps/brain/ws_protocol.py` for existing `WSMessage` types, builders, and payload schemas before defining new message formats.

---

## 3. Enforcement Gate

Before drafting an implementation plan or writing code, your reasoning must explicitly confirm:
- *"Did I search the codebase to check if this capability/module already exists?"*
- *"Which existing files/functions/tools were inspected?"*
- *"Why is an in-place extension or reuse preferred over creating new code?"*
