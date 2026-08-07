# Claude Session Log — Makima v7.1

Running log of every fix, decision, and architectural change made by Claude
during live debugging/rebuild sessions on this codebase. Kept in-repo (not
just in chat history) so nothing is lost if a session ends, a connector
drops, or files move between drives.

Append-only. Newest entries at the bottom. Each entry: what was found, what
was changed, why, and any tradeoff worth remembering later.

**Lesson learned twice now (2026-07-18, then again 2026-07-23): the
FileSystem write tool used to edit this file OVERWRITES on every call,
regardless of an "overwrite" parameter. Always read this file fresh and
write the FULL combined content back — never write only the new entry.**

---

## 2026-07-18 — Session 1

### Context
Working via a Windows-MCP connector (PowerShell + FileSystem access) directly
on pg's machine. Original path was `D:\code\makima`; mid-session this path
vanished after a Windows 11 → 10 reinstall. Turned out to be a broken
junction/symlink — the real project was untouched at `C:\code\makima` the
whole time. All fixes below are confirmed present on `C:\code\makima`.

### Fixed: `uia_bridge.py` was missing entirely
`main.py` imported `UIABridge` from a file that didn't exist → guaranteed
`ImportError` on startup. Wrote a full implementation: `pywinauto`-backed
find/click/set_text/get_element_tree, 3-retry policy, 5s timeout per lookup,
coordinate-click fallback if UIA resolution fails, graceful no-op if
`pywinauto` isn't installed on the machine.

### Fixed: `EternalMemory.search()` / `.save_turn()` / `.get_history()` misuse
These are `async def` methods that already handle their own executor
offloading internally. `command_router.py` was wrapping them in
`run_in_executor()` again — which doesn't run an async function, it just
constructs an unawaited coroutine and returns immediately. Net effect:
memory search always returned a coroutine object (not a list — would break
any downstream slicing), and **every conversation turn was silently never
being saved to memory at all**. Fixed all 4 call sites to await these
methods directly.

### Fixed: `EntityExtractor` wiring + never started
`main.py` was calling `EntityExtractor(ai_handler, memory.triple_store, CONFIG, ws_broadcast=ws_broadcast)`
— `EternalMemory` has no `triple_store` attribute (AttributeError), and even
past that, the positional args didn't match the constructor signature
(duplicate `ws_broadcast` kwarg → TypeError). This has since been corrected
independently (found already-fixed on `C:\` — likely via another tool in pg's
workflow, e.g. Antigravity/OpenCode).
Wired `entity_extractor.submit_turn(message, context)` into
`command_router.handle_message()` so real conversation turns actually reach
the background extraction thread — previously nothing fed it at all, so even
a correct constructor would've sat idle forever.

### Verified already-fixed (found in this state independently, not by Claude)
- `memory_forget.py` wiring into `main.py` / `_modules` / WS approve-action
  handler — done, and it degrades gracefully (clear message) when the Rust
  triple store / vector index aren't compiled yet.
- `agent_orchestrator.py`: missing `await` on `self.memory.save_draft(...)`
  — already fixed.

---

## 2026-07-18 — Session 1 (continued, after C:\ discovery)

### Correction to an earlier claim in this same log
I previously wrote that the 5 "hollow" agents (`research_agent.py`,
`code_agent.py`, `memory_agent.py`, `creative_agent.py`,
`messaging_agent.py`) were "unchanged since original analysis" based on
matching file sizes. That was wrong — I was comparing sizes against a size
check done on the correct `C:\` copy, but assumed it meant "same content as
my original `D:\` read." On actually re-reading the content:

- `research_agent.py`, `code_agent.py` — already properly dispatch the
  `web_search` tool via a JSON tool-decision pattern, with retry-on-failure
  logic in the research agent. Genuinely well-built.
- `memory_agent.py` — already dispatches `memory_search`/`memory_forget`
  tools correctly.
- `messaging_agent.py` — already dispatches `messaging_search_draft`/
  `messaging_read`, both of which are real `MessagingHub` methods, not
  stubs.
- `creative_agent.py` — correctly minimal (pure LLM task, no tool needed).

None of these needed rewriting. The actual gap was one level lower.

### Fixed: `web_search` was a stub — now a real implementation
`tool_registry.register_tool("web_search", ...)` in `main.py` was wired to
`_stub_tool("web_search")`, which just returns
`"[Tool 'web_search' not yet implemented...]"`. Since `research_agent.py`
and `code_agent.py` both genuinely depend on this tool, this was the actual
blocker, not the agents themselves.

No search API key exists in `.env` (only `MAKIMA_OPENROUTER_KEY`), so
implemented a real backend using DuckDuckGo's HTML endpoint — no signup
needed, `httpx` was already a dependency. New file:
`apps/brain/web_search_tool.py`. Wired into `main.py` in place of the stub.
Live-tested end to end — returns real, correctly parsed results.

**Known interim workaround (flagged in code + here):** `httpx.AsyncClient`
uses `verify=False` in this file. This machine has something (AV/proxy)
doing HTTPS inspection that breaks Python's default certificate trust chain
— confirmed via direct test (`verify=True` → `SSLCertVerificationError`;
`verify=False` → works). **This turned out to affect far more than search
— see Session 2 below.**

### Done: real guardrail enforcement (tool-call limit)
- `agent_guardrails.py`: rewritten. `max_tokens`/token-budget concept
  removed entirely per pg's direction (see cost-tracker removal below) —
  now only enforces `max_wall_time_s` (checked by the orchestrator via
  `asyncio.wait_for`, since only the orchestrator can interrupt an agent
  from outside) and `max_tool_calls` (checked inside the agent itself).
  Added `GuardrailExceeded` exception carrying the violation reason.
- `agents/base_agent.py`: now accepts `guardrails` in `__init__`; added
  `set_execution_mode(is_interactive)` so the orchestrator can tell an
  agent which limit tier applies to *this* run; `_use_tool()` now actually
  checks `guardrails.check_limits()` after incrementing the tool-call
  counter and raises `GuardrailExceeded` if hit (self-cancels first, so
  any further `_llm_call`/`_use_tool` in the same `execute()` short-circuits
  immediately).
- `agent_orchestrator.py`: passes `guardrails` into every agent at
  construction; uses the real `is_background` dispatch flag to pick the
  limit tier (previously this was a hardcoded check for
  `agent_name == "commander_agent"`, which didn't reflect actual dispatch
  context); catches `GuardrailExceeded` distinctly from timeouts/generic
  errors and reports the real reason; fixed a real dead-tracking bug where
  `TaskResult.tokens_used`/`.tool_calls` always reported 0 (was reading an
  orchestrator-side field that got reset to 0 at dispatch and never
  updated — now pulls real stats from `agent.get_execution_stats()` after
  every run).

### Architectural decision: removed CostTracker + token-limit guardrail
(pg's explicit direction, Hinglish: "token limit cost tracker ko hta do
bekaar hai ye dono")
- `cost_tracker.py` deleted from the repo entirely.
- `ai_handler.py`: dropped the `cost_tracker` constructor param/attribute,
  the cost-recording block after each successful backend call, and the
  cost-budget gate before attempting a backend. `rate_limit_manager`'s
  pre-flight check — a different, legitimate concern (staying under
  provider rate limits, not cost) — was left untouched; it shares the same
  `estimated_tokens` value but never depended on `cost_tracker`.
- `main.py`: removed the `CostTracker` import/instantiation and its
  `_modules` registry entry.

### Fixed: `entity_extractor.start()` / `.stop()` were still never called
Constructor was already correct (fixed independently, see previous log
entry), but the daemon thread never actually started. Added
`entity_extractor.start()` to the background-services startup block and
`entity_extractor.stop()` to shutdown, in `main.py`.

### Verification performed this session
- All 49 `.py` files under `apps/brain` compile clean
  (`python -m py_compile`, zero failures).
- `web_search_tool.py` live-tested against real DuckDuckGo results — works.
- Confirmed no agent subclass's `__init__` breaks with the new `guardrails`
  param (only `commander_agent.py` overrides `__init__`, and it's a clean
  `*args, **kwargs` passthrough to `super()`).

### Still open (as of end of Session 1)
- Rust core (`makima-core`) never compiled via `maturin develop` — brain
  currently runs entirely in graceful-degrade mode for memory/graph
  features that depend on it.
- `set_reminder` / `run_workflow` tools are still stubs (used by
  `automation_agent.py`, which was already functional otherwise — not
  touched this session, wasn't in scope).
- SSL cert trust issue on this machine not actually fixed at the root —
  `verify=False` is a workaround in `web_search_tool.py` only (at the time).

---

## 2026-07-23 — Session 2: Overlay window + LLM backend fixes

### Fixed: LLM backend pipeline was completely dead
`.env` only had `MAKIMA_OPENROUTER_KEY` — Groq/Gemini/GPT-4o all `enabled: true`
in config but no key, and Ollama wasn't running. `fast_chat` and
`intent_classification` routing lists had NO working backend at all (every
message goes through intent classification first) → `AllBackendsDownError`
on literally every message. Fixed: added `claude` (OpenRouter) as a working
fallback in both routing lists; added a pre-flight check to skip
keyless backends before wasting a network round-trip; switched the
OpenRouter-backed "claude" backend to free-tier models (general: currently
`meta-llama/llama-3.3-70b-instruct:free` per config, task-aware swap to a
coding-specialized free model for code/debugging/refactoring — see
`_OPENROUTER_FREE_CODE_MODEL` in `ai_handler.py`). **Free model IDs on
OpenRouter rotate frequently — if backend calls start 404ing, re-check
`https://openrouter.ai/api/v1/models` for current free IDs.**

### SSL interception blocks ALL outbound HTTPS on this machine
Confirmed via live testing: something (AV/proxy) breaks certificate
verification for httpx (Python), npm, AND cargo — three independent
toolchains, three different error signatures
(`CERTIFICATE_VERIFY_FAILED` / `UNABLE_TO_VERIFY_LEAF_SIGNATURE` /
`CRYPT_E_NO_REVOCATION_CHECK`). Workarounds applied per-tool (all interim,
not fixes): `verify=False` in all `httpx.AsyncClient()` calls in
`ai_handler.py` and `web_search_tool.py`; `npm config set strict-ssl false`
(temporarily, reverted after install); `CARGO_HTTP_CHECK_REVOKE=false` env
var needed for `cargo check`/`cargo build` on this machine going forward.
**Proper fix, not done yet:** find and trust the actual intercepting root CA.

### UI audit: Settings/Integrations pages are non-functional scaffolding
`ChatPage.tsx` and `ControlCenterPage.tsx` are genuinely wired to the
backend. `IntegrationsPage.tsx` and `SettingsPage.tsx` are not — "Test
Connection"/"Save"/"Export Now" buttons have no `onClick`, all config
inputs have no `value`/`onChange` (nothing persists, nothing loads).
Backend side is also incomplete: `/settings` POST only handles
`wake_word_enabled`+`ptt_key`; `/integrations` has no POST/save endpoint at
all. Not fixed yet this session — flagged for a dedicated pass along with
the "Cost Tracking" settings section, which is now fully orphaned since
`cost_tracker.py` was removed.

### Also found: widespread mojibake encoding corruption
Multi-generation UTF-8 mis-decoding (e.g. `ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬` where an
em-dash/emoji should be) across both `apps/ui/src/**/*.tsx` and
`apps/brain/main.py` docstrings/comments. Cosmetic, not functional, but
pervasive — needs a systematic find/replace pass, not touched this session
(stayed in scope of the overlay window + backend fixes).

### Built: overlay window (Copilot/Spotlight-style quick access)
Second Tauri window (`label: "overlay"`), frameless, transparent,
always-on-top, hidden from taskbar, toggled via a global `Alt+Space` hotkey
(chosen to avoid clashing with the existing PTT binding,
`Ctrl+Shift+Space`) — works even when Makima isn't focused, registered in
`src-tauri/src/lib.rs` via `tauri-plugin-global-shortcut`.

- `tauri.conf.json` — added the `overlay` window definition
  (`url: "index.html?window=overlay"`, 600x500, `decorations: false`,
  `transparent: true`, `alwaysOnTop: true`, `skipTaskbar: true`,
  `visible: false` by default).
- `capabilities/default.json` — extended to cover both `main` and
  `overlay` windows.
- `src-tauri/Cargo.toml` — added `tauri-plugin-global-shortcut = "2"`.
- `src-tauri/src/lib.rs` — registers `Alt+Space` at startup; handler shows
  + focuses the overlay if hidden, hides it if visible; also exposes a
  `toggle_overlay` Tauri command for a future UI-triggered toggle if
  needed.
- `apps/ui/src/pages/OverlayPage.tsx` (+ `.css`, new) — compact chat panel,
  reuses the existing `useMakimaWebSocket()` hook (same backend connection
  as the full `ChatPage`), shows the last 6 messages, `Esc` hides the
  window, input auto-focuses on open.
- `App.tsx` — synchronous query-param check (`?window=overlay`) decides
  whether to render `OverlayPage` or the full app shell, before first
  paint (no flash of the wrong UI).

**v1 scope, deliberately kept small:** hotkey toggle + Esc to hide only.
No cursor-following position, no auto-hide-on-blur, no open/close
animation yet — layered on top later without touching the show/hide
plumbing.

**Verified:** `npx tsc --noEmit` clean (one unrelated pre-existing warning
in `ChatPage.tsx`, not touched this session). `cargo check` —
`Finished 'dev' profile ... in 1m 00s`, no errors.

**Not yet tested:** an actual `npm run tauri dev` run to confirm the hotkey
fires and the overlay visually renders correctly — session ended before
that step. Do this first before relying on it.


## 2026-07-23 — Session 2 (continued): Settings/Integrations real wiring

### Built: real persistence for Settings + Integrations
Backend (`main.py` + new `apps/brain/user_settings_store.py`):
- New `UserSettingsStore` -- JSON-file-backed store at
  `configs/user_settings.json`. `get_settings()`/`update_settings()` for
  general settings (privacy_mode, backup_path, tts_engine/voice, wake_phrase,
  llm_backend_toggles); `save_integration_fields()`/`get_integration_fields()`
  (masked)/`get_integration_raw()` (unmasked, backend-only) for per-service
  credentials. Guards against overwriting a real stored secret with a
  masked placeholder that round-trips back from a GET-then-POST in the UI.
- `/settings` GET/POST expanded beyond just wake_word_enabled+ptt_key to
  cover the full general-settings bucket.
- New `/integrations/{id}` POST (save credentials) and
  `/integrations/{id}/test` POST (connectivity test -- real implementation
  for Telegram via `getMe`, honest "not yet implemented" for others rather
  than faking a pass).
- **Known, stated limitation**: none of this hot-reloads the already-running
  MessagingHub/AIHandler -- those read CONFIG once at startup. A saved
  credential or backend toggle takes effect on the *next* brain restart.
  API responses and the UI both say this explicitly rather than implying
  an instant connection. Full hot-reload is future work.

Frontend:
- `IntegrationsPage.tsx` -- rewritten. Fields are now controlled inputs
  keyed by a stable backend field key (not the display label), pre-filled
  from `GET /integrations`'s new `fields` data on mount, Save actually
  POSTs, Test Connection actually POSTs and shows the real pass/fail
  message.
- `SettingsPage.tsx` -- rewritten. Loads real values from `GET /settings`
  on mount. Every section (Voice, LLM Backends, Privacy, Export) now has a
  working Save button that POSTs and persists. Removed the "Cost Tracking"
  section entirely -- fully orphaned since `cost_tracker.py` was deleted
  earlier this session. "Export Now" is honest about not being wired to
  real backup automation yet (backup_path itself does save).
- Added missing `.text-success`/`.text-error`/`.spin` CSS classes to
  `IntegrationsPage.css` (needed by the new Test/Save feedback states,
  didn't exist before).

### Verified
- `npx tsc --noEmit` clean (only the same pre-existing unrelated
  `ChatPage.tsx` warning from before, not touched).
- All 57 `apps/brain/*.py` files compile clean.
- `UserSettingsStore` live-tested directly: settings persist, integration
  fields save/mask/unmask correctly, and the "don't clobber a real secret
  with a masked placeholder" guard behaves correctly on a simulated
  re-save.

### Mojibake cleanup -- attempted, mostly deferred
Tried an automated iterative cp1252/latin-1 round-trip fix. Worked cleanly
on lightly-corrupted strings (single-generation mojibake), confirmed safe
on plain ASCII (no-op). Failed on the worst cases in `main.py`'s websocket
docstring and similar -- those went through enough encode/decode cycles
(4-5+) that they don't reverse algorithmically with confidence. Attempted
hand-written replacements for the specific user-visible strings in
`SettingsPage.tsx`/`ControlCenterPage.tsx`/`useMakimaWebSocket.ts`, but
copy-pasting the corrupted source through the PowerShell connector
introduced yet another layer of mangling, so those replacements didn't
match and were abandoned rather than risk a bad edit. **Still open.** If
revisited: do the fix via a Python script that reads/writes the files
directly (as the settings-store work in this session did), never round-tripping
the corrupted text through PowerShell's own console display.

### Still open (end of Session 2)
- Mojibake cleanup (see above) -- cosmetic only, no functional impact.
- SSL root cause not fixed -- `verify=False`/`strict-ssl false`/
  `CARGO_HTTP_CHECK_REVOKE=false` are all interim workarounds across
  Python/npm/cargo respectively.
- Overlay window not yet runtime-tested (`npm run tauri dev` + Alt+Space).
- Settings/Integrations changes hot-reload on restart only, not live.
- Rust `makima-core` still never compiled via `maturin develop`.
- `set_reminder`/`run_workflow` tools still stubs.
- Free OpenRouter model IDs rotate -- re-check
  `https://openrouter.ai/api/v1/models` if backend calls start 404ing.


## 2026-07-23 — Session 2 (continued again): brain folder audit + CreativeAgent upgrade

### Found: significant parallel work by other tooling since earlier this session
`apps/brain` grew from 49 to 57 files; `main.py` alone grew from ~19KB to
~151KB. New files present that weren't here earlier today: `offline_queue.py`,
`focus_profiles.py`, `screen_reader.py`, `notification_hub.py`,
`health_aggregator.py`, `learning_engine.py`, `conversation_summarizer.py`,
`ollama_manager.py`, `reminder_service.py`, `agents/browser_agent.py`,
`agents/voice_agent.py`, `rate_limit_manager.py`, `app_learner.py`,
`conversation_export.py`, `skill_marketplace.py`, `watchdog_manager.py`,
`cost_analytics.py`, `agents/media_agent.py`, `workflow_builder.py`,
`task_manager.py`, `stt_postprocessor.py`, `calendar_adapter.py`,
`disk_guard.py`. All 57 files still compile clean.

**Noted but deliberately not touched:** `cost_analytics.py` exists and is
wired into `main.py` (usage summary + budget status) -- this is a
different class/file than the `cost_tracker.py` removed earlier this
session per pg's explicit instruction, added independently by whatever
other tool has been working on this repo in parallel. Not removing it
unilaterally since (a) it wasn't part of this turn's ask, (b) pg may want
it now, unclear. Flagging for pg's awareness rather than deciding for them.

**Confirmed fixed by the parallel work:** `set_reminder`/`run_workflow`
tools, flagged as stubs in an earlier log entry, are now wired to real
`reminder_service.py`/`workflow_builder.py` implementations.

### Improved: CreativeAgent -- real features added, not just a rewrite
Previous version was a flat LLM passthrough: one fixed system prompt, no
awareness of format, length, or revision requests. Added:
- **Format detection** (regex-based, 7 formats: haiku, poem, screenplay,
  song, social_post, essay, short_story) -- each gets specific craft
  guidance appended to the system prompt (e.g. haiku's 5-7-5 structure,
  screenplay's INT./EXT. conventions) instead of one generic instruction
  for every format.
- **Length-target honoring** -- "write a 100 word story" / "a 4 line poem"
  are extracted via regex and converted into a `max_tokens` hint passed to
  the LLM call, so explicit length requests actually get respected instead
  of the model picking whatever length it feels like.
- **Revision-turn detection** -- follow-ups like "make it funnier/shorter/
  darker" get flagged (when conversation history exists) so the system
  prompt explicitly tells the model to edit the previous piece rather than
  write something unrelated.
- **Format-aware temperature** -- 0.95 for poetry/lyrics/fiction (wants
  variety), 0.7 for essays/social posts (wants control).

Live-tested the pure detection/extraction logic (no LLM call needed) against
8 sample messages -- all format/length/revision detections correct.

### Scope decision, stated plainly
Given remaining session budget, did NOT attempt to touch all 57 files --
picked one genuinely under-featured agent (`CreativeAgent`, previously the
most bare-bones file in the whole agents/ directory) and improved it
solidly rather than making shallow, higher-risk changes across everything.
`automation_agent.py` was reviewed too (docstring claims "macro recording/
playback" that doesn't actually exist as a tool) but not changed this
session -- noted here for a future pass.

### Still open (end of Session 2, this pass)
- Everything listed at the end of the previous entry, still open.
- `automation_agent.py`'s docstring overclaims macro recording/playback
  capability that isn't actually implemented -- either build it or fix the
  docstring.
- The other 8-9 new agents/modules from the parallel work (browser_agent,
  voice_agent, media_agent, learning_engine, skill_marketplace, etc.)
  haven't been reviewed for quality/bugs yet -- only confirmed they compile.
- `cost_analytics.py` -- pg should decide explicitly whether this stays or
  goes, given the earlier removal of `cost_tracker.py` this session.


## 2026-07-23 — Session 2 (continued again): multi-tool coordination audit

### Context
pg confirmed: DeepSeek, MiMo, and Gemini are also actively working on this
codebase in parallel with these Claude sessions. This explains the rapid
growth (main.py 19KB -> 151KB, 49 -> 57 files) noted in the previous entry.

### Audited for conflicts/duplication between tools -- found none
Checked the two highest-risk-looking overlaps directly:
- `agents/browser_agent.py` vs existing `browser_controller.py` -- no
  conflict. Clean layering: `browser_controller.py` is the Playwright
  infrastructure, `browser_agent.py` is a new agentic-loop consumer of it
  (JSON tool-call loop, 10-iteration cap, privacy gate, self-correction on
  invalid tool names). Docstring explicitly states it replaces
  `AutomationAgent`'s old browser duties -- `automation_agent.py`'s own
  prompt already says to delegate browsing to it. No orphaned/dead code.
- `agents/voice_agent.py` vs existing `speech_orchestrator.py` -- same
  clean pattern: orchestrator stays the audio/whisper/TTS infrastructure
  layer, voice_agent is a new single-shot command layer (wake word
  toggle, TTS voice change, PTT config) sitting on top of it.

Also verified:
- `command_router.py`'s `INTENT_TO_AGENT` correctly routes to both new
  agents (`browser`, `voice` intents both wired, not orphaned).
- All 11 tool names referenced by `browser_agent.py`/`voice_agent.py`
  match their `main.py` `register_tool(...)` registrations exactly --
  no silent "unknown tool" mismatches.
- My earlier fixes from this session (memory.search/save_turn awaited
  correctly, not re-wrapped in run_in_executor; free-model routing;
  verify=False SSL workaround) are all still intact -- and one was
  independently improved: `_OPENROUTER_FREE_CODE_MODEL` got corrected
  from my placeholder `qwen/qwen3-coder:free` (which turned out not to
  exist on live OpenRouter) to `poolside/laguna-m.1:free`, which matches
  what I'd actually found in the live model list check earlier.
- **`MAKIMA_GROQ_KEY` is now set in `.env`** (wasn't there earlier this
  session) -- live-tested `intent_classification`: now resolves directly
  via `groq`/`llama-3.3-70b-versatile`, no fallback needed. The intended
  primary fast-path design is now actually live, not just falling back to
  OpenRouter.
- Full import test (not just `py_compile` syntax check) across all 24
  new/changed top-level modules, including `main.py` itself -- all import
  cleanly, no circular imports or missing dependencies.

### Verdict
The parallel multi-tool work (DeepSeek/MiMo/Gemini) on this codebase is
genuinely solid -- consistent architecture patterns, clean layering,
correct wiring, no conflicts found. No corrective action was needed this
pass.

### Still open
- `cost_analytics.py` -- pg still hasn't given an explicit keep/remove
  decision; leaving as-is until they do.
- `automation_agent.py`'s docstring still overclaims macro recording/
  playback that isn't implemented.
- Mojibake cleanup still open (cosmetic only).
- SSL root cause still not fixed at the source (workarounds only).
- Overlay window still not runtime-tested.
