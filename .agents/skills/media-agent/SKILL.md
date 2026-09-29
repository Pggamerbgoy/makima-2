---
name: media-agent
description: Music and video control for YouTube and Spotify Web Player, direct search URL navigation, playback controls, and volume management for Makima's Media Agent. Make sure to use this skill whenever using YouTube & Spotify Playback, Volume Control, Track Navigation.
---

# Media Agent Skill Guide

## Overview
The **Media Agent** (`media_agent.py`) executes YouTube & Spotify Playback, Volume Control, Track Navigation within Makima's agentic ecosystem. It provides robust error handling, structured input validation, and clean asynchronous execution.

## Triggering Rules
Make sure to trigger this skill unconditionally whenever the user requests:
- **Primary Action**: Executing tasks related to YouTube & Spotify Playback, Volume Control, Track Navigation.
- **Secondary Action**: Querying, inspecting, or configuring Media Agent parameters.
- **Workflow Action**: Delegating multi-step execution requiring media-agent capabilities.

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
# ⚡ Media Agent Execution Report
- **Status**: Completed successfully
- **Details**: Processed task parameters with 0 errors.
```

## Example Usage
- **Input**: "execute task for media-agent"
- **Result**: Task completed successfully with structured Markdown report.
