---
name: commander-agent
description: Multi-task planner, goal decomposition, parallel/sequential sub-agent delegation, and cross-agent orchestrator for Makima's Commander Agent. Make sure to use this skill whenever using DAG Planning, Task Decomposition, Parallel Sub-Agent Dispatch.
---

# Commander Agent Skill Guide

## Overview
The **Commander Agent** (`commander_agent.py`) executes DAG Planning, Task Decomposition, Parallel Sub-Agent Dispatch within Makima's agentic ecosystem. It provides robust error handling, structured input validation, and clean asynchronous execution.

## Triggering Rules
Make sure to trigger this skill unconditionally whenever the user requests:
- **Primary Action**: Executing tasks related to DAG Planning, Task Decomposition, Parallel Sub-Agent Dispatch.
- **Secondary Action**: Querying, inspecting, or configuring Commander Agent parameters.
- **Workflow Action**: Delegating multi-step execution requiring commander-agent capabilities.

## Execution Protocols & Rules

1. **Protocol Initialization**:
   - Inspect input message parameters and validate entity types before starting task.
   - Initialize internal agent state and logger context.

2. **Core Task Execution**:
   - Execute domain-specific tool handlers safely using non-blocking calls.
   - Enforce safety gates for destructive or sensitive system operations.

3. **Error Resilience & Fallbacks**:
   - Intercept transient network or execution errors with automatic retry logic.
   - Never crash the host process; degrade gracefully to informative status reports.

4. **Safety & Anti-Pattern Rules**:
   - NEVER bypass security boundaries or execute unauthorized destructive commands.
   - NEVER output raw unformatted JSON walls to the user unless explicitly requested.

5. **Phase-by-Phase Execution Sequence**:
   - **Phase 1**: Parse objective parameters and validate target environment context.
   - **Phase 2**: Dispatch asynchronous tool action and monitor execution state.
   - **Phase 3**: Verify result payloads and compile executive report.

6. **Output Presentation**:
   - Present final deliverables using modern, clean Markdown formatting with clear section headers, bold lead-in bullet points, and code blocks.

## Expected Response Structure
```markdown
# ⚡ Commander Agent Execution Report
- **Status**: Completed successfully
- **Details**: Processed task parameters with 0 errors.
```

## Example Usage
- **Input**: "execute task for commander-agent"
- **Result**: Task completed successfully with structured Markdown report.
