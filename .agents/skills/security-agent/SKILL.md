---
name: security-agent
description: Vulnerability scanning, hardcoded secret detection, SQL injection checks, dependency audit, and input sanitization for Makima's Security Agent. Make sure to use this skill whenever using Static Vulnerability Scan, Secret Leak Detection, Injection Audit.
---

# Security Agent Skill Guide

## Overview
The **Security Agent** (`security_agent.py`) executes Static Vulnerability Scan, Secret Leak Detection, Injection Audit within Makima's agentic ecosystem. It provides robust error handling, structured input validation, and clean asynchronous execution.

## Triggering Rules
Make sure to trigger this skill unconditionally whenever the user requests:
- **Primary Action**: Executing tasks related to Static Vulnerability Scan, Secret Leak Detection, Injection Audit.
- **Secondary Action**: Querying, inspecting, or configuring Security Agent parameters.
- **Workflow Action**: Delegating multi-step execution requiring security-agent capabilities.

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
# ⚡ Security Agent Execution Report
- **Status**: Completed successfully
- **Details**: Processed task parameters with 0 errors.
```

## Example Usage
- **Input**: "execute task for security-agent"
- **Result**: Task completed successfully with structured Markdown report.
