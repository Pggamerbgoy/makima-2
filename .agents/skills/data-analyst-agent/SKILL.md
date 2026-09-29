---
name: data-analyst-agent
description: Data analysis, statistical summary, CSV/JSON metrics processing, trend visualization, and chart generation for Makima's Data Analyst Agent. Make sure to use this skill whenever using Statistical Metrics, CSV/JSON Processing, Chart Visualization.
---

# Data Analyst Agent Skill Guide

## Overview
The **Data Analyst Agent** (`data_analyst_agent.py`) executes Statistical Metrics, CSV/JSON Processing, Chart Visualization within Makima's agentic ecosystem. It provides robust error handling, structured input validation, and clean asynchronous execution.

## Triggering Rules
Make sure to trigger this skill unconditionally whenever the user requests:
- **Primary Action**: Executing tasks related to Statistical Metrics, CSV/JSON Processing, Chart Visualization.
- **Secondary Action**: Querying, inspecting, or configuring Data Analyst Agent parameters.
- **Workflow Action**: Delegating multi-step execution requiring data-analyst-agent capabilities.

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
# ⚡ Data Analyst Agent Execution Report
- **Status**: Completed successfully
- **Details**: Processed task parameters with 0 errors.
```

## Example Usage
- **Input**: "execute task for data-analyst-agent"
- **Result**: Task completed successfully with structured Markdown report.
