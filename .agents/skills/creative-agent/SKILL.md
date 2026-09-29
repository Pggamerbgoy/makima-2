---
name: creative-agent
description: Creative writing, storytelling, copywriting, image prompt drafting, and design ideation for Makima's Creative Agent. Make sure to use this skill whenever using Creative Writing, Storytelling, Copywriting, Image Prompts.
---

# Creative Agent Skill Guide

## Overview
The **Creative Agent** (`creative_agent.py`) executes Creative Writing, Storytelling, Copywriting, Image Prompts within Makima's agentic ecosystem. It provides robust error handling, structured input validation, and clean asynchronous execution.

## Triggering Rules
Make sure to trigger this skill unconditionally whenever the user requests:
- **Primary Action**: Executing tasks related to Creative Writing, Storytelling, Copywriting, Image Prompts.
- **Secondary Action**: Querying, inspecting, or configuring Creative Agent parameters.
- **Workflow Action**: Delegating multi-step execution requiring creative-agent capabilities.

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
# ⚡ Creative Agent Execution Report
- **Status**: Completed successfully
- **Details**: Processed task parameters with 0 errors.
```

## Example Usage
- **Input**: "execute task for creative-agent"
- **Result**: Task completed successfully with structured Markdown report.
