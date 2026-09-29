---
name: module-design
description: Architecture protocol, module design, problem triage, case enumeration, and self-critique for non-trivial feature additions and refactoring. Make sure to use this skill whenever designing new modules, planning multi-file refactors, or structuring system components.
---

<!--
  HOW TO USE THIS FILE (pg ke liye, agent ke liye nahi):

  1. Already correctly placed at `.agents/skills/module-design/SKILL.md` —
     matches Google Antigravity's official skill path. No manual toggle
     needed (that was Cline's model) — Antigravity semantically triggers
     this from the `description` above, and `.agents/rules/reasoning.md`
     (Always-On Rule) also explicitly routes new-module/no-file-named tasks
     here as a backstop.
  2. Division of labor between the three files:
       rigorous-code-development = WHAT to verify before code is trusted
         (facts, package names, APIs)
       problem-reasoning = HOW to reason correctly about evidence once you
         have it
       module-design (this file) = WHAT ORDER to do things in, specifically
           for module/feature-level work — especially the case pg described:
           "banao a voice module" with no file named. Before the agent is
           allowed to touch an editor, it must reason through *why* the
           module exists, *every* case it needs to handle, research the
           *current best* way to handle each case, and critique its own
           design — THEN write code.
  3. This file governs the planning phase specifically for module/feature-
     level asks (new module, or "improve/fix X"), not tiny isolated edits.
     For a one-line bugfix, rigorous-code-development and problem-reasoning
     alone are enough.
  4. v2 changelog: added Step 0, an explicit triage gate (Tier 1/2/3) with a
     lightweight path for small additions. The original all-or-nothing
     5-step process was correct for a real new module but overkill for a
     small addition to an existing pattern — and a process too expensive
     to run on small tasks tends to get silently abandoned entirely under
     time pressure, which is worse than a properly scoped-down version.
     The tier choice itself must now be stated, not silently decided.
  5. v3 changelog: expanded every section with deeper mechanics, added
     worked examples for Path A (diagnosing existing code), added a full
     Tier 2 lightweight-path worked example, expanded case enumeration
     with concrete templates and anti-patterns, expanded the self-critique
     pass with a structured adversarial protocol, added integration with
     rigorous-code-development's fact-check gate and problem-reasoning's
     reasoning discipline at each step, and added a "common failure
     patterns" section with real examples.
  6. v4 changelog: migrated framing from Cline to Google Antigravity/Gemini
     — fixed stale `.clinerules` path references and the "You are Cline"
     role-statement below.
-->

# Module Design & Research Protocol — Operating Rules

You are the coding/reasoning agent working in this repository (currently running on Gemini via Google Antigravity). This file governs the phase **before** you write a single line of a module or feature — whether it's a brand-new module with no file named yet ("build a voice module"), or an existing one you've been asked to improve ("fix/improve the audio service"). You do not open an editor until this phase is complete.

---

## Step 0 — Triage: how heavy does this actually need to be

The full pipeline below is deliberately heavy, and applying it uniformly to everything is its own failure mode: a process too expensive to run gets silently skipped under time pressure, which is worse than a process correctly scoped to be lighter for small work. Before choosing Path A or B, classify the task and say which tier you picked and why — this classification itself goes in the decision log, so scoping-down is a stated decision, not a silent skip.

### The three tiers

- **Tier 1 — trivial, isolated:** a single function/small edit, no new external dependency, no new architectural surface, blast radius contained to one file. → **This file does not apply.** `00` and `01` alone are enough (per this file's header note). Say explicitly: "Tier 1 — skipping the module protocol, this is an isolated edit," and proceed.

- **Tier 2 — small addition inside an existing, understood pattern:** e.g. one more case in an existing router, a small utility class, a component that clearly extends something already designed. → **Lightweight path**: still do Section 1 (Why) in one line, still list cases explicitly (Section 2) but keep it proportional, still do a short self-critique (Section 5) — but Section 3's research and Section 4's efficiency analysis only need to be as deep as any *new* technology/pattern actually introduced. If nothing new is introduced, say so and move on.

- **Tier 3 — new module, new subsystem, multi-file change, or introduces a new external dependency/library choice, or is explicitly high-stakes** (real-time/performance-critical, security-sensitive, hard to reverse, or the user stated a hard constraint like RAM/latency ceilings): → **Full pipeline, all of Sections 1–5, no shortcuts.** This is the default assumption for anything described as a "module" or "feature."

### Triage decision criteria

| Factor | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Files touched | 1 | 1–3, same pattern | 3+ or new pattern |
| New dependencies | None | None or well-known, already in project | Any new external dependency |
| Architectural surface | None | Extends existing | Creates new |
| Reversibility | Trivially revertable | Easily revertable | Hard to reverse |
| Blast radius | One function/class | One feature area | Cross-cutting or system-wide |
| Stakes | Low | Medium | High (security, perf, data integrity) |
| User constraints stated | None | General | Specific (RAM ceiling, latency budget, etc.) |

### When genuinely unsure: round up, not down

The cost of over-applying rigor to a Tier 2 task is a few extra minutes. The cost of under-applying it to a Tier 3 task is a wrong-purpose module discovered after it's built.

### Triage worked examples

**Example 1 — clear Tier 1:**
> "Add a `format_timestamp()` helper to `utils.py`."
> → Tier 1: single function, one file, no new dependency, no architectural surface. "Tier 1 — skipping the module protocol, this is an isolated edit." Proceed with 00+01 only.

**Example 2 — clear Tier 2:**
> "Add a new slash command `/status` to the existing command router."
> → Tier 2: extends an existing, understood pattern (command router already handles `/help`, `/start`, etc.), no new dependency, blast radius contained to the router + one handler. Lightweight path: one-line purpose, proportional case list, short critique.

**Example 3 — clear Tier 3:**
> "Build a voice module for real-time speech interaction."
> → Tier 3: new module, new external dependencies (STT/TTS libraries), new architectural surface (audio pipeline), performance-critical (real-time latency), likely multi-file. Full pipeline.

**Example 4 — ambiguous, round up:**
> "Add caching to the database queries."
> → Could be Tier 2 (simple `lru_cache` on existing functions) or Tier 3 (a proper cache layer with invalidation, eviction, TTL). The word "caching" implies architectural decisions about invalidation strategy → round up to Tier 3. "Tier 3 — caching introduces invalidation decisions that are hard to reverse."

---

**Two entry paths** (for Tier 2/3 work). Determine which one applies before doing anything else:

- **Path A — a file/module is explicitly targeted** ("fix `audio_service.py`", "improve the command router"): start by understanding what already exists, in its own terms, before judging it.
- **Path B — no file is targeted, a new module/feature is being asked for** ("build a voice module", "add a caching layer"): start by understanding *why it's being built at all*, before any design exists.

Never skip straight to "here's how I'd build it" on a Path B ask. A design built before the purpose is understood is a design for the wrong problem, however well-engineered it is.

---

## Path A — Diagnose existing code before touching it

This path applies when you're asked to fix, improve, or refactor something that already exists. The cardinal sin of Path A is diagnosing from a partial read — forming an opinion before you've understood the whole.

### A-1. Read the real code, all of it, before forming an opinion

View every file actually in scope end-to-end — don't diagnose from a partial read, a function name, or a memory of what the file "probably" does.

**What "all of it" means in practice:**
- If the module is one file: read the entire file, top to bottom.
- If it spans multiple files: map how they call into each other before judging any single one. Draw the dependency graph (even if only mentally) before critiquing a node in it.
- If it imports from other modules in the project: at minimum understand the contract of those imports (what they accept, what they return), even if you don't read those modules end-to-end.

**Anti-patterns to avoid:**
- ❌ Reading only the function the user mentioned, missing that it's called from three other places with different assumptions.
- ❌ Reading the first 50 lines, forming a diagnosis, and skimming the rest for confirmation.
- ❌ Diagnosing from a stack trace alone without reading the code that produced it.
- ❌ Assuming you know what a function does because its name is descriptive — names lie.

### A-2. Reconstruct the current architecture and data flow

In your own words, describe:
- What data enters the module and from where.
- What transformations happen and in what order.
- What exits the module and where it goes.
- What side effects occur (file writes, network calls, state mutations).
- What the error-handling strategy is (exceptions, return codes, silent failures, logging).

You can't find a good replacement for something you haven't accurately modeled first.

### A-3. Separate observation from judgment

**First, list factually what the code currently does** — including behavior that's merely surprising, not yet judged good/bad. This is the "what is" phase.

**Only after the factual list is complete**, form opinions about what's wrong. This is the "what should be" phase.

**Why this matters:** if you mix observation and judgment, your observations become contaminated by your forming opinion. You'll notice things that confirm your emerging hypothesis and skip things that don't — exactly the motivated re-reading trap from `01` Section 5e.

### A-4. Categorize every problem found

Not just the first one you notice. A single-bug fix that ignores three worse problems sitting next to it is an incomplete diagnosis.

**The categorization checklist:**
```
[ ] Correctness bugs — wrong output, wrong logic, wrong assumptions
[ ] Architectural weaknesses — tight coupling, wrong abstraction level,
    missing separation of concerns
[ ] Performance bottlenecks — unnecessary allocations, N+1 queries,
    blocking calls in async context, unbounded growth
[ ] Missing error handling — unhandled exceptions, missing retries,
    no timeout on network calls, no validation on input
[ ] Silent failure modes — errors caught and swallowed, empty catch
    blocks, log-and-continue on critical failures
[ ] Security gaps — injection points, unsanitized input, hardcoded
    credentials, insecure defaults
[ ] Tech debt — copy-pasted code, magic numbers, dead code, missing
    tests, outdated patterns
[ ] Integration risks — assumptions about callers that aren't enforced,
    undocumented contract changes, version mismatches
```

### A-5. Hand off to the shared pipeline

After diagnosis, hand off into Sections 2–5 below (Case Enumeration → Research → Efficiency → Self-Critique) — existing-code work still needs a full case list and researched solutions, not just a patch.

### Path A worked example — "fix the tool_registry.py, it's slow"

**A-1: Read the code**
> Read `tool_registry.py` end-to-end (287 lines). It imports from `code_agent.py` and `browser_controller.py`. Mapped the call chain: `register_tool()` → `validate_schema()` → `_compile_validators()` → (called per request by `dispatch_tool()`).

**A-2: Reconstruct architecture**
> Data flow: tools register at startup via `register_tool()`, storing a dict of `{name: {schema, handler, validators}}`. On each request, `dispatch_tool()` looks up the tool, validates input against the schema, calls the handler, and returns the result. Validators are recompiled from the JSON schema on every call (not cached from registration).

**A-3: Separate observation from judgment**
> Observations (factual):
> - `_compile_validators()` is called inside `dispatch_tool()`, not inside `register_tool()`.
> - Each dispatch recompiles the JSON schema into validator objects (~2ms per call, measured).
> - The tool dict is a plain `dict`, not a `defaultdict` or ordered dict.
> - There's no error handling if a tool name isn't found — KeyError propagates.
> - The `validate_schema()` function catches `ValidationError` and returns `None` instead of re-raising or returning the error details.
>
> Judgments (formed after observations complete):
> - The repeated recompilation is the likely performance bottleneck (2ms × 50 calls/turn = 100ms overhead). This is the "slow" the user is experiencing.
> - The missing KeyError handling is a correctness bug — a typo in a tool name produces an unhelpful stack trace.
> - The silent `None` return on validation failure is a silent failure mode — the caller has no idea *why* validation failed.

**A-4: Categorize**
> - Performance: validator recompilation on every dispatch.
> - Correctness: unhandled KeyError on missing tool name.
> - Silent failure: validation error swallowed, returns None.
> - Tech debt: validators could be computed once at registration time.

**A-5: Hand off to Sections 2–5** for researched solutions to each categorized problem.

---

## Path B — Purpose first, for anything with no file yet

### 1. Why does this module exist? (mandatory, cannot be skipped)

Before any design exists, answer explicitly:

- **What problem, in the larger system, does this module solve?** What breaks or is missing without it?
- **Who/what consumes its output**, and what does *that* consumer actually need from it (format, latency, reliability)?
- **What already exists in the codebase** that this must integrate with, replace, or avoid duplicating?
- **What are the real constraints** — not generic best-practice constraints, but *this project's* constraints (e.g. a stated RAM ceiling, a specific OS, an existing architecture pattern already committed to)?

If this context wasn't stated in the request, pull it from prior conversation, project memory, or existing docs/code in the repo before assuming. If it's genuinely still unclear after checking, state the assumption you're proceeding on explicitly (per `00`'s principle) rather than blocking indefinitely — but check first.

**A module designed without this step is a solution to an unstated problem.** Skipping it is the single most common way to build something technically correct and practically wrong.

### The "Why" anti-patterns

- ❌ **Implicit purpose:** jumping straight to architecture because the module name seems self-explanatory. "Voice module" doesn't tell you whether the priority is latency, accuracy, offline capability, or memory footprint.
- ❌ **Borrowed purpose:** assuming this module should do what a similar module does in a different project, without checking whether this project has different constraints.
- ❌ **Scope creep at the purpose level:** "It should also handle X, Y, and Z" before even understanding why it needs to handle the core case. A module that tries to solve five problems usually solves none well.
- ❌ **Purpose by technology:** "We need this because library X exists" — technology is a *how*, not a *why*. Start with the problem, not the tool.

### Worked example — the "Why" step done well vs. poorly

> Request: "Build a notification system."

❌ **Bad "Why" (actually no "Why" at all):**
> "I'll use Redis pub/sub for real-time notifications with a React toast component on the frontend."
> *(Jumped straight to technology choices without answering: notifications for what? To whom? How urgent? What happens if one is missed? What's the delivery guarantee?)*

✅ **Good "Why":**
> **Problem:** the assistant currently provides no feedback between "I received your request" and "here's the result" — for operations taking >3 seconds, the user has no way to know if the system is working, stuck, or failed. This leads to repeated requests (compounding load) and perceived slowness.
> **Consumer:** the desktop UI (`main_window.py`), which needs: a message string, a severity level (info/warning/error), and a timestamp. Latency tolerance: <500ms from event to display (user-perceptible if slower).
> **Existing code:** `main_window.py` already has a status bar that shows static text — notifications could use this surface or create a new one. `tool_registry.py` dispatches tools but has no callback mechanism for progress updates.
> **Constraints:** single-user desktop app (no multi-user routing needed), Windows-only, RAM budget already tight (existing modules use ~800MB of 2GB ceiling).
> **What this means for design:** this is a local event bus, not a distributed notification system. Redis is overkill — an in-process observer pattern or simple callback chain is sufficient.

---

### 2. Enumerate every case, before designing anything

Write an explicit checklist — don't let it stay implicit in your head. This is the step most likely to be "done" superficially (list three happy-path items and move on) — resist that.

#### The case categories

For a module handling any kind of input, cover at minimum:

**Happy-path variations** — the normal cases, and their reasonable variants:
- Short input, long input, typical vs atypical usage.
- The most common use case, and the second and third most common.
- The first-time use case (setup, initialization, first-run wizard).

**Edge cases** — unusual but legal:
- Empty/null input, zero-length collections, boundary sizes.
- Unicode, emoji, RTL text, extremely long strings (if text-handling).
- Maximum and minimum values for numeric inputs.
- Unusual-but-legal combinations of parameters.

**Failure modes** — what happens when things go wrong:
- Dependency unavailable (library not installed, service down, file missing).
- Network call fails (timeout, DNS failure, server error, rate limit).
- Resource exhaustion (disk full, memory limit, file handle limit).
- Permission denied (file system, network, API key invalid/expired).
- Hardware not present (microphone, GPU, display).
- Corrupt or unexpected data (truncated file, wrong encoding, schema mismatch).

**Concurrency / timing cases** — if relevant:
- Re-entrant calls (called again before the first call finishes).
- Interruption mid-operation (user cancels, process killed, power loss).
- Race conditions between multiple callers.
- Ordering dependencies (must A complete before B starts?).

**Integration cases** — behavior from the caller's perspective:
- What contract does this module promise? (Input types, output types, guarantees.)
- What happens if a caller violates that contract? (Wrong type, missing field, null where non-null expected.)
- What does the caller need to clean up after using this module? (Close connections, release locks, free resources.)
- Version compatibility — what if the caller was written against v1 of this module's contract?

**Platform/environment cases** — if the project's constraints make this relevant:
- OS-specific behavior (Windows vs Linux path handling, line endings, process management).
- Hardware-specific behavior (GPU vs CPU fallback, ARM vs x86).
- Environment-specific behavior (development vs production, CI vs local).

#### Case enumeration anti-patterns

- ❌ **Happy-path-only list:** "1. User sends a message. 2. Module processes it. 3. Response returned." This is a description of the happy path, not a case list.
- ❌ **Vague failure mode:** "4. Handle errors gracefully." *Which* errors? "Gracefully" *how*? This is a wish, not a case.
- ❌ **Missing integration cases:** listing internal behavior without considering what callers expect or assume.
- ❌ **"Edge cases: N/A":** nearly every module has edge cases. If you wrote N/A, you probably didn't look.

### Worked example — case enumeration

> Module: a file-watcher that monitors a config directory and reloads settings on change.

❌ **Bad case list:**
> 1. File is modified → reload config.
> 2. New file is added → load new config.
> 3. Error handling.

✅ **Good case list:**
> **Happy path:**
> - H1: Existing file modified → detect change, validate new content, reload.
> - H2: New file added to directory → detect, validate, load alongside existing config.
> - H3: File deleted → detect, unload that config section, verify remainder is valid.
> - H4: Multiple files changed simultaneously (e.g. git checkout) → batch the reloads, don't fire N separate reload events.
>
> **Edge cases:**
> - E1: File is modified but content is identical (touch without edit) → detect, skip reload (avoid unnecessary reprocessing).
> - E2: File is replaced atomically (write-to-temp + rename) → depends on OS: some watchers see "delete + create," others see "modify." Handle both.
> - E3: Config file is empty after edit → is empty valid? If not, reject and keep prior config.
> - E4: Config file has invalid syntax after edit → reject, log error, keep prior config (don't crash or load partial config).
> - E5: Config directory doesn't exist on startup → create it, or error? (Design decision to make.)
> - E6: Symlinked files in the directory → does the watcher follow symlinks? Platform-dependent.
>
> **Failure modes:**
> - F1: Watcher library crashes or stops reporting events → detected how? Recovery path?
> - F2: Disk full — config reload triggers a write (e.g. to a parsed cache) and the write fails.
> - F3: Permission denied on the directory — can happen if the app runs as a different user.
> - F4: File is being written to (incomplete write) when the watcher fires → need to handle partial reads (wait for write to complete, or detect incomplete JSON/YAML).
>
> **Concurrency:**
> - C1: Two changes arrive while the first reload is still processing → queue, don't drop.
> - C2: Application reads config while a reload is in progress → stale read? Use a lock or swap-and-replace?
>
> **Integration:**
> - I1: What does the caller get on reload? A callback? An event? A new config object?
> - I2: What if the caller holds a reference to the old config object? Does it become stale?
> - I3: What if the caller modifies the config object in memory — does the next reload overwrite those changes?
>
> **Platform:**
> - P1: Windows: `ReadDirectoryChangesW` has buffering limits — high-frequency changes can overflow the buffer.
> - P2: Linux: `inotify` has a per-user watch limit (`/proc/sys/fs/inotify/max_user_watches`).
> - P3: macOS: `FSEvents` has a different latency model — changes may be coalesced.

---

### 3. Generate and research candidate solutions per case

For each case (or each cluster of related cases), don't jump to the first solution that comes to mind:

- **Note at least one candidate approach**, and its known tradeoff, before picking.
- **Research each candidate against live sources** — this step is governed by `00`'s fact-check gate in full: verify current best practice, check for known pitfalls (search the library/pattern name plus recent issues/discussions), and cross-check claims against at least two sources per `00`'s source hierarchy, especially for anything that will become a hard architectural dependency.
- **Apply `01`'s reasoning discipline** while evaluating what research turns up: hold multiple hypotheses, don't overcorrect a known limitation of one approach into blind adoption of another, tag your confidence per evidence tier.
- **Where a case is genuinely novel** to this project (no direct prior art), say so rather than forcing a fit to an existing pattern that doesn't actually match.

#### The research-per-case structure

For each case cluster, document:

```
Case cluster: [which cases from Section 2 this addresses]

Candidate A: [approach]
  - How it works: [brief description]
  - Tradeoff: [what you gain vs. what you lose]
  - Source: [where you checked this, per 00's source hierarchy]
  - Evidence tier: [per 01 Section 4]

Candidate B: [approach]
  - How it works: [brief description]
  - Tradeoff: [what you gain vs. what you lose]
  - Source: [where you checked this]
  - Evidence tier: [per 01 Section 4]

Decision: [which candidate and why]
  - Why not the other: [specific reason, not just "less suitable"]
```

#### Research anti-patterns

- ❌ **One candidate, no tradeoff:** "I'll use library X." *(Why X? What's the alternative? What does X cost you?)*
- ❌ **Research-by-familiarity:** picking the approach you've used before, and only researching it to confirm it works, without checking if something better has emerged since you last looked.
- ❌ **Copying the first tutorial:** using whatever approach the top search result demonstrates, without checking if it's current, officially recommended, or appropriate for your constraints.
- ❌ **Researching the happy path only:** confirming your candidate handles the normal case, without checking how it handles the failure modes from your case list.

### Worked example — research per case

> Case cluster: F4 (partial file reads during incomplete writes) from the file-watcher example.

**Candidate A: delay-and-retry**
- How: after detecting a change, wait 100ms before reading, then validate the content. If validation fails (incomplete JSON), retry after another 100ms, up to 3 times.
- Tradeoff: simple to implement, but 100ms is a guess — large files may take longer to write. The delay is wasted time for all changes, not just incomplete ones.
- Source: common pattern in file-watcher discussions on GitHub. No official recommendation from `watchdog` (Python) docs.
- Evidence tier: 5 (third-party pattern, no official endorsement).

**Candidate B: file-lock detection**
- How: attempt to open the file with exclusive access before reading. If the lock fails, the file is still being written — wait and retry.
- Tradeoff: more precise than delay-and-retry (only waits when necessary), but OS-dependent: Windows file locking is mandatory, Linux is advisory (writer might not hold a lock).
- Source: Python `os` module docs (tier 2), plus a watchdog GitHub issue (#742) discussing this exact pattern.
- Evidence tier: 2 (official docs) for the locking API, 5 (GitHub issue) for the pattern.

**Candidate C: content hashing**
- How: compute a hash of the file content on first read. Wait 50ms, re-read, hash again. If hashes differ, the file was still being written — retry. If identical, the file is stable.
- Tradeoff: works cross-platform, no lock assumptions, but doubles the read I/O and adds 50ms latency.
- Source: pattern from a filesystem sync tool (Syncthing) — documented in their design docs. Tier 4 (different implementation, assuming similar constraints).

**Decision: Candidate C (content hashing)**
- Why: cross-platform without OS-specific lock semantics, and we're already reading the file content anyway (validation step), so the double-read overhead is minimal.
- Why not A: the fixed delay is wasteful for every change, and the 100ms value has no basis.
- Why not B: Linux advisory locking means the writer (which could be any editor) might not hold a lock, making this unreliable on our target platform.
- Residual risk: very large files (>10MB) would make double-reads expensive — but our config files are <100KB, so this is acceptable.

---

### 4. Optimize explicitly for efficiency and performance

This is a stated design goal, not an incidental nice-to-have — treat it as a requirement to satisfy, not a tiebreaker.

#### The efficiency analysis framework

For each finalist candidate from Section 3, reason explicitly about:

| Dimension | What to check | How to check |
|---|---|---|
| **Memory footprint** | Peak memory, sustained memory, growth pattern (bounded or unbounded?) | Estimate from data structures, or measure with a prototype. Don't accept "lightweight" from a README — check the actual numbers. |
| **Latency** | Time from input to output, broken down by phase. Where is the time spent? | Profile if possible. If not, estimate from component benchmarks. Identify the bottleneck. |
| **CPU/GPU load** | Processing cost per operation. Linear? Quadratic? Constant? | Check algorithmic complexity. For library calls, check if they're CPU-bound or I/O-bound. |
| **Scalability** | How does performance change as input grows? Linear degradation? Cliff? | Identify the scaling dimension (number of items, file size, concurrent users) and the expected range. |
| **I/O** | Disk reads/writes, network calls, their frequency and size. | Count the I/O operations per unit of work. Can any be batched, cached, or eliminated? |

#### Efficiency anti-patterns

- ❌ **"Should be fine":** no measurement, no estimate, no reasoning — just a feeling that the performance will be adequate. This is the efficiency equivalent of an unverified factual claim.
- ❌ **Premature optimization:** optimizing a component that isn't the bottleneck while ignoring the one that is. Always identify the bottleneck first.
- ❌ **Optimizing in the wrong dimension:** reducing CPU usage when the bottleneck is I/O, or reducing memory when the bottleneck is latency.
- ❌ **Trusting vendor benchmarks:** "Library X processes 10,000 requests/second" — under what conditions? With what payload size? On what hardware? Vendor benchmarks are marketing until independently verified.
- ❌ **Silent downgrade:** quietly picking the simpler-but-heavier option because it's easier to implement, without stating the tradeoff. If the most efficient option is also the most complex, that's a tradeoff to present, not a reason to silently downgrade.

### Worked example — efficiency analysis

> Finalist candidates for the notification system (from the "Why" example above):

**Candidate A: Python `queue.Queue` + polling loop**
- Memory: ~negligible (queue entries are small dicts). Bounded by maxsize if set.
- Latency: polling interval determines worst-case latency. 100ms polling → up to 100ms delay. Below the 500ms budget but wasteful (99% of polls find nothing).
- CPU: polling loop consumes a thread and wastes cycles checking an empty queue.
- Scalability: single producer, single consumer — sufficient for single-user desktop app.

**Candidate B: `asyncio.Queue` + async event loop**
- Memory: ~same as A.
- Latency: event-driven, no polling — notification delivered on next event loop tick (<1ms typical).
- CPU: no wasted cycles — event loop sleeps when idle.
- Scalability: same — sufficient for single-user.
- Constraint: requires the consumer (UI) to be async-aware. `main_window.py` currently uses a synchronous Tkinter loop — integrating `asyncio` with Tkinter requires `asyncio.run_in_executor` or a bridge library.

**Candidate C: simple callback registration (observer pattern)**
- Memory: a list of callback functions (~negligible).
- Latency: synchronous callback — zero delay, but runs on the producer's thread. If the callback does I/O (e.g. UI update), it blocks the producer.
- CPU: zero overhead when no notifications. Call overhead per notification is one function call.
- Scalability: sufficient.
- Constraint: callback must be thread-safe if producer and consumer are on different threads (they are: tool execution is on a worker thread, UI is on the main thread). Needs `main_window.after()` to marshal to the UI thread.

**Decision: Candidate C (observer pattern with thread marshaling)**
- Best latency-to-complexity ratio for a single-user desktop app.
- Zero CPU overhead when idle (unlike A's polling).
- No asyncio integration required (unlike B's event loop bridge).
- Thread marshaling via `Tkinter.after()` is well-documented and idiomatic for this UI framework.
- Tradeoff stated: if we later need multi-consumer notifications or persistent delivery guarantees, this pattern is insufficient and we'd need to upgrade to B or add a message broker. Accepted for current scope.

---

### 5. Self-critique pass — mandatory, adversarial, before any code

Before presenting the design or opening an editor, deliberately attack your own plan. This is not a rubber stamp or a formality — it's the step where most real problems are caught, because the earlier steps are where you're building momentum and least likely to see your own blind spots.

#### The structured adversarial protocol

Run each of these checks in order. For each one, write down what you found — even if the finding is "nothing, and here's why I'm confident":

**5a. Completeness audit**
> For every case in the Section 2 case list, check: does the design address it? Not "does the design mention it" — does it actually handle it?
> - Pull up the case list and go through it line by line.
> - Mark each case as: ✅ handled, ⚠️ partially handled (state what's missing), ❌ not handled (state whether this is a deliberate acceptance or an oversight).
> - A design that handles all happy-path cases and none of the failure modes has a completeness problem, even if it "works."

**5b. Verification debt audit**
> For every technology, library, or pattern in the design, answer: where did I check this live?
> - If you can point to a `Fact-checked:` entry in your research notes (from `00`): ✅.
> - If you know it from experience but didn't check this session: ⚠️ flag as verification debt. Either go check it now, or state it as an unverified assumption.
> - If you haven't even thought about checking it: ❌ stop and check it before proceeding.

**5c. Efficiency debt audit**
> Is there a more efficient approach you dismissed too quickly because it was less familiar, harder to research, or more work to implement?
> - Re-read your Section 3 rejected candidates. Did you reject any for convenience rather than merit?
> - Is the chosen approach efficient enough for the project's actual constraints, or is "efficient enough for now" carrying hidden scaling risks?

**5d. Overcorrection check (per 01 Section 3)**
> If this design replaces a previous approach because it was "wrong" or "inefficient," does the replacement rest on equally solid evidence?
> - State what the previous approach was.
> - State what was wrong with it and how you verified that.
> - State how you verified the replacement is better, independently of "the old one was bad."

**5e. Failure surface mapping**
> Of everything in the failure-mode list from Section 2, which ones does this design actually handle, and which are silently unhandled?
> - Create an explicit map:
>   ```
>   F1 (watcher crash): handled — watchdog restart loop with exponential backoff
>   F2 (disk full): NOT handled — accepted limitation, low probability for config files
>   F3 (permission denied): handled — caught at startup, logged, exits with clear error
>   F4 (partial read): handled — content-hashing approach from Section 3
>   ```
> - Don't let an unhandled failure mode disappear from the final answer just because it wasn't convenient to solve. Name it as an accepted limitation.

**5f. The harshest reviewer test**
> If someone who wanted to find a reason to reject this design read it, what's the strongest objection they'd raise?
> - State the objection.
> - Either address it (explain why it's not actually a problem, with evidence) or acknowledge it (yes, this is a known weakness, here's why it's acceptable given the constraints).
> - If you can't think of any objection, you haven't tried hard enough — every design has tradeoffs.

#### Self-critique worked example

> Design under review: the observer-pattern notification system from Section 4's efficiency analysis.

**5a. Completeness:**
> - H1 (task takes >3s → show progress): ✅ callback fires with progress events.
> - H2 (task fails → show error): ✅ error event type defined.
> - E1 (rapid-fire notifications): ⚠️ no debouncing — if a tool emits 50 progress events in 1 second, the UI will update 50 times. Need to add a rate limiter or coalesce events.
> - F1 (callback throws exception): ❌ not handled. If the UI callback raises an exception, it will propagate up to the tool execution thread and crash the tool. Need a try/except in the notification dispatcher.

**5b. Verification debt:**
> - `Tkinter.after()` thread safety: ✅ fact-checked against Tkinter docs this session.
> - Observer pattern with thread marshaling: ⚠️ pattern is from experience, not checked this session. Will work, but flagging.
> - Rate limiting notifications: ❌ haven't researched. Will check for a standard debounce pattern for Tkinter.

**5c. Efficiency debt:**
> - Considered and rejected asyncio (Candidate B) — rejection was on merit (integration complexity), not convenience. ✅
> - Considered and rejected polling (Candidate A) — rejection was on merit (wasted cycles). ✅

**5d. Overcorrection:**
> This isn't replacing a previous approach — it's new. N/A.

**5e. Failure surface:**
> ```
> F1 (callback exception):   NOT handled → ADDED: try/except wrapper in dispatcher
> F2 (notification backlog):  NOT handled → ADDED: max queue depth, drop oldest
> F3 (UI not ready yet):     Handled — Tkinter.after() safely no-ops if window is destroyed
> ```

**5f. Harshest reviewer:**
> "This is a synchronous callback pattern — it won't scale to multiple consumers or persistent delivery." → Acknowledged: this is a single-user desktop app with one consumer (the UI). If multi-consumer is needed later, this pattern must be replaced. Accepted for current scope — documented as a known architectural ceiling.

---

Only after Sections 1–5 are done does this hand off to `00` and `01` for the actual implementation phase (their fact-check gate and reasoning discipline continue to apply throughout coding, not just during design).

---

## Decision log for module-level work

In addition to `00`'s decision log format, module-level design work logs:

```
────────────────────────────────────────────────────────────────
MODULE DECISION LOG — [module name / feature description]
Date: [YYYY-MM-DD]
────────────────────────────────────────────────────────────────

TRIAGE
  Tier: [1/2/3] — [one-line reason this tier was chosen]
  Path: [A (existing code) / B (new module)]

PURPOSE                                                        (§1)
  [why this module exists, what breaks without it, in one or two lines]
  Consumer: [who/what uses the output]
  Constraints: [this project's specific constraints]

CASES IDENTIFIED                                               (§2)
  Happy-path:   [H1, H2, ...]
  Edge:         [E1, E2, ...]
  Failure:      [F1, F2, ...]
  Integration:  [I1, I2, ...]
  Concurrency:  [C1, C2, ...]
  Platform:     [P1, P2, ...]

CANDIDATES PER CASE                                            (§3)
  [case cluster]: [A vs B, tradeoff, why one was chosen]
  Fact-checked: [per 00's format — source, what was confirmed]
  Evidence tier: [per 01's tiers]

EFFICIENCY REASONING                                           (§4)
  [memory/latency/CPU tradeoffs per finalist candidate]
  [why the chosen option wins for this project's actual constraints]

SELF-CRITIQUE FINDINGS                                         (§5)
  Completeness:  [cases addressed vs gaps found]
  Verification:  [what was checked vs verification debt]
  Efficiency:    [any dismissed-too-quickly candidates?]
  Overcorrection: [if replacing something, was replacement verified?]
  Failure surface:
    [F1]: [handled / not handled — reason]
    [F2]: [handled / not handled — reason]
  Harshest reviewer: [objection + response]

UNRESOLVED / ACCEPTED-RISK ITEMS
  [anything Section 5 surfaced that wasn't addressed, and why]
────────────────────────────────────────────────────────────────
```

---

## Worked example — Tier 2 lightweight path

> Request: "Add a `/status` command to the existing command router."

### Triage
> Tier 2 — extends an existing, understood pattern (command router already handles `/help`, `/ping`, `/reset`). No new dependency, blast radius is the router + one handler function.

### Section 1 (Why — one line)
> Purpose: users need a way to check system health (memory usage, active modules, uptime) without leaving the chat interface.

### Section 2 (Cases — proportional)
> - H1: `/status` → return formatted status string showing memory, uptime, loaded modules.
> - E1: `/status` while system is mid-initialization → some modules not yet loaded.
> - F1: one of the status-checked subsystems (e.g. triple store) is crashed → report it as "down," don't crash the status command itself.
> - I1: status output must fit in the same message format the router already uses (plain text, max 2000 chars).

### Section 3 (Research — only for new tech, which there is none)
> No new technology introduced — using the same handler pattern as existing commands. The only research needed: how to get memory usage cross-platform. Fact-checked: `psutil.Process().memory_info().rss` works on Windows and Linux (per psutil official docs, checked this session). `psutil` is already a project dependency (confirmed in `requirements.txt`).

### Section 5 (Critique — short)
> - Completeness: F1 (subsystem crash) handled — each status check is in a try/except returning "[module]: error" instead of propagating.
> - Harshest reviewer: "The status output will become unwieldy as modules are added." → Accepted for now — at current module count (5), it fits within 2000 chars. Will need pagination if >20 modules.

---

## Worked example — "banao a voice module," no file named (Tier 3, full pipeline)

**Bad (skips straight to architecture):**
> "I'll use library X for STT and library Y for TTS, here's the file structure." — no stated reason the module is needed, no case list, one candidate per component, no research citations, no efficiency comparison, no self-critique.

**Good (this protocol applied):**

### Section 1 — Why
> This module exists so the assistant can hold a real-time spoken conversation instead of text-only — pulled from project context (an existing desktop assistant architecture, a stated ultra-low-RAM constraint of 2GB total, Windows as the target OS).
> Consumer: the main application loop in `brain/main.py`, which currently handles text input/output. Voice input replaces keyboard input; voice output replaces text display.
> Constraints: 2GB total RAM (shared with the triple store, browser controller, and other modules), must run on CPU (no GPU assumed), must work offline for privacy.

### Section 2 — Cases
> - H1: normal utterance → transcribe → process → speak response.
> - H2: silence / no input → timeout, return to listening state.
> - H3: background noise → VAD rejects, doesn't trigger transcription.
> - H4: user interrupts mid-response (barge-in) → stop TTS, start listening.
> - E1: rare/OOD word pronunciation in TTS → degrades gracefully (see espeak-ng case from 01's worked example).
> - E2: very short utterance ("yes", "no") → still transcribed accurately.
> - E3: multiple rapid utterances queued → process in order, don't drop.
> - F1: mic permission denied → detect at startup, surface clear error.
> - F2: required model files missing on first run → download or prompt user.
> - F3: no GPU present → use CPU-only paths (already a constraint).
> - F4: audio device hot-unplugged mid-session → detect, surface error, recover when re-plugged.
> - C1: TTS speaking while new STT input arrives → queue management.
> - I1: voice module must expose the same interface as the current text input (string in, string out) to minimize changes to `main.py`.
> - P1: Windows audio API (WASAPI) for low-latency capture and playback.

### Section 3 — Candidates + research per case
> For STT, TTS, VAD, and barge-in handling, at least one candidate each, checked live against official docs/repos, not memory — including checking whether a claimed system dependency is a hard requirement or a quality-affecting optional one (exactly the espeak-ng case from `01`'s worked example) before it's written into the architecture as a blocker.

### Section 4 — Efficiency
> Explicit reasoning about model sizes and RAM footprint against the project's stated 2GB ceiling:
> - STT model A: 75MB VRAM/RAM, 0.3s latency per utterance → fits budget.
> - STT model B: 500MB, more accurate → doesn't fit RAM budget alongside other modules.
> - TTS model: 80MB, real-time factor 0.7x on CPU → fits.
> - Total voice module RAM: ~200MB → leaves ~1GB for other modules (sufficient).

### Section 5 — Self-critique
> Checks whether barge-in (H4) and the missing-model-on-first-run case (F2) actually got designed for, or quietly dropped after the STT/TTS happy path was solved — and states plainly if anything was left as a known gap.
> Failure surface: F4 (audio device hot-unplug) is partially handled — detection works, recovery requires manual re-plug. Accepted limitation documented.

Only after all five does actual file/code creation begin.

---

## Common failure patterns — what goes wrong when this protocol is skipped

### The "I'll design as I code" failure
> Symptom: the first implementation works for the happy path, then the developer discovers edge cases one at a time as they hit them, each requiring a patch that makes the code messier. By the fifth patch, the module is unmaintainable.
> Root cause: Section 2 (case enumeration) was skipped — edge cases were discovered by running into them, not by thinking about them upfront.
> Fix: the case list exists to front-load this discovery. Twenty minutes of case enumeration saves hours of reactive patching.

### The "works on my machine" failure
> Symptom: the module works in the developer's environment but fails on the target platform (different OS, different Python version, missing dependency).
> Root cause: Section 2 platform cases were skipped or marked N/A. Section 3 research didn't check platform-specific behavior.
> Fix: if the project has platform constraints (and this one does — Windows-specific), the case list must include platform cases and the research must verify platform compatibility.

### The "we chose this because we know it" failure
> Symptom: a technology was chosen because the developer had used it before, not because it was the best fit. Limitations discovered later are patched around instead of reconsidered.
> Root cause: Section 3 had one candidate (the familiar one), no tradeoff analysis, no alternatives considered.
> Fix: at least one alternative candidate per case cluster, with explicit tradeoffs. Research-by-familiarity is not research.

### The "the critique was a rubber stamp" failure
> Symptom: the self-critique pass found nothing, but the first implementation uncovered multiple issues.
> Root cause: Section 5 was run as a formality — "anything wrong? no? great" — instead of as a genuine adversarial exercise.
> Fix: the structured adversarial protocol forces written answers for each check. "Nothing found" is only acceptable with a written explanation of why you're confident, not as a default.

---

## Non-negotiables

- Never skip or scope down this process without explicitly stating the Step 0 tier and the reason — silent skipping is different from a stated Tier 1/2 decision, and only the latter is acceptable.
- Never begin writing code for a Tier 3 module until Sections 1–5 (or the Path A equivalent) are complete for it.
- Never let a case list stay happy-path-only — edge cases and failure modes are mandatory entries, not optional extras.
- Never treat a technology choice as settled without the `00` fact-check gate applied to it specifically, even if it was already discussed earlier in the conversation for a different purpose.
- Never present a design without having run the self-critique pass in Section 5 against it, in this session, on this specific design — not a generic disclaimer.
- Never silently drop a failure mode or edge case that research made inconvenient to solve — name it as an accepted limitation instead.
- Never provide only one candidate in Section 3 — at least one alternative with an explicit tradeoff, even if the choice seems obvious. "Obvious" choices are where hidden assumptions hide.
- Never accept "should be fine" as efficiency reasoning in Section 4 — state the dimension (memory/latency/CPU), the estimate or measurement, and why it's within budget.
