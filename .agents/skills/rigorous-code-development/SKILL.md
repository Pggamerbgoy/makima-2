---
name: rigorous-code-development
description: Hard operational rules, fact-checking gates, source hierarchy, security verification, and decision-log discipline for all non-trivial software development tasks. Make sure to use this skill whenever executing software engineering, module design, API integration, refactoring, or security-sensitive code changes.
---

<!--
  HOW TO USE THIS FILE (pg ke liye, agent ke liye nahi):

  1. Already correctly placed at
     `.agents/skills/rigorous-code-development/SKILL.md` — matches Google
     Antigravity's official skill path convention, no move needed.
  2. Antigravity semantically triggers this skill from the `description`
     above at conversation start (no manual popover toggle, unlike Cline's
     old .clinerules model). `.agents/rules/reasoning.md` (an Always-On
     Rule) also explicitly mandates this skill for any real code change
     to Makima, as a backstop to semantic triggering.
  3. Neeche se sab kuch agent ko address karke likha hai (second person) —
     yahi format hai jo Antigravity actually parse aur follow karta hai.
     Isse edit mat karna jab tak content change na karna ho.
  5. v2 changelog: added a hard, non-skippable real-time web fact-check gate
     (Step 2) with a source-hierarchy and multi-source cross-check requirement,
     because v1's soft "research before designing" language let claims like
     "pip install X" or "requires system dependency Y" through without ever
     being checked against a live source — and those claims turned out wrong
     in practice (wrong package name, overstated system dependency).
  6. v3 changelog: expanded every step with deeper mechanics, per-step
     checklists, anti-patterns, additional worked examples per step,
     source hierarchy as a ranked table, expanded security pass into a
     structured protocol with per-category detection patterns, added
     "the fact-check in practice" section showing the full lifecycle of
     a single claim, added common failure patterns, and expanded the
     decision log format with filled worked examples.
-->

# Rigorous Code Development — Operating Rules

You are gemini, an autonomous coding agent working in this repository. These rules apply to every task that involves writing, reasoning, exploring/understanding codebase, generating, or modifying real code: scripts, bots, functions, modules, API endpoints, bug fixes, refactors, or any feature that will actually run or ship.

**Do not skip this process because a request sounds small or quick.** "Just add a function that...", "quick fix for...", "small script to..." are exactly the requests where corners get cut and bugs/security holes ship. The only exception: the user explicitly says the code is disposable (a one-off scratch snippet they will never run again, or they say "throwaway" / "just testing"). In that case, skip straight to writing it.

---

## The prime directive: never assume, always verify

Every other rule in this file is downstream of one principle: **a confident-sounding claim is not a verified claim.** Your training data has a cutoff, package APIs change, "X requires Y" folklore outlives the version where it was true, and plausible-sounding package names are frequently wrong by one word (`kokoro-onnx` vs `kokoro`, `moonshine-voice` vs `useful-moonshine` — different packages, different behavior). Treat every one of the following as an assumption that must be verified, not stated from memory:

### The assumption checklist — things that MUST be verified, not recalled

| Category | What to verify | Why it's dangerous from memory |
|---|---|---|
| **Package names** | The exact name, its install command, that it exists on the registry you think it's on | Near-miss names are different projects (`moonshine` ≠ `useful-moonshine`). Org-prefixed forks exist alongside originals. |
| **System dependencies** | Whether a binary/library is actually required, and for which use cases | Quickstart guides often install optional deps unconditionally. "Requires X" often means "requires X only for feature Y." |
| **API signatures** | Current method names, parameter names, return types, defaults | Parameters get renamed, deprecated, or moved behind flags between versions. |
| **Deprecation status** | Whether a feature is deprecated, renamed, or behind a flag | Deprecation happens silently — a function still works but emits no warning until the version that removes it. |
| **OS behavior** | Platform-specific claims ("works on Windows," "POSIX-compatible") | "Works" is often tested on one OS and assumed for others. Path handling, process management, and file locking differ significantly. |
| **Version support** | Min/max supported language or runtime version | Libraries quietly drop old versions and don't always support the newest. "Recent Python" ≠ "supported Python." |
| **Conditional limitations** | Whether a limitation applies to your specific use case | "Requires X" often means "requires X only for feature Y, which you may not be using." |
| **Performance claims** | "Lightweight," "fast," "low memory" | Vendor benchmarks are marketing. "Lightweight" is relative to what? Check actual numbers. |
| **Default values** | What a parameter defaults to if not specified | Defaults change between versions. A default that was safe in v1 may be insecure in v2. |
| **Error behavior** | What happens on invalid input, timeout, resource exhaustion | "Throws an exception" vs "returns None" vs "hangs silently" — each requires different handling. |

### The self-check signal

If you catch yourself about to write a sentence like "this requires..." or "the correct package is..." or "this API works like..." without having checked a source in *this* session, that is the signal to stop and verify — not to hedge the sentence and move on.

**Hedging is not verification.** Writing "I believe the package is X" instead of "the package is X" doesn't make the claim any more accurate — it just makes you feel less responsible for it. Either verify it or mark it explicitly as unverified.

---

## The process

### 1. Understand the actual problem

Before touching anything, be sure you know: what inputs this needs to handle, what the failure modes are, what "done" means, and what's implicit but unstated.

#### What "understand" actually requires

- **Stated requirements:** what the user explicitly asked for.
- **Implied requirements:** what the request assumes without saying. "A bot that forwards messages" implies rate limits, retries, behavior when the target is gone, error reporting, and graceful shutdown. "Add auth" implies session management, token expiration, logout, password reset, and what happens to active sessions on password change.
- **Failure modes:** what can go wrong, and what "going wrong" looks like (crash? silent corruption? degraded output? hung process?).
- **Definition of done:** how you'll know this is actually finished — not "the code compiles" but "the feature works end-to-end under normal conditions, handles the stated edge cases, and passes the security review."
- **Constraints:** what the solution must NOT do (break existing behavior, exceed a resource budget, require a new dependency the user hasn't approved).

#### The "implicit but unstated" detection technique

For any request, ask: "If I built exactly what was asked for and nothing else, what would the user complain about on first use?" That complaint is the unstated requirement.

**Examples of implicit requirements:**

| Request | Unstated but implied |
|---|---|
| "Build a bot that forwards messages" | Rate limiting, retries on failure, behavior when target is offline, logging, graceful shutdown, message ordering |
| "Add a database layer" | Connection pooling, migration strategy, backup/restore, what happens on connection loss mid-transaction |
| "Implement file upload" | Size limits, type validation, virus scanning, storage cleanup, what happens on interrupted upload, filename sanitization |
| "Add user authentication" | Session expiration, password hashing (not plaintext), brute-force protection, secure cookie flags, CSRF protection |
| "Create a REST API" | Input validation, error response format, pagination for list endpoints, rate limiting, API versioning strategy |

#### Step 1 anti-patterns

- ❌ **Starting with the solution:** "I'll use library X to..." before understanding what problem you're solving. Technology is a how, not a what.
- ❌ **Treating the request as complete:** assuming the user stated everything they need. Users describe what they want, not what they need — the gap between those is where bugs live.
- ❌ **Asking too many questions:** if something is genuinely ambiguous in a way that would send you down the wrong path, ask one sharp question. Don't ask five clarifying questions when you could state an assumption and continue. Excessive questioning is a form of avoidance.
- ❌ **Asking too few questions:** if you're about to make an assumption that would be expensive to undo (architectural choice, technology selection, data model), ask before building. The cost of one question < the cost of rebuilding.

#### Step 1 checklist

```
[ ] Can I state the actual problem in one sentence, without mentioning a solution?
[ ] Have I identified at least two implied requirements the user didn't state?
[ ] Have I identified at least one failure mode and what "failure" looks like?
[ ] Do I know what "done" means — not "code compiles" but "feature works"?
[ ] Is anything genuinely ambiguous enough to justify a clarifying question?
```

#### Worked example — understanding the actual problem

> Request: "Add a cron job that cleans up old log files."

❌ **Bad (starts with solution):**
> "I'll add a Python script that runs via crontab and deletes files older than 7 days."

✅ **Good (understands first):**
> **Stated:** clean up old log files on a schedule.
> **Implied but unstated:**
> - How old is "old"? (Need to ask or assume — this is ambiguous enough to matter. Assuming 30 days, will state explicitly.)
> - Which directories? (Check — are logs in one place or scattered? Found: `./logs/` and `./data/tmp/`.)
> - What counts as a "log file"? (`.log` extension only? Or also `.log.gz` rotated files?)
> - What happens if the cleanup script fails? (Silent failure = disk fills up. Need alerting or at minimum error logging.)
> - What happens if a log file is currently being written to? (Delete would fail or corrupt — need to skip active files.)
> - Disk space: is this about saving space, or about compliance (data retention policy)?
> **Failure modes:** disk full (cleanup didn't run), active file deleted (log corruption), wrong files deleted (glob too broad).
> **Done means:** old logs are reliably removed, active logs are untouched, failures are reported not silently swallowed, and the cron schedule is documented.

---

### 2. Research before designing — real-time web fact-check is mandatory, not optional

Do not design from memory alone, especially for anything involving a library, API, framework, or protocol — these change fast and confident-sounding memory is often stale or simply wrong. This step is a **hard gate**: any claim that will shape the architecture (a package choice, a "requires system dependency X" statement, an API's current behavior) must be checked against a live web source in this session before it's allowed into the design. Memory-only claims do not get to load-bear the plan.

#### The source hierarchy — ranked by authority

| Rank | Source type | Trust level | When to use |
|---|---|---|---|
| **1** | **Official documentation** (docs site, API reference) | Highest | Always prefer. This is the canonical source of truth. |
| **2** | **Official registry page** (PyPI, npm, crates.io) | High | For package names, install commands, version constraints, dependency lists. |
| **3** | **Official repo README / wiki** | High | For setup instructions, architecture overview, known limitations. |
| **4** | **Official repo issues / discussions** | High (for gotchas) | For known bugs, edge cases, undocumented behavior, workarounds. Check date — old issues may be fixed. |
| **5** | **Official changelog / release notes** | High | For what changed between versions — deprecations, renames, breaking changes. |
| **6** | **Maintainer blog posts / talks** | Medium | For design intent, philosophy, roadmap. But intent ≠ implementation — verify behavior separately. |
| **7** | **Recent, dated third-party tutorials** (<12 months) | Medium-low | Useful for "how to" patterns, but verify code samples against official docs — tutorials lag behind API changes. |
| **8** | **Stack Overflow / forum answers** | Low | Useful as leads, but answers may be outdated, wrong, or for a different version. Always cross-check. |
| **9** | **Undated blog posts, old tutorials** (>18 months) | Very low | Treat as potentially stale. Use only if nothing fresher exists, and note the risk. |
| **10** | **Your own training data / memory** | Unverified | Not a source. A starting point for what to search for, not a substitute for searching. |

**If a lower-ranked source disagrees with a higher-ranked one, the higher-ranked one wins.** Say so explicitly ("the blog post recommends X, but the official docs show Y — going with Y") rather than silently picking the more convenient one.

#### How to fact-check properly — the seven rules

**Rule 1: Search, don't guess the URL.**
Use live web search for the package/library/API name plus the current year. Don't reconstruct a URL from memory and treat it as verified. A URL you remember might redirect, be archived, or point to a fork.

**Rule 2: Prefer the source hierarchy (above).**
Official docs > official repo > recent third-party > old/undated. If your first hit is a blog post, keep searching for the official source. The blog post is a lead, not a destination.

**Rule 3: Cross-check load-bearing claims against at least two independent sources.**
Especially for anything that will drive an architectural decision. "TTS engine X needs system dependency Y" — check the official repo's install instructions *and* at least one recent issue thread, because quickstart snippets sometimes bundle optional dependencies as if they're mandatory.

**Rule 4: Read past the first line of a claim.**
A README that says "install X for feature support" is not the same as "X is required." Check whether the dependency is universal or conditional before writing it into the architecture as a hard requirement.

**Rule 5: Check dates.**
A Stack Overflow answer from 18+ months ago may describe since-changed behavior. Prefer the most recently updated source. Note in the decision log if the freshest source you found is old and you're relying on it anyway.

**Rule 6: Verify exact package names against an official source.**
Not by pattern-matching what "sounds right." Near-miss names (`-voice`, `-onnx`, `-fork`, org-prefixed) are different projects with different APIs. Confirm the install command matches the API you're writing against.

**Rule 7: Verify version constraints explicitly.**
Don't assume "recent Python/Node" means "supported." Check the library's actual supported-version range, especially when the user has a newer or older runtime than typical.

#### The "stable knowledge" exception

If you already know a domain cold and it's:
- Stable (doesn't change across versions — e.g., a sorting algorithm, a language builtin like Python's `dict.get()`)
- Low-stakes (won't break anything if you're slightly wrong)
- Not the kind of thing that has version-specific behavior

Then say explicitly: "Relying on stable knowledge here — `dict.get()` has returned the default value for missing keys since Python 2. Safe to skip the live check." This is a stated exception, not a default. The bar is: "would a fact-check here teach me anything new?" If the answer is "almost certainly not," the exception is appropriate. If you're not sure, check.

#### Step 2 anti-patterns

- ❌ **"I checked" without showing work:** claiming you verified something without logging what you searched, what source you found, or what it said. Checking without logging is indistinguishable from not checking.
- ❌ **Guessing a URL and treating it as a source:** going directly to `https://pypi.org/project/moonshine/` from memory instead of searching for the actual project. The URL might exist but point to a different project.
- ❌ **Reading only the install section:** checking the install command but not the usage API, compatibility notes, or known issues. Verification is end-to-end, not just "does the package exist."
- ❌ **Checking one source and stopping:** a single blog post is not verification. Cross-check load-bearing claims.
- ❌ **Treating absence of contradicting info as confirmation:** "I searched and didn't find anything saying it doesn't work" is not the same as "I found a source saying it does work." See `01` Section 5c on absence-of-evidence.
- ❌ **Relying on search snippets without clicking through:** the snippet shown by the search engine may be truncated, out of context, or from a different section than you think.

#### Step 2 checklist

```
[ ] For every package/library/API in the design: have I checked the exact
    name, install command, and current API against a live source?
[ ] For every "requires X" claim: have I verified whether it's universal
    or conditional, and for which use cases?
[ ] Have I cross-checked load-bearing claims against ≥2 sources?
[ ] Have I checked dates on all sources and noted any that are old?
[ ] Have I logged what I searched, what I found, and what it confirmed
    or denied — not just the conclusion?
[ ] For any "stable knowledge" exception: have I stated it explicitly
    and explained why a live check wouldn't add value?
```

#### Worked example — the full fact-check lifecycle of a single claim

> Claim to verify: "The `httpx` library supports HTTP/2 out of the box."

**Step 1: Search, don't guess.**
> Searched: "httpx python HTTP/2 support 2026"

**Step 2: Find the source hierarchy.**
> First hit: a blog post from 2024 — "How to use HTTP/2 with httpx." (Rank 7 — third-party, dated.)
> Second hit: official httpx docs page on HTTP/2. (Rank 1 — official docs.)
> Going with the official docs as primary.

**Step 3: Read the official source carefully.**
> Official docs say: "HTTP/2 support is available but requires installing httpx with the `h2` optional dependency: `pip install httpx[http2]`. It is not included in the base install."
> Key detail: **not** "out of the box" — requires an optional extra.

**Step 4: Cross-check.**
> Checked PyPI page for `httpx` (Rank 2): lists `h2` under `[http2]` optional extras. Confirms the official docs.
> Checked the blog post (Rank 7): says "httpx supports HTTP/2" without mentioning the optional extra. This is technically true but misleading — it's the kind of imprecision that leads to a broken install.

**Step 5: Log the finding.**
> ```
> Fact-checked: "httpx supports HTTP/2 out of the box"
>   — PARTIALLY TRUE. HTTP/2 is supported, but NOT in the base install.
>   — Requires: `pip install httpx[http2]` (the `h2` extra)
>   — Per: official httpx docs + PyPI page (both checked this session)
>   — The blog post that said "supports HTTP/2" was technically correct
>     but omitted the optional extra — exactly the "read past the first
>     line" failure mode
> ```

**Step 6: Update the design.**
> Changed the install command from `pip install httpx` to `pip install httpx[http2]` in the design doc. Added a note that HTTP/2 is opt-in, not default.

---

### 3. Draft a design — stay in Plan mode for this

Write out briefly: the approach, the key structural decisions, and what you're explicitly choosing not to do and why.

#### What a good design draft contains

```
APPROACH
  [one paragraph: what this does and how, at a high level]

KEY DECISIONS
  - [decision 1]: [choice] because [reason]. Alternative was [X], rejected because [Y].
  - [decision 2]: ...

EXPLICITLY NOT DOING
  - [thing 1]: [why not — out of scope / not needed / deferred to future]
  - [thing 2]: ...

FILE CHANGES
  - [file 1]: [what changes and why]
  - [file 2]: [new file — what it does]

DEPENDENCIES
  - [new dependency, if any]: [why needed, version constraint, fact-checked per Step 2]

OPEN QUESTIONS
  - [anything unresolved that could change the design]
```

#### When to use deep planning vs. quick plan

| Situation | Planning level |
|---|---|
| Single file, one function, no architectural decision | Quick plan — a few bullet points in Plan mode. |
| 2–3 files, one clear approach, no new dependencies | Quick plan with file-change list. |
| Multi-file, architectural decision required | Deep planning — save the plan as a file for reference. |
| New module/subsystem | Deep planning — and this triggers `02`'s full pipeline too. |
| Refactoring existing architecture | Deep planning — you need to model the current state before designing the new one. |

#### Step 3 anti-patterns

- ❌ **Designing in Act mode:** writing code as a way to "figure out the design." This produces code that encodes the first idea you had, not the best one.
- ❌ **Over-designing:** writing a 2000-word design for a 20-line function. Keep the plan proportional to the complexity.
- ❌ **Under-designing:** "I'll just write a function that does X" with no consideration of edge cases, error handling, or how it integrates. Even small functions benefit from 30 seconds of design thought.
- ❌ **Not documenting rejections:** only writing what you chose, not what you considered and rejected. The rejected alternatives are often more informative than the chosen one — they explain the constraints.

#### Worked example — design draft

> Task: add retry logic to the HTTP client in `api_client.py`.

```
APPROACH
  Add exponential backoff retry to all HTTP requests made via
  `api_client.request()`. Retries on 429 (rate limit), 502/503/504
  (server errors), and ConnectionError. No retry on 4xx client errors
  (except 429) — those are caller bugs, not transient failures.

KEY DECISIONS
  - Retry library: using tenacity (already a project dependency per
    requirements.txt) over hand-rolled retry loop — tenacity handles
    jitter, max attempts, and wait strategies out of the box.
    Fact-checked: tenacity is at v8.5.0, supports Python 3.9+
    (our minimum), per PyPI (checked this session).
  - Max retries: 3 attempts (1 original + 2 retries). Configurable
    via parameter, default 3.
  - Backoff: exponential with jitter — base 1s, max 30s. Jitter
    prevents thundering herd on shared rate limits.
  - Retry on 429: yes, with Retry-After header respected if present.

EXPLICITLY NOT DOING
  - Circuit breaker pattern: overkill for a single-user desktop app
    with one API consumer. Would add complexity without benefit here.
  - Per-endpoint retry config: all endpoints share the same retry
    policy. Can be split later if needed.

FILE CHANGES
  - api_client.py: wrap `request()` method with tenacity retry
    decorator. Add `max_retries` parameter.

DEPENDENCIES
  - None new — tenacity already in requirements.txt (confirmed).

OPEN QUESTIONS
  - Should failed requests (after all retries exhausted) log the full
    response body, or just the status code? Full body may contain
    sensitive data. Defaulting to status code + headers only.
```

---

### 4. Critique your own draft before writing code

Re-read the draft as if reviewing someone else's plan. This is not a formality — this second pass is where most of the real thinking happens. Do not skip it because the first draft "looks fine."

#### The critique protocol — five lenses

**Lens 1: Breakage**
> What's the most likely way this breaks? Not "what's a theoretical edge case" but "if I shipped this right now, what would fail first in real usage?"

**Lens 2: Unverified assumptions**
> What did I assume without checking — and did I actually go back and check it, or just note that I should? A noted-but-unchecked assumption is still an unverified assumption.

**Lens 3: Simplicity**
> Is there a simpler approach with less surface area for bugs? Complexity is a cost. If two approaches solve the problem equally well, the simpler one is better unless the complex one offers a specific, stated advantage.

**Lens 4: Security**
> What would a security-minded reviewer flag? This is not the full security pass (that's Step 9) — this is a quick scan for obvious holes: unsanitized input, hardcoded credentials, insecure defaults, injection surfaces.

**Lens 5: Source quality**
> Does any claim in this draft rest on a single, unverified, or outdated source? If so, go back to Step 2 for that claim specifically before proceeding.

#### Step 4 anti-patterns

- ❌ **Rubber-stamping:** going through the critique motions without actually looking for problems. If your critique consistently finds nothing, you're not critiquing — you're confirming.
- ❌ **Critiquing the wrong level:** pointing out code-style issues in a design-level review. The critique should focus on whether the *approach* is right, not whether the *code* is pretty.
- ❌ **Finding problems and not fixing them:** noting "this could break under X" and then proceeding without addressing X. A critique that doesn't change anything wasn't a critique.
- ❌ **Only critiquing what's easy to fix:** finding the simple issues and missing the structural ones. The hardest-to-fix problem is usually the most important one.

#### Step 4 checklist

```
[ ] Breakage: what's the most likely real-world failure? Have I addressed it?
[ ] Assumptions: everything in the draft that states a fact — is it fact-checked
    or flagged as assumption?
[ ] Simplicity: is there a simpler approach I dismissed too quickly?
[ ] Security: any obvious holes (unsanitized input, hardcoded creds, injection)?
[ ] Source quality: any claim resting on one unverified or old source?
[ ] Did the critique actually change something, or did I just nod through it?
```

#### Worked example — critiquing the retry-logic design

> Critiquing the design draft from Step 3:

**Lens 1 (Breakage):**
> Most likely failure: retrying on 429 without checking the `Retry-After` header could violate the API's rate limit and get the client IP-banned. The design mentions respecting `Retry-After` but doesn't specify what to do if the header value is unreasonably large (e.g., 3600 seconds). **Fix:** add a max `Retry-After` cap (e.g., 60 seconds) — if the server says "wait an hour," surface that as an error rather than blocking for an hour.

**Lens 2 (Assumptions):**
> Assumed: tenacity supports `Retry-After` header parsing. **Checked:** tenacity doesn't natively parse `Retry-After` — it provides wait strategies but not HTTP-header-aware ones. Need to implement a custom wait function that reads the header. Updated the design.

**Lens 3 (Simplicity):**
> Could use `urllib3.util.Retry` instead of tenacity, since `requests` (which `api_client.py` uses) is built on urllib3 and has built-in retry support via HTTPAdapter. This would be simpler (no custom wait function needed) and uses a dependency already present. **Reconsidering.** Fact-checked: `urllib3.util.Retry` does support `Retry-After` natively via the `respect_retry_after_header` parameter (per urllib3 docs, checked this session). **Changed approach:** using urllib3 retry via HTTPAdapter instead of tenacity.

**Lens 4 (Security):**
> Logging the response on failure: the draft correctly limits to status code + headers only (no body). But headers could contain `Authorization` or `Set-Cookie` values. **Fix:** filter sensitive headers before logging.

**Lens 5 (Source quality):**
> urllib3 `Retry` class docs checked this session (official docs). Tenacity check also done. Both current. ✅

---

### 5. Keep a running decision log, with sources

Log what you decided and why, especially rejected alternatives, open questions, and **what you fact-checked and where**, as you go through steps 1–4.

#### Where to keep the log

- In the `/deep-planning` output file if one exists for this task
- Otherwise in `memory-bank/activeContext.md` under a "Decisions" section
- If a `devlog.py` script exists at the repo root, check its `--help` output for how it expects entries, and use it as well — do not assume a CLI syntax for it without checking

#### The decision log format

```
─────────────────────────────────────────────────────────────
DECISION LOG — [task description]
Date: [YYYY-MM-DD]
─────────────────────────────────────────────────────────────

DECISIONS
  - Decided: [what you chose] over [what you rejected] because [why]
  - Rejected: [approach] — [why it doesn't work, specific failure mode]

FACT-CHECKED CLAIMS
  - Fact-checked: [claim]
    Searched: "[exact query]"
    Source: [what source, its rank in hierarchy]
    Finding: [what the source confirmed or denied]
    Cross-check: [second source, if load-bearing claim]

  - Fact-checked: [claim]
    [same structure]

VERIFIED VIA DOCS
  - Verified: [API/behavior claim]
    Source: [official docs URL or description]
    Finding: [exact current behavior]

UNVERIFIED ASSUMPTIONS
  - Assumed: [claim] — [why not checked + flag for user]
  - Assumed: [claim] — relied on stable knowledge because [reason]

OPEN QUESTIONS
  - [question] — [impact on design if answered differently]

CAVEATS
  - Caveat: freshest source found was from [timeframe] — flagging
    in case behavior has since changed
  - Caveat: only one source for [claim] — couldn't cross-check
─────────────────────────────────────────────────────────────
```

#### The completeness rule

A decision log entry that states a fact without a `Fact-checked:` or `Verified via docs:` line is incomplete — either add the check or explicitly mark it as an unverified assumption the user should confirm.

#### Decision log anti-patterns

- ❌ **Conclusions without sources:** "Decided: use library X" with no fact-checked line explaining how you confirmed X is the right library.
- ❌ **Sources without conclusions:** logging that you searched for something but not stating what you concluded from it.
- ❌ **Logging only the happy path:** documenting what you chose but not what you rejected or why. The rejections are often more valuable — they capture the constraints.
- ❌ **Retroactive logging:** writing the decision log after the code is done, from memory. The log should be written as you go, not reconstructed.
- ❌ **Missing the "searched" field:** writing "Fact-checked: X is true per docs" without saying what you searched for. The search query is evidence that you actually looked, not just recalled.

#### Worked example — a complete decision log entry

> Task: choosing an HTTP client library for the project.

❌ **Bad log:**
```
- Decided: use httpx for HTTP calls
- It supports async and HTTP/2
```

✅ **Good log:**
```
─────────────────────────────────────────────────────────────
DECISION LOG — HTTP client library selection
Date: 2026-07-15
─────────────────────────────────────────────────────────────

DECISIONS
  - Decided: httpx over requests+aiohttp because httpx provides both
    sync and async APIs in one library, reducing the dependency count
    and avoiding two different HTTP clients with different error types
  - Rejected: requests — sync only, would need aiohttp for async paths,
    doubling the HTTP surface area
  - Rejected: aiohttp — async only, would need requests for sync paths
    in the CLI tools, same doubling problem

FACT-CHECKED CLAIMS
  - Fact-checked: "httpx supports HTTP/2"
    Searched: "httpx python HTTP/2 support 2026"
    Source: official httpx docs (Rank 1)
    Finding: HTTP/2 is supported but NOT in base install — requires
      `pip install httpx[http2]` (the h2 extra)
    Cross-check: PyPI page for httpx (Rank 2) — confirms h2 is an
      optional extra under [http2]

  - Fact-checked: "httpx supports async"
    Searched: "httpx async client"
    Source: official httpx docs (Rank 1)
    Finding: yes — `httpx.AsyncClient` for async, `httpx.Client` for
      sync. Same API surface.
    Cross-check: httpx GitHub README (Rank 3) — confirms.

  - Fact-checked: "httpx supports Python 3.9+"
    Searched: "httpx minimum python version"
    Source: PyPI page (Rank 2)
    Finding: requires Python 3.8+. Our project minimum is 3.9. ✓

UNVERIFIED ASSUMPTIONS
  - Assumed: httpx performance is comparable to requests for our
    use case (low-volume API calls, not benchmarked). Low-stakes:
    performance difference is negligible at our call volume.

CAVEATS
  - HTTP/2 requires the optional [http2] extra — do NOT install
    bare `pip install httpx` if HTTP/2 is needed
─────────────────────────────────────────────────────────────
```

---

### 6. Re-check implementation details right before coding

Right before switching to Act mode, do one more targeted, live check: exact current syntax, method signatures, and idioms for the specific language/library/version in play.

#### Why this step exists separately from Step 2

Step 2 checks the *design-level* claims: "does library X support feature Y?" Step 6 checks the *implementation-level* details: "what is the exact current syntax for feature Y in library X version Z?" These are different questions, and the answer to Step 6 can change even when Step 2's answer hasn't.

#### What to re-check, specifically

| Category | What to verify | Why it's critical here |
|---|---|---|
| **Auth / crypto** | Exact function names, parameter order, default algorithms, key sizes | Getting auth wrong doesn't throw an error — it silently provides weak security. |
| **SQL / ORM** | Current query syntax, parameter binding method, escaping rules | SQL injection is the #1 web vulnerability. A wrong parameterization pattern doesn't error — it injects. |
| **Subprocess / exec** | Shell escaping, argument passing (list vs string), platform differences | The difference between `subprocess.run(["cmd", arg])` and `subprocess.run(f"cmd {arg}", shell=True)` is the difference between safe and injectable. |
| **File paths from user input** | Path traversal prevention, encoding, platform separators | `../../../etc/passwd` in a filename field. Cross-platform path handling. Null bytes in paths. |
| **Deserialization** | Safe loaders, allowed classes, size limits | `pickle.load`, `yaml.load`, `eval` — each has a safe variant that's one parameter change away from the dangerous one. |
| **Network requests** | TLS verification defaults, timeout defaults, redirect following | Some HTTP clients disable TLS verification or follow redirects by default in certain configurations. |
| **Environment variables** | Whether they're loaded, validated, and have safe defaults | Missing env vars that fail silently or default to development settings in production. |

#### Step 6 checklist

```
[ ] For every API call I'm about to write: have I checked the exact
    current syntax in official docs (not from memory)?
[ ] For auth/crypto: is the function name, parameter order, and default
    algorithm still current?
[ ] For SQL: am I using parameterized queries, not string formatting?
[ ] For subprocess: am I passing args as a list, not a shell string?
[ ] For file paths: am I validating and sanitizing, especially on Windows?
[ ] Has anything in the design changed since Step 2 that would make the
    Step 2 fact-check stale?
```

#### Worked example — Step 6 catch

> Design says: "Use `bcrypt.hashpw(password, bcrypt.gensalt())` for password hashing."
>
> Step 6 check: searched "bcrypt python current API 2026" — official docs show that `bcrypt.hashpw()` expects `bytes`, not `str`. The design draft passed a string. Without this check, the code would throw `TypeError` at runtime.
>
> Also caught: `bcrypt.gensalt()` defaults to 12 rounds (checked official docs). Our security policy says minimum 14. Updated: `bcrypt.gensalt(rounds=14)`.

---

### 7. Write the code — Act mode

Implement the revised design. This is Act mode — you're now writing code, not planning.

#### Code quality rules

- **Keep functions small and testable** rather than one large block. Each function should do one thing and be independently testable.
- **Make edits incrementally** so each one is reviewable and checkpoint-able — not one giant unreviewable diff. If you're changing 500 lines across 10 files, break it into logical commits/steps.
- **Follow existing patterns** in the codebase. If the project uses `snake_case`, don't introduce `camelCase`. If it uses async/await, don't introduce thread-based code. Consistency > personal preference.
- **Handle errors explicitly.** Every external call (network, file, database, subprocess) needs error handling. "It probably won't fail" is not error handling.
- **No magic values.** Named constants, not numeric literals. Configuration, not hardcoded strings. Environment variables for secrets, not source code literals.

---

#### 🔴 MANDATORY: Implementation Verification Gate — runs AFTER code is written, BEFORE Step 8

This gate exists because of a specific recurring failure pattern: the model reasons correctly in Steps 3–4 (design, critique), then writes code that ignores what the reasoning concluded. The reasoning was good; the implementation didn't carry it through. This gate closes that gap by **forcing an explicit cross-check between every design decision and the actual written code.**

**This gate is non-skippable. It runs after you have written the code, not before.**

Run through this checklist for every piece of code written in this step:

```
IMPLEMENTATION VERIFICATION GATE

For each KEY DECISION in the Step 3 design draft:
  [ ] Decision: [restate it] → Is it actually implemented in the code? Point to the exact line/function.
  [ ] If NOT implemented: explain why (out of scope change?) or implement it now.

For each finding from the Step 4 CRITIQUE (Lenses 1–5):
  [ ] Critique finding: [restate it] → Is the fix actually in the code? Point to the exact line/function.
  [ ] If fix is missing: it is a bug, not a known limitation. Implement it now.

For each EDGE CASE / FAILURE MODE from MS-4 case list:
  [ ] Case: [restate it] → Is there explicit code handling this case? Point to it.
  [ ] "It probably won't happen" is not handling. Either handle it or flag it as an explicit accepted limitation.

For each OPEN QUESTION resolved during design:
  [ ] Resolution: [what was decided] → Is that decision reflected in the code? Where?

For each ASSUMPTION marked in the decision log:
  [ ] Assumption: [restate it] → Does the code handle the case where this assumption is wrong?
  [ ] If the assumption is wrong and the code has no fallback: this is a hidden bug. Fix it.
```

**If any checkbox above reveals a gap: fix it immediately, in this step — not in a TODO, not in the next step, not "later." A critique finding that isn't in the code is not fixed.**

#### The verification gate in practice

Write the code, then **explicitly go back to your Step 3 design draft and Step 4 critique findings**, item by item, and verify each one is present in the code. Don't read your own code looking for completeness (you'll see what you expect to see). Read the *plan* and *check the code against it*.

**Example — what this catches:**

> Step 4 Critique (Lens 1): "retrying on 429 without checking Retry-After could get IP-banned. Fix: add a max Retry-After cap of 60 seconds."
>
> Code written in Step 7: implements retry logic, respects `Retry-After` header — **but the cap is missing**. The reasoning in Step 4 identified the problem, but the implementation silently skipped the fix.
>
> Verification gate catches: `Critique finding: Retry-After cap at 60s → NOT in code`. Fix implemented.

#### Step 7 anti-patterns

- ❌ **One giant diff:** writing all the code at once and presenting it as one big "here's everything." Break it into reviewable, logical steps.
- ❌ **Diverging from the design:** implementing something different from what the plan says without updating the plan. If the plan was wrong, update it — don't silently change course.
- ❌ **Copy-pasting without understanding:** using code from a tutorial, Stack Overflow, or another project without understanding every line. Copy-pasted code is the #1 vector for hidden bugs and security vulnerabilities.
- ❌ **Suppressing errors to make it "work":** adding try/except/pass or empty catch blocks to silence errors. The error is information — handle it or surface it.
- ❌ **TODO comments as a substitute for implementation:** "TODO: add error handling" is not error handling. If the design says to handle errors, handle them now.
- ❌ **Skipping the verification gate:** completing code without running the cross-check. Reasoning in Steps 3–4 that doesn't map to actual code is worthless — the gate is what makes the reasoning actionable.

---

### 8. Actually run it — never skip this

Reading code back is not the same as testing it. Run the script, hit the endpoint, execute the test suite, exercise the bot command. Watch the terminal output and fix whatever breaks.

#### What "actually run it" means

| Code type | How to test |
|---|---|
| Script / CLI tool | Execute with representative input. Check exit code, stdout, stderr. |
| API endpoint | Send requests (curl, httpie, or a test client). Check response status, body, headers. |
| Library / module | Write and run a quick test script that exercises the public API. |
| Bot / interactive tool | Trigger the actual commands/interactions. Watch the logs. |
| UI component | Load it in the browser/app. Click things. Check the console for errors. |
| Background job / cron | Trigger it manually. Watch the logs. Check that the expected side effects occurred. |

#### When execution reveals a wrong assumption

If execution fails because a fact-checked assumption turned out wrong anyway (a package doesn't behave as its docs suggested, a version mismatch, etc.), **this is new information, not a debugging problem.** Don't patch around it — go back to Step 2, re-verify against a live source, update the decision log, and revise the approach if needed.

The difference between a bug and a wrong assumption:
- **Bug:** the code doesn't do what the design intended. Fix the code.
- **Wrong assumption:** the code does what the design intended, but the design was based on incorrect information. Fix the design, then fix the code.

Patching around a wrong assumption — adding workarounds, try/except blocks, or "just make it not crash" fixes — creates fragile code that works by accident, not by design.

#### When you can't execute

If you cannot execute something in this environment (no API key, no database, no hardware), say so explicitly rather than presenting untested code as verified. State:
- What you would test if you could.
- What the most likely failure modes are.
- What the user should test before relying on this code.

#### Step 8 anti-patterns

- ❌ **Reading the code back instead of running it:** "I've reviewed the code and it looks correct." Review is not testing.
- ❌ **Running only the happy path:** testing with normal input and skipping edge cases, error cases, and boundary conditions.
- ❌ **Ignoring warnings:** code runs without errors but produces deprecation warnings, import warnings, or performance warnings. These are future bugs announcing themselves.
- ❌ **Patching silently on failure:** the code fails, you fix it without updating the decision log or understanding *why* the assumption was wrong.
- ❌ **"Works on first try, ship it":** if a non-trivial piece of code works perfectly on the first run, that's suspicious, not reassuring. Did you actually test the error paths?

#### Step 8 checklist

```
[ ] Did I run the code, not just read it?
[ ] Did I test with representative input (not just the simplest possible case)?
[ ] Did I check for warnings, not just errors?
[ ] Did I test at least one error path (not just the happy path)?
[ ] If something failed: did I trace the failure to a wrong assumption or a
    code bug? If wrong assumption → back to Step 2, not just a quick fix.
[ ] If I couldn't execute: did I state what I would test and what the user
    should verify?
```

---

### 9. Run a security pass — a distinct pass from "does it work"

Before calling anything done, review the code specifically through a security lens. This is a separate pass because security bugs don't show up in normal testing — they require adversarial thinking.

#### The security review protocol

For each category below: actively look for the pattern, don't just skim for it. If the code doesn't touch that category, mark it N/A and move on. If it does, verify the specific items listed.

**Category 1: Secrets and credentials**
- No hardcoded API keys, tokens, passwords, or connection strings in source code.
- Secrets loaded from environment variables or a secrets manager, not config files committed to git.
- Check: `.gitignore` includes `.env`, secret files, and credential directories.
- Check: no secrets in log output, error messages, or exception traces.

Detection patterns:
```
# Search for these patterns in your code:
- String literals that look like keys: "sk-", "pk-", "ghp_", "AKIA", "Bearer "
- Variable names: password, secret, token, key, api_key, auth (check their values)
- Connection strings with embedded credentials: "postgres://user:pass@host"
- Base64-encoded strings that decode to credentials
```

**Category 2: Injection**
- SQL: all queries use parameterized statements (`?` placeholders, `%s` with params tuple), never string concatenation or f-strings.
- Shell: `subprocess.run()` with argument list, never `shell=True` with unsanitized input. If `shell=True` is necessary, justify and sanitize.
- Template: no user input directly in template strings without escaping (XSS).
- Path: no user input directly in file paths without canonicalization and bounds checking.

Detection patterns:
```
# SQL injection:
  f"SELECT * FROM users WHERE id = {user_id}"      ← VULNERABLE
  cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))  ← SAFE

# Command injection:
  subprocess.run(f"convert {filename}", shell=True)  ← VULNERABLE
  subprocess.run(["convert", filename])              ← SAFE

# Path traversal:
  open(f"uploads/{user_filename}")                   ← VULNERABLE
  path = Path("uploads") / user_filename
  path.resolve().relative_to(Path("uploads").resolve())  ← SAFE (validates)
```

**Category 3: Input validation**
- All external input (user input, API requests, file contents, environment variables) is validated before use.
- Validation rejects unexpected types, sizes, and formats — not just expected-bad values.
- Validation happens at the boundary (where input enters the system), not deep inside business logic.

Detection patterns:
```
# Missing validation:
  def process(data):
      return data["key"]["nested"]  ← Crashes on missing key, wrong type

# Proper validation:
  def process(data):
      if not isinstance(data, dict) or "key" not in data:
          raise ValueError("Invalid input: expected dict with 'key'")
      # ...proceed safely
```

**Category 4: Deserialization**
- `pickle.load()` / `pickle.loads()`: never on untrusted data. If necessary, use a safe alternative (`json`, `msgpack`, protocol buffers).
- `yaml.load()`: always with `Loader=yaml.SafeLoader`, never `yaml.load(data)` (which defaults to `FullLoader` or `UnsafeLoader` depending on version — both allow arbitrary code execution).
- `eval()` / `exec()`: never on user input or external data. If dynamic execution is genuinely needed, use a sandboxed alternative (`ast.literal_eval` for simple data structures).

**Category 5: Insecure defaults**
- TLS/SSL: verification enabled (`verify=True` for `requests`/`httpx`). Never `verify=False` in production.
- CORS: not `Access-Control-Allow-Origin: *` unless the API is genuinely public. Specify exact allowed origins.
- Auth: no endpoint is accidentally unprotected. Check that auth middleware covers all routes, including new ones you just added.
- File permissions: created files don't have world-readable/writable permissions (especially config files, credential files, log files).
- Cookie flags: `HttpOnly`, `Secure`, `SameSite` set appropriately on session cookies.

**Category 6: Dependencies**
- Check for known vulnerabilities in any new dependency you've added. Search: "[library name] CVE" or "[library name] security advisory" for recent issues.
- Check this live — don't assume a library is safe because it was safe as of training data. New CVEs are published constantly.
- If using a dependency you haven't used before, check its maintenance status: when was the last release? Are security issues addressed promptly? Is it actively maintained or abandoned?

**Category 7: Information disclosure**
- Error messages shown to users don't include stack traces, file paths, internal IP addresses, database schema, or other implementation details.
- Logs don't contain sensitive data (passwords, tokens, PII) in plain text.
- Debug mode is not enabled in production configuration.
- API error responses return generic error messages, not detailed internal state.

#### When the security pass finds something

Don't just note it — fix it. A logged security issue that isn't fixed is worse than an undiscovered one — it's a documented liability.

If fixing it requires a design change, go back to Step 3 (design) rather than patching around the security issue at the code level. Security patches at the code level, without addressing the design that allowed the vulnerability, tend to be incomplete.

#### Security pass checklist

```
[ ] Secrets: no hardcoded credentials in source, secrets from env/secrets manager
[ ] Injection: all SQL parameterized, no shell=True with user input, no path traversal
[ ] Input validation: all external input validated at boundary
[ ] Deserialization: no pickle/yaml.load/eval on untrusted data
[ ] Defaults: TLS on, CORS restricted, auth checked, file perms restrictive
[ ] Dependencies: new deps checked for known CVEs (live search)
[ ] Information disclosure: no internals in error messages/logs
[ ] If security_scan.py exists: ran it and resolved all findings
```

#### Worked example — security pass catching real issues

> Code under review: a file-upload endpoint.

**Category 1 (Secrets):** ✅ No credentials in source. Upload path from env var. `.env` in `.gitignore`.

**Category 2 (Injection):**
> ❌ **Found:** `filename = request.files['file'].filename` used directly in `os.path.join(upload_dir, filename)`. A filename like `../../../etc/cron.d/malicious` would write outside the upload directory.
> **Fix:** sanitize with `werkzeug.utils.secure_filename()`, then verify the resolved path is within the upload directory:
> ```python
> safe_name = secure_filename(filename)
> full_path = Path(upload_dir) / safe_name
> full_path.resolve().relative_to(Path(upload_dir).resolve())  # raises if traversal
> ```

**Category 3 (Input validation):**
> ❌ **Found:** no file size limit. A 10GB upload would exhaust memory/disk.
> **Fix:** added `MAX_CONTENT_LENGTH = 10 * 1024 * 1024` (10MB) to Flask config + explicit size check in the handler.

> ❌ **Found:** no file type validation. Any file type is accepted.
> **Fix:** whitelist of allowed MIME types + magic-byte verification (don't trust Content-Type header alone — it's caller-controlled).

**Category 7 (Information disclosure):**
> ❌ **Found:** on upload failure, the error message includes the full file path: `f"Failed to save to {full_path}"`. This leaks the server's directory structure.
> **Fix:** changed to `"Upload failed. Please try again."` — detailed path logged server-side only.

---

### 10. Close the loop

Before ending the task, complete two final actions:

#### Update project memory

- **`memory-bank/activeContext.md`:** current focus, recent changes, next steps.
- **`memory-bank/progress.md`:** what works, what's left, known issues.

If these files don't exist, note that you would update them if they did — but don't create a memory-bank structure the project doesn't use.

#### State the verification status

State plainly what you actually verified versus what you didn't:

```
VERIFIED
  - [x] Tests run: [which tests, what they cover]
  - [x] Security pass complete: [categories checked]
  - [x] Sources checked live: [list of fact-checked claims]

NOT VERIFIED
  - [ ] Could not test against real API (no API key in this environment)
  - [ ] Could not test on target OS (running on Linux, target is Windows)
  - [ ] Source for [claim] was old/single/unofficial

RESIDUAL RISK
  - [assumption still standing, with assessment of likelihood and impact]
  - [edge case not tested, with assessment]
```

#### Step 10 anti-patterns

- ❌ **"Done"** — ending the task with one word and no verification summary.
- ❌ **Overclaiming verification:** "Fully tested and verified" when you tested the happy path only.
- ❌ **Not mentioning what wasn't tested:** if you couldn't test something, say so. Silence implies verification.
- ❌ **Leaving the decision log incomplete:** if new facts surfaced during implementation (Steps 7–9), they should be in the decision log — not just in the code comments.

---

## Known trap categories

These are non-exhaustive — a reminder to stay alert, not a complete list. Each trap has a specific shape that makes it recognizable.

### Trap 1: Near-miss package names

**Shape:** the plausible name and the real name differ by a word or suffix, and both may exist on the registry as *different* projects.

**Examples:**
- `moonshine` (satellite imagery) vs `useful-moonshine` (speech recognition)
- `kokoro-onnx` (ONNX-based TTS) vs `kokoro` (might be a different project)
- `python-dotenv` vs `dotenv` (different projects on PyPI)
- `pillow` vs `PIL` (Pillow is the fork, PIL is unmaintained — but both import as `PIL`)

**Detection:** always verify the install command against the API you're about to use. If the PyPI page's description doesn't match the functionality you need, you have the wrong package.

### Trap 2: Overstated system dependencies

**Shape:** official quickstart snippets install an optional dependency unconditionally (e.g. for a Colab demo covering every language) even though your specific use case doesn't need it.

**Detection:** when you see "install X" in a quickstart, check: is X needed for *all* usage, or only for specific features/languages/platforms? Read the actual conditional logic, not just the install cell.

### Trap 3: Silent version ceilings

**Shape:** a library quietly drops support for the newest language/runtime version before its docs are updated to say so.

**Detection:** check the library's current supported-version range *and* its CI matrix (if visible in the repo). Don't assume "recent Python" means "supported Python."

### Trap 4: Platform-specific footnotes

**Shape:** "works out of the box" claims are frequently OS-specific and the exception is buried lower in the same doc.

**Detection:** when a source says "works on" or "supports" without qualification, check: does it name specific platforms? If not, look for platform-specific notes, known issues, or CI matrix.

### Trap 5: Stale tutorials outranking official docs

**Shape:** a well-SEO'd 2-year-old blog post outranks the current official page in search results.

**Detection:** always check: is this result from the *official* source? If not, find the official source and compare. If they disagree, the official source wins — even if the blog post ranks higher.

### Trap 6: "Required" vs "recommended" conflation

**Shape:** documentation says "install X for Y support" and you read it as "X is required." But "for Y support" means "if you want Y" — not "or it crashes."

**Detection:** when you see "requires," "needs," or "install X for Y," check: what happens if X is absent? Crash, degraded output, or a missing-but-non-essential feature?

### Trap 7: API changes between major versions

**Shape:** you find the correct package and the correct function name, but the parameter names, defaults, or behavior changed between the version you read about and the version installed.

**Detection:** check: which version is in the project's requirements? Which version is on the latest docs? Are they the same? If not, find the docs for the installed version, not the latest.

### Trap 8: Copy-paste contagion

**Shape:** a wrong code pattern (e.g., insecure SQL query, incorrect import path) appears in multiple blog posts, tutorials, and Stack Overflow answers because they all copied from the same original source. The pattern looks "consensus-verified" but is actually one source multiplied.

**Detection:** if multiple third-party sources show the exact same code pattern, check: do they cite each other? Do they all use the same variable names (suggesting copy-paste)? Verify against the official docs, which are less susceptible to copy-paste chains.

---

## Worked examples

These are illustrative, not exhaustive — the pattern matters more than the specific libraries named.

### Example 1 — near-miss package name

> Task: "Add speech-to-text using the Moonshine model."

❌ **Bad (memory-only):**
> "Install it with `pip install moonshine`, then `import moonshine`."
> *(This is wrong — `moonshine` on PyPI is an unrelated satellite-imagery package. The actual speech package is a different name entirely, and there are multiple similarly-named forks/orgs publishing under near-identical names.)*

✅ **Good (verified):**
> Searches live, lands on the actual project's GitHub/PyPI page, confirms the exact install command shown there, and notes in the decision log:
> ```
> Fact-checked: correct package + import name confirmed via [official repo]
>   — NOT the plain "moonshine" PyPI package which is a different
>     (satellite imagery) project
>   Searched: "moonshine speech recognition python 2026"
>   Source: official GitHub repo (Rank 3)
>   Cross-check: PyPI page (Rank 2) — description matches speech, not imagery
> ```

---

### Example 2 — overstated system dependency

> Task: "Add text-to-speech using Kokoro, English only."

❌ **Bad (copies the quickstart uncritically):**
> "You'll need to install the `espeak-ng` system binary and add it to PATH before this will work, per the official quickstart."
> *(The quickstart installs it unconditionally because the demo covers many languages. For English-only usage the library has a pure-Python fallback path that doesn't need it.)*

✅ **Good (reads past the first line):**
> Checks the library's own docs/issues for what the dependency actually gates, finds it's conditional (only for certain languages / out-of-dictionary fallback), and logs:
> ```
> Fact-checked: "requires espeak-ng" claim
>   Searched: "kokoro tts espeak-ng required English"
>   Source 1: official repo README (Rank 3) — shows espeak-ng in quickstart
>     but doesn't say it's required for all languages
>   Source 2: GitHub issue #127 (Rank 4) — user confirms English works
>     without espeak-ng on Windows, with graceful degradation on OOD words
>   Finding: espeak-ng is conditional, not universal. Required for non-Latin
>     languages and OOD pronunciation quality. Not needed for English-only
>     base case.
> ```

---

### Example 3 — silent version ceiling

> Task: "Set up the project on the user's machine, which has Python 3.14."

❌ **Bad (assumes newer is safer):**
> "Python 3.14 should work fine, it's just a newer version."

✅ **Good (checks the actual support matrix):**
> Looks up the library's current supported-version range before assuming compatibility, finds it caps out lower than 3.14 because a core dependency hasn't published wheels yet.
> ```
> Fact-checked: "Python 3.14 compatible?"
>   Searched: "[library name] python 3.14 support"
>   Source: PyPI page (Rank 2) — classifiers show Python 3.9–3.13
>   Source: GitHub CI matrix (Rank 3) — tests only run up to 3.13
>   Source: GitHub issue #892 (Rank 4) — blocked on numpy not yet
>     publishing 3.14 wheels
>   Finding: NOT supported on 3.14. Flagging to user as blocker BEFORE
>     writing setup code that will fail.
> ```

---

### Example 4 — stale tutorial outranking official docs

> Task: "How do I configure exclusive-mode audio on Windows with this audio library?"

❌ **Bad (takes the first search result):**
> Cites a two-year-old blog post's exact code sample as current, without checking if the API has changed since.

✅ **Good (checks freshness and authority):**
> Pulls the official docs/changelog first, and only leans on the blog post for the surrounding "why," noting if the blog's code differs from current official syntax:
> ```
> Fact-checked: exclusive-mode audio API
>   Searched: "[library] exclusive mode audio Windows 2026"
>   Source 1: blog post from 2024 (Rank 9 — old, undated tutorial)
>     — shows: `audio.set_mode("exclusive")`
>   Source 2: official docs, last updated 2026 (Rank 1)
>     — shows: `audio.configure(mode=AudioMode.EXCLUSIVE)`
>   Finding: API changed between versions. The blog's `set_mode()` was
>     renamed to `configure()` with an enum parameter in v3.0.
>     Blog's code will throw AttributeError on current version.
> ```

---

### Example 5 — decision log with proper sourcing (contrast)

❌ **Bad log entry:**
```
- Decided: use library X for TTS
- It requires system dependency Y
```

✅ **Good log entry:**
```
- Decided: use library X for TTS over alternative Z — X has a pure-Python
  path for our English-only case, Z always shells out to a system binary
- Fact-checked: system dependency Y is conditional, not universal, per
  official repo docs + a corroborating GitHub issue (both checked this
  session) — not needed for our use case
- Caveat: freshest official confirmation found was from [timeframe];
  flagging in case behavior has shifted since
```

---

### Example 6 — Step 4 critique catching a design flaw

> Task: "Add WebSocket support to the API server."

**First draft:** "Add a WebSocket endpoint at `/ws` using the `websockets` library. Messages are broadcast to all connected clients."

**Step 4 critique:**

> **Lens 1 (Breakage):** what happens when a client disconnects ungracefully (network drop, not clean close)? The broadcast loop will throw a `ConnectionClosed` exception. If not caught per-client, one dead client kills the broadcast to all clients. **Fix needed:** wrap each send in try/except per client, remove dead clients.

> **Lens 2 (Assumptions):** assumed `websockets` integrates cleanly with our existing Flask server. **Checked:** `websockets` is a standalone async library — it doesn't plug into Flask. Would need to run a separate process or use `flask-sock` instead. **Design change needed.**

> **Lens 3 (Simplicity):** broadcast to *all* clients is the simplest model but not what's needed — the feature spec says "per-room updates." Broadcast would leak data between rooms. **Design change needed.**

> **Lens 4 (Security):** WebSocket upgrade has no auth check in this draft. Any client can connect without a token. **Fix needed:** validate JWT during the upgrade handshake.

> **Result:** the critique caught four issues — one of which (Flask incompatibility) would have required a complete rewrite after implementation. This is why Step 4 exists.

---

### Example 7 — Step 8 execution revealing a wrong assumption

> Code: uses `os.path.expanduser("~/.config/myapp")` to find the config directory.

**Ran on Windows:**
> The path resolved to `C:\Users\username/.config/myapp` — mixing Windows and Unix separators. The directory wasn't found. Python's `expanduser` works cross-platform for the `~` part, but the hardcoded `/.config/` path is Unix-specific.

**This is a wrong assumption, not a bug.** The design assumed Unix-style config paths work on Windows. Going back to Step 2:
> ```
> Fact-checked: cross-platform config directory
>   Searched: "python cross platform config directory"
>   Source: Python docs for platformdirs (Rank 1) — recommended approach
>     is `platformdirs.user_config_dir("myapp")`
>   Source: Python docs for os.path (Rank 1) — expanduser handles ~ but
>     not the rest of the path structure
>   Finding: on Windows, config should be in %APPDATA%\myapp, not ~/.config/myapp
>   Updated design: use platformdirs library for cross-platform config paths
> ```

---

## Common failure patterns — what goes wrong when these steps are skipped

### The "works in my head" failure (skipped Step 8)
> **Symptom:** code looks correct, passes code review, but fails on first execution with a basic error (wrong import, missing parameter, type mismatch).
> **Root cause:** the code was reviewed by reading, not by running. Reading code activates pattern-matching ("this looks like correct code"), not execution ("this will actually produce the right output").
> **Prevention:** Step 8, unconditionally.

### The "verified the wrong thing" failure (Step 2 done superficially)
> **Symptom:** the package exists and installs, but its API is different from what the code uses. `import foo; foo.bar()` throws `AttributeError: module 'foo' has no attribute 'bar'`.
> **Root cause:** Step 2 verified the package *name* but not the package *API*. The install command was correct, but the function calls were from a different version or a different package with a similar name.
> **Prevention:** Step 2 Rule 6 — verify the package name against the API you're writing against, not just against the registry.

### The "security-pass-after-ship" failure (skipped Step 9)
> **Symptom:** a security vulnerability is discovered after the code is deployed — hardcoded credential, SQL injection, path traversal.
> **Root cause:** the security pass was skipped because the change "seemed small" or "didn't touch security-sensitive code." But security vulnerabilities are often one line of code in a thousand — they don't announce themselves.
> **Prevention:** Step 9, unconditionally for anything touching user input, auth, files, subprocess, network, or serialization.

### The "silent assumption" failure (skipped Step 5)
> **Symptom:** the code works in the developer's environment but fails in production because of an unstated assumption (a specific OS, a specific Python version, a specific environment variable that happens to be set locally).
> **Root cause:** the assumption was never logged, so it was never reviewed, so it was never caught.
> **Prevention:** Step 5 — if it's not in the decision log, it's not a stated assumption, it's a hidden one. Hidden assumptions are the ones that break things.

---

## Non-negotiables

- Never mark a task complete without having executed it (Step 8).
- Never skip the security pass (Step 9) for anything touching user input, auth, files, subprocess, network, or serialization — regardless of how small the change looks.
- Never state a package name, install command, system dependency, or API behavior as fact without having checked a live source in this session — say explicitly if you're relying on memory instead, and treat that as a flagged risk, not a settled answer.
- Never accept a single unofficial source for a load-bearing architectural claim — cross-check, or flag that you couldn't.
- If asked to do something that conflicts with these rules for a "quick" task, flag the conflict rather than quietly dropping the rule.
- Never hedge instead of verifying — "I believe X" is not safer than "X" if both are unverified. Either check it or mark it as `Assumed, not verified:` in the decision log.
- Never write a decision log entry that states a fact without a `Fact-checked:` or `Verified via docs:` line — either add the check or explicitly mark it as an unverified assumption.
- Never patch around a failed assumption at Step 8 without going back to Step 2 to understand *why* the assumption was wrong — the fix should address the root cause, not the symptom.
- Never present the security pass findings without fixing them — a documented vulnerability is still a vulnerability.