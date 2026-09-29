# Makima Project — OpenCode Rules

## MANDATORY: Library-First Verification Protocol (Always-On)

Before writing ANY code, verify whether an existing library, SDK, built-in, or
Makima subsystem already provides that capability. This is non-negotiable.

Applies to: stdlib, pip packages, AI model native capabilities, SDKs, external
APIs, and all existing Makima modules.

### 3-Question Gate

**Q1 — Python stdlib?** (`pathlib`, `json`, `re`, `functools`, `asyncio`, etc.)
**Q2 — Installed package?** Check `pyproject.toml` first. (Pydantic, httpx, psutil, etc.)
**Q3 — Existing Makima module?** Grep `apps/brain/` before writing.

### Required Block Before Every Implementation

```
[LIBRARY CHECK] Capability: <what I need>
[LIBRARY CHECK] Stdlib check: <result>
[LIBRARY CHECK] Installed packages check: <result>
[LIBRARY CHECK] Existing module check: <result>
[LIBRARY CHECK] Decision: USE EXISTING: X  OR  CREATE NEW because: <gap>
```

### Makima Canonical Implementations (Do NOT Duplicate)

| Need | Use This |
|---|---|
| JSON parsing | `ai_handler.try_parse_json()` |
| Web search / HTTP | `web_search_tool.py` (httpx-based) |
| Tool registration | `ToolRegistry` in `tool_registry.py` |
| Window management | `manage_window()` in `system_tools.py` |
| Memory storage | `EternalMemory` in `eternal_memory.py` |
| Task scheduling | `DurableTaskEngine` in `durable_task_engine.py` |
| Preferences | `PreferenceEngine` in `preference_engine.py` |
| Type → JSON schema | `pydantic.TypeAdapter(ann).json_schema()` |
| System stats | `psutil.cpu_percent()` / `psutil.virtual_memory()` |
| Canvas regex | Reuse `CANVAS_RE` in `orchestration_engine.py` |
| AI dispatch | `OrchestrationEngine` / `sdk_bridge.py` |

### Hard Anti-Patterns (Strictly Banned)

- ❌ New parallel utility alongside existing one
- ❌ New HTTP client when `web_search_tool.py` works
- ❌ New routing layer alongside `OrchestrationEngine`
- ❌ New tool registry alongside `ToolRegistry`
- ❌ Hand-rolling what `pydantic`, `psutil`, `httpx`, or the LLM SDK handles natively

---

## MANDATORY: No Duplication of Existing Modules

See `.agents/rules/pre_implementation_discovery.md` for the full 3-step discovery
protocol. Run grep searches before creating any new file or function.

## MANDATORY: Minimal Diff — Smallest Possible Change

See `.agents/rules/minimal_diff.md`. Never rewrite a whole function to fix 2 lines.
Required gate: `[MINIMAL DIFF]` block before every edit.

## MANDATORY: Verify Before Implement

See `.agents/rules/verify_before_implement.md`. Reproduce the bug with a failing
test or command output BEFORE writing any fix. Required gate: `[VERIFY]` block.

## MANDATORY: No Speculative Code

See `.agents/rules/no_speculative_code.md`. No TODOs, unused params, future-proof
abstractions, or unrequested features. Only implement what was explicitly asked.
Required gate: `[SCOPE]` block.

## MANDATORY: Dead Code Cleanup After Every Edit

See `.agents/rules/dead_code_cleanup.md`. After every patch, check for and remove
orphaned functions, unused imports, and unreachable branches. Required gate:
`[DEAD CODE CHECK]` block.

## MANDATORY: Check Git History Before Changing Existing Logic

See `.agents/rules/check_git_before_fix.md`. Before changing code that looks
"wrong," run `git log -p` and check `docs/DEVLOG.md` first. Required gate:
`[GIT CHECK]` block.

## MANDATORY: Skill Check on Every Response

Every response must start with `[SKILL CHECK]` with `master-workflow` listed first.
See `.agents/rules/reasoning.md`.

---

## Project Setup

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest

# Start brain server
python -m apps.brain.main
```

## Test Baseline
- **338 tests** must pass before and after every change.
- Run: `python -m pytest --tb=short -q`

## Key Files

| File | Purpose |
|---|---|
| `apps/brain/core/orchestration_engine.py` | Main AI dispatch engine (771 LOC) |
| `apps/brain/core/sdk_bridge.py` | OpenAI Agents SDK bridge (2903 LOC) |
| `apps/brain/tools/system_tools.py` | OS/desktop automation tools (4267 LOC) |
| `apps/brain/eternal_memory.py` | Long-term vector memory |
| `apps/brain/core/preference_engine.py` | User preference & learning |
| `apps/brain/core/durable_task_engine.py` | Durable task checkpointing |
| `apps/brain/personality.py` | Personality & emotion engine |
| `apps/brain/web_search_tool.py` | Web search (httpx, battle-tested) |
| `docs/DEVLOG.md` | Engineering decision log |

## Constraints

- Python 3.14+ on Windows (PowerShell). No Linux/Mac assumptions.
- No `asyncio.run()` in subprocesses — use `asyncio.to_thread()`.
- `duckduckgo_search` library is broken on Windows — do NOT use it.
- All code must be real, production-ready — zero mocks.
- No external API keys (no Anthropic, no OpenAI key in environment).
