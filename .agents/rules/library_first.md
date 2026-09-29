# MANDATORY: Library-First Verification Protocol (Always-On)

## Core Mandate (Strict — Zero Exceptions)

**Before writing ANY code** — functions, classes, utilities, helpers, scripts,
config parsers, HTTP clients, schedulers, serializers, validators, state machines,
retry logic, regex patterns, token counters, path resolvers, or ANY other
implementation — you MUST first verify whether an **existing library, SDK,
built-in module, framework utility, or external resource** already provides that
capability.

This rule is **NOT limited to AI models or LLM SDKs**. It applies universally to:

- 📦 **Third-party libraries** (pip packages already installed in the project)
- 🐍 **Python standard library** (stdlib modules: `pathlib`, `json`, `re`, `asyncio`, `functools`, `itertools`, `dataclasses`, `contextlib`, `typing`, etc.)
- 🔧 **SDKs and framework utilities** (OpenAI Agents SDK, Pydantic, FastAPI, SQLAlchemy, etc.)
- 🤖 **AI model capabilities** (structured output, JSON mode, function calling, embeddings — don't hand-roll what the model does natively)
- 🌐 **External services and APIs** (don't build a currency converter if an API call works)
- 🏗️ **Existing Makima subsystems** (don't shadow `ToolRegistry`, `OrchestrationEngine`, `EternalMemory`, `PreferenceEngine`, `DurableTaskEngine`, `WebSearchTool`, etc.)

Writing custom code when a library/SDK/built-in already handles it is **STRICTLY BANNED**.

---

## The 3-Question Gate (Answer Before Writing Any Code)

Before writing any implementation, explicitly answer these 3 questions:

### Q1 — Does Python stdlib already do this?
Check: `pathlib.Path`, `json.loads`, `re`, `functools.lru_cache`, `itertools`,
`collections.defaultdict`, `contextlib.suppress`, `dataclasses`, `typing.get_type_hints`,
`inspect.signature`, `hashlib`, `base64`, `urllib.parse`, `datetime`, `asyncio`, etc.

### Q2 — Does an already-installed package do this?
Check `pyproject.toml` / `requirements.txt` for installed packages FIRST.
Common examples:
- **Pydantic** → data validation, JSON schema generation, TypeAdapter, model serialization
- **httpx / aiohttp** → HTTP requests (don't hand-roll connection pools)
- **SQLite / aiosqlite** → persistence (don't build a custom key-value store)
- **APScheduler / asyncio** → scheduling and timers
- **psutil** → system stats (CPU, RAM, disk — don't reimplement)
- **rapidfuzz / difflib** → fuzzy matching
- **jinja2** → templating
- **loguru / logging** → logging

### Q3 — Does an existing Makima module already do this?
Run a grep search across `apps/brain/` before writing. Key existing capabilities:
- JSON parsing → `ai_handler.try_parse_json()`
- Web search → `web_search_tool.py` (httpx-based, battle-tested)
- Tool registration → `ToolRegistry` in `tool_registry.py`
- Window management → `manage_window()` in `system_tools.py`
- Memory storage → `EternalMemory` in `eternal_memory.py`
- Task scheduling → `DurableTaskEngine` in `durable_task_engine.py`
- Preference storage → `PreferenceEngine` in `preference_engine.py`
- Type schema synthesis → `pydantic.TypeAdapter(ann).json_schema()`

---

## Hard Anti-Patterns (Strictly Prohibited)

| ❌ Banned Pattern | ✅ Use Instead |
|---|---|
| Hand-rolling JSON schema from type annotations | `pydantic.TypeAdapter(ann).json_schema()` |
| Writing a retry decorator from scratch | `tenacity` library or `asyncio` backoff |
| Custom HTTP connection pool | `httpx.AsyncClient` with `limits=` |
| Hand-rolling fuzzy string match | `rapidfuzz.fuzz.ratio()` or `difflib` |
| Writing a path sanitizer from scratch | `pathlib.Path(...).resolve()` |
| Custom token counter | Model provider's `count_tokens()` or `tiktoken` |
| Reimplementing CPU/RAM stats | `psutil.cpu_percent()`, `psutil.virtual_memory()` |
| Custom SQLite connection manager | `contextlib.closing()` + stdlib `sqlite3` |
| Building a new scheduler | `DurableTaskEngine` or `APScheduler` |
| Custom regex for code fences | Reuse `CANVAS_RE` in `orchestration_engine.py` |
| New web search implementation | Reuse `web_search_tool.py` |
| Custom window focus/bring-to-front | Reuse `manage_window()` in `system_tools.py` |
| Re-registering tools in a new registry | Use existing `ToolRegistry` |
| New dispatch/routing mechanism | Use `OrchestrationEngine` / `sdk_bridge.py` |

---

## Enforcement Gate (Required Before Every Implementation)

Before writing a single line of implementation code, your reasoning MUST
explicitly state:

```
[LIBRARY CHECK] Capability: <what I need to implement>
[LIBRARY CHECK] Stdlib check: <stdlib module checked, result>
[LIBRARY CHECK] Installed packages check: <packages checked, result>
[LIBRARY CHECK] Existing Makima module check: <files/functions checked, result>
[LIBRARY CHECK] Decision: <USE EXISTING: X> or <CREATE NEW because: specific gap>
```

**If any of the three checks is skipped, the implementation is INVALID.**

---

## Why This Rule Exists

Hand-rolled implementations of things that libraries already handle introduce:
- **Hidden bugs** (edge cases the library already solved)
- **Maintenance burden** (now you own it forever)
- **Duplication** (another implementation to keep in sync)
- **Security gaps** (libraries are audited; hand-rolled code is not)
- **Performance issues** (libraries are optimized; hand-rolled rarely is)

The cost of a 30-second grep + pyproject.toml check is zero compared to the cost
of maintaining a custom reimplementation indefinitely.

## Interaction With Other Rules

This rule works in tandem with:
- `pre_implementation_discovery.md` — which governs duplicate Makima-internal code
- `reasoning.md` — which mandates `[SKILL CHECK]` before every task

**All three rules are always-on and non-negotiable.**
