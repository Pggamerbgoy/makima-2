---
name: master-workflow
description: End-to-end master execution sequence integrating fact-checking, reasoning discipline, module design, security checks, and decision logging into one unified engineering pipeline. Make sure to use this skill whenever leading full-lifecycle software execution or orchestrating multi-step technical tasks.
---

<!--
  HOW TO USE THIS FILE (pg ke liye, agent ke liye nahi):

  1. Already correctly placed at `.agents/skills/master-workflow/SKILL.md` —
     matches Google Antigravity's official skill path. No manual toggle
     needed (Cline's old model) — `.agents/rules/reasoning.md` (Always-On
     Rule) explicitly routes here FIRST whenever more than one skill
     applies, per its Step 3 priority ordering.
  2. Why this file exists: rigorous-code-development, problem-reasoning,
     and module-design each have their own internally numbered steps
     ("Step 2", "Section 4", "Path B Step 3"...). Read together, it's not
     immediately obvious what order things actually fire in for one real
     request, or which file's "step 2" is active at a given moment. This
     file resolves that with a single linear master sequence, a legend,
     and one merged decision-log format — so the agent (and pg reading the
     log) isn't juggling three parallel checklists that were never
     designed to be read side by side.
  3. This file adds no new rules of its own. It only orders and merges
     rigorous-code-development, problem-reasoning, and module-design. If
     it ever conflicts with one of them on a point of substance, the more
     specific file wins, not this one.
  4. v2 changelog: added the visual flowchart, explicit gate definitions
     (when 00's fact-check and 01's reasoning discipline fire as
     cross-cutting concerns), Tier 1 fast-path detail, loop mechanics,
     handoff semantics, conflict resolution, a full worked example, and
     unified non-negotiables synthesizing all three source files.
  5. v3 changelog: migrated framing from Cline to Google Antigravity/Gemini.
-->

# Master Workflow — How 00, 01, and 02 Fire Together

## Legend — what each file actually governs

| File | Governs | Fires when |
|---|---|---|
| `00-rigorous-code-development.md` | **WHAT to verify** — facts, package names, APIs, security | Any claim that will shape code or architecture |
| `01-problem-and-reasoning-discipline.md` | **HOW to reason** about evidence once you have it | Any conclusion drawn from research, a correction, a disputed claim |
| `02-module-design-and-research-protocol.md` | **WHAT ORDER**, specifically for module/feature-level work | New module/feature, or "improve/fix X" at Tier 2/3 |
| `03-master-workflow.md` (this file) | **The glue** — one sequence, one merged log | Every request — read this first |

---

## Skill composability — one task, multiple skills

This file (and 00/01/02) are not the only skills in `.agents/skills/`. A single task routinely needs more than one skill active at once — this master sequence does not claim exclusivity over the task, it only governs *how 00/01/02 specifically interleave*. In practice:

- **Format/output skills** (docx, pdf, pptx, and similar) can run alongside this pipeline without conflict — they govern *output-format mechanics*, this file governs *engineering process*. Both apply: research/reason/critique per this sequence first, then hand the finished content to the format skill's own rules to produce the deliverable.
- **codebase-analysis** and **quick-rules** are not alternate pipelines — they feed *into* MS-2/MS-3 (understanding what already exists) and MS-1 (fast heuristics for an obvious tier call), not replacements for the master sequence once a task is Tier 2/3.
- **rigorous-code-development** (the general-purpose version of 00) and this project's own 00/01/02/03 set should not both fire in full for the same task — if both happen to be loaded, the project-specific files (00–03) take precedence per Conflict Resolution below; rigorous-code-development's checklist is treated as a subset already covered by MS-2 through MS-9.
- **Log which skills are active.** Whenever more than one skill fires on a task, add a `Skills active:` line to the decision log header naming all of them — same principle as the `[tag]` system this file already uses for 00/01/02, so it's traceable which rules produced which part of the output.
- **Never silently pick one skill and drop another that's also relevant.** If a task plausibly needs two loaded skills at once (e.g. building a module *and* writing up a Word-doc spec for it), say so explicitly and run both, sequenced by actual dependency — design/code first, documentation-of-it after, typically, not the other way round.

---

## Visual flowchart — how one request moves through the system

```
                          ┌──────────────────────┐
                          │  INCOMING REQUEST     │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                    ┌─────┤  MS-1: TRIAGE [02§0] ├─────┐
                    │     └──────────┬───────────┘     │
                    │                │                  │
               Tier 1            Tier 2             Tier 3
                    │                │                  │
                    │         ┌──────▼───────┐         │
                    │         │  MS-2: UNDER-│         │
                    │         │  STAND [00§1]│         │
                    │         └──────┬───────┘         │
                    │                │                  │
                    │     ┌──────────▼───────────┐     │
                    │     │  MS-3: PATH A or B   │     │
                    │     │  [02 Path A§1 /      │     │
                    │     │   02 Path B§1]       │     │
                    │     └──────────┬───────────┘     │
                    │                │                  │
                    │     ┌──────────▼───────────┐     │
                    │     │  MS-4: CASE LIST     │     │
                    │     │  [02 Path A§2–5 /    │     │
                    │     │   02 Path B§2]       │     │
                    │     └──────────┬───────────┘     │
                    │                │                  │
                    │     ┌──────────▼───────────┐     │
                    │     │  MS-5 ←→ MS-6: THE   │◄──── GATE PAIR
                    │     │  RESEARCH + REASON   │      (see below)
                    │     │  LOOP [00§2 + 01§1-8]│
                    │     │  (repeats per claim)  │
                    │     └──────────┬───────────┘
                    │                │
                    │     ┌──────────▼───────────┐
                    │     │  MS-7: EFFICIENCY     │
                    │     │  [02 Path B§4]        │
                    │     └──────────┬───────────┘
                    │                │
          ┌─────────▼────────────────▼───────────┐
          │  MS-8: DRAFT DESIGN [00§3]           │
          │  (Tier 1 enters here — lightweight)  │
          └──────────────────┬───────────────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-9: MERGED        │
                  │  CRITIQUE [00§4 +    │
                  │  02 Path B§5]        │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-10: DECISION LOG │
                  │  [merged format]     │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-11: LAST LIVE    │
                  │  CHECK [00§6]        │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-12: WRITE CODE   │
                  │  [00§7]              │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-13: RUN IT       │
                  │  [00§8]              │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-14: SECURITY     │
                  │  PASS [00§9]         │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  MS-15: CLOSE THE    │
                  │  LOOP [00§10]        │
                  └──────────────────────┘
```

**Key:** `MS-N` = Master Step N. Bracketed tags (e.g. `[00§3]`) refer to the originating file and section. Tier 1 enters at MS-8 directly — the full column from MS-2 through MS-7 only applies to Tier 2/3 work.

---

## The three gates — cross-cutting concerns that fire *inside* other steps

Gates are not sequential steps you do once. They are **interrupt-level checks** that activate whenever their trigger condition is met, regardless of which master step you're currently in.

### Gate F — the fact-check gate [from 00]

**Trigger:** you are about to write, state, or rely on a claim about a package name, install command, system dependency, API signature, version constraint, OS behavior, or any factual assertion that will shape code or architecture.

**What fires:**
1. Search live — don't guess a URL or reconstruct from memory.
2. Prefer 00's source hierarchy: official docs > official repo > recent third-party > old/undated.
3. Cross-check the claim against at least two independent sources.
4. Read past the first line — conditional requirements are not universal requirements.
5. Check dates — stale sources can outrank fresh ones in search results.
6. Verify exact package names against an official source, not by pattern-matching.
7. Verify version constraints explicitly.

**When it fires during the master sequence:** primarily inside MS-5 (the research loop), but also at MS-11 (last live check before coding), and anywhere else a factual claim surfaces — including mid-coding at MS-12 if you realize you're relying on something unchecked.

**Output:** a `Fact-checked:` line in the decision log with the exact query, source, and what it confirmed or denied.

### Gate R — the reasoning discipline gate [from 01]

**Trigger:** you are about to draw a conclusion from evidence, correct a prior claim, compare competing explanations, or present a recommendation.

**What fires:**
1. Decompose (01§1): is this actually one question or several bundled together?
2. Multiple hypotheses (01§2): have you named at least one alternative you considered?
3. Overcorrection check (01§3): if correcting something, is the correction independently verified?
4. Evidence-tier tagging (01§4): which tier (1-6) does this rest on, and does your wording match?
5. Reasoning-trap scan (01§5): false binary? extrapolation across implementations? absence-of-evidence?
6. Confidence tag (01§6): high / medium / low — and does your tone match?
7. Devil's-advocate pass (01§7): what's the strongest argument you're still wrong?
8. Pre-conclusion checklist (01§8): the literal 7-checkbox gate — run it, don't nod at it.

**When it fires during the master sequence:** primarily inside MS-6 (reason about what research found), but also at MS-9 (the merged critique pass), and whenever you're about to state a conclusion anywhere — including while drafting the design at MS-8 if you realize a design choice rests on a reasoning chain you haven't validated.

**Output:** evidence-tier tags and confidence levels on every non-trivial conclusion in the decision log.

### How the two gates interact

They are sequential, not parallel: **Gate F produces evidence, Gate R evaluates it.** For any single claim during the research loop (MS-5 → MS-6):

```
  Claim surfaces → Gate F activates (search, verify, cross-check)
                 → findings in hand
                 → Gate R activates (decompose, hypothesize, check for traps,
                   tag evidence tier, tag confidence, devil's-advocate)
                 → conclusion written with proper tier/confidence wording
                 → logged as a Fact-checked + Evidence-tier pair in the decision log
                 → next claim
```

If Gate R surfaces doubt about Gate F's findings (e.g. the two sources disagree, or the evidence tier is too low for the weight being placed on the claim), loop back into Gate F for additional verification before proceeding.

**Exit criteria for the loop — when to stop searching and escalate instead of looping forever:**
The Gate F ↔ Gate R loop is not allowed to run indefinitely on a single claim. Stop and escalate when any of these hit:
- **Two search attempts with materially different query framings both fail to find a live, current source** for the claim.
- **Sources found genuinely conflict** (not just differ in emphasis) and a third independent source doesn't resolve it.
- **The claim is about something inherently unverifiable in this environment** (no network access to the relevant service, a private/internal API with no public docs, behavior that depends on the user's specific local setup).

When any of these hit, do **not** keep looping. Instead:
1. Log the claim as an **unverified assumption** in the decision log (see the `UNVERIFIED ASSUMPTIONS` field), stating exactly what was tried and why it didn't resolve.
2. If the claim is load-bearing for an architectural decision (would change which approach gets picked, not just a minor detail) — **stop and ask pg directly** rather than silently picking a side and proceeding. State the conflicting findings or the gap, and let him decide or provide the missing context.
3. If the claim is low-stakes (doesn't change the approach either way), proceed with the best-supported option, tagged at the appropriate (low) confidence level per Gate R, and move on — don't block the whole task over a minor unresolvable detail.

### Gate T — the intent-drift / re-triage gate [from this file]

**Trigger:** at any point *after* MS-1, the actual shape of the task turns out to be different from what was triaged — mid-conversation the ask grows ("fix this function" → "actually redesign this whole module"), shrinks, reveals a new file/module in scope, reveals a new external dependency, or the user's follow-up implies a different consumer/purpose than MS-3 assumed. This includes drift discovered by Claude itself mid-work, not just drift stated explicitly by the user.

**What fires:**
1. Stop at the current master step — do not keep executing against the old triage.
2. State plainly what changed and why it matters: "this started as Tier 1 but now touches N files / introduces a new dependency — re-triaging."
3. Re-run MS-1 (Triage) against the *current* understanding of the task, not the original one.
4. If the new tier is higher than the old one, go back and pick up MS-2 onward for whatever ground wasn't covered under the lighter tier — don't assume earlier lightweight work already satisfies the heavier tier's requirements.
5. If the new tier is lower (task turned out simpler than assumed), it's fine to collapse forward to MS-8 — but say so, the same way a Tier 1 call must be stated, not silently assumed.
6. Re-check whether the *set of active skills* from the "Skill composability" section above still matches the new shape of the task (e.g. a pure-code fix that grew into "also write the user a spec doc" now needs docx alongside 00–03).
7. **MS-9 (merged critique) must be re-run against the new scope if the tier increased.** A critique pass done against the old, smaller scope does not cover the new surface area — this is not optional just because a critique already happened once on this task.

**Output:** a `Re-triaged:` line in the decision log — old tier, new tier, what changed, and which master steps were re-run as a result. Do not just quietly update the tier number with no trail.

**When it fires during the master sequence:** anywhere, the moment drift is noticed — most commonly right after MS-2/MS-3 (understanding surfaces a bigger scope than the initial ask suggested) or mid-MS-12 (writing code reveals the real fix is elsewhere/bigger). Gate T can also re-fire more than once on a single task if intent keeps shifting — each re-trigger gets its own `Re-triaged:` log line.

---

## The single master sequence — detailed

For any request, this is the actual chronological order — the bracketed tag shows which file's rule is firing at that point, so nothing here is a new rule, just an ordering of the existing three.

### MS-1: TRIAGE [02 · Step 0]

Classify the request as Tier 1, 2, or 3. **State the tier and the reason** — this classification itself goes in the decision log. Silent scoping-down is not the same as a stated Tier 1 decision.

- **Tier 1 — trivial, isolated:** single function/small edit, no new dependency, no new architectural surface, blast radius ≤ one file. → **Skip to MS-8.** Say: "Tier 1 — skipping the module protocol, this is an isolated edit." Files 00 and 01 still apply (fact-check gate and reasoning gate are always on), but 02's full pipeline does not.
- **Tier 2 — small addition inside an existing pattern:** one more case in a router, a utility class, extending something already designed. → **Lightweight pass through MS-2 through MS-7:** Section 1 (Why) in one line, case list proportional to scope, Section 3/4 research and efficiency only as deep as any *new* technology introduced.
- **Tier 3 — new module, new subsystem, multi-file, new external dependency, or high-stakes:** → **Full pipeline, all steps, no shortcuts.** Default assumption for anything called a "module" or "feature."

**Concrete anchors — when file-count/size alone doesn't settle it:**
A change is **high-stakes regardless of file count or line count** (i.e. round up to Tier 3) if it touches any of:
- Auth, session/token handling, or anything crypto-adjacent
- Data deletion, data migration, or anything that mutates persisted state irreversibly
- The FFI boundary between Rust core and Python (PyO3 bindings) — mismatches here fail silently across the language boundary
- The 700-line Makima system prompt or persona logic — behavioral regressions here are hard to test mechanically
- Anything that changes a provider-routing decision in `ai_handler.py` (billing risk, per the Cline Plan/Act incident)
- Any new external network call or third-party API integration

Conversely, a multi-file change is still legitimately **Tier 1** if every file's edit is the same mechanical change with no new decision per file (e.g. renaming an import across 5 files).

**When genuinely unsure:** round up, not down. Over-applying rigor to Tier 2 costs minutes; under-applying it to Tier 3 costs a wrong-purpose module discovered after it's built.

### MS-2: UNDERSTAND THE PROBLEM [00 · Step 1]

*Tier 2/3 only. Tier 1 skipped to MS-8.*

Before touching anything: what inputs does this need to handle, what are the failure modes, what does "done" mean, what's implicit but unstated (e.g. "a bot that forwards messages" implies rate limits, retries, behavior when target is gone). If genuinely ambiguous in a way that sends you down the wrong path, ask one sharp question. Otherwise state the assumption explicitly and continue.

### MS-3: ENTRY PATH [02 · Path A or B]

*Tier 2/3 only.*

Determine which path before doing anything else:

- **Path A — a file/module is explicitly targeted** ("fix `audio_service.py`", "improve the command router"): understand what already exists, in its own terms, before judging it.
  1. Read the real code, all of it, end-to-end — not a partial read or a function name.
  2. Reconstruct current architecture and data flow in your own words.
  3. Separate observation from judgment — list what it does factually first, then form opinions.
  4. Categorize every problem found: correctness, architecture, performance, error handling, silent failures, security, tech debt.

- **Path B — no file targeted, a new module/feature** ("build a voice module", "add a caching layer"): understand *why* before designing *how*.
  1. What problem does this solve? What breaks without it?
  2. Who/what consumes its output, and what does that consumer need?
  3. What already exists that this must integrate with, replace, or avoid duplicating?
  4. What are the real constraints — *this project's*, not generic best-practice?

**Never** skip straight to "here's how I'd build it" on a Path B ask. A design built before the purpose is understood is a design for the wrong problem.

### MS-4: CASE ENUMERATION [02 · Path A§2–5 / Path B§2]

*Tier 2/3 only.*

Write an explicit checklist — don't let it stay implicit:
- **Happy-path variations** — normal cases and reasonable variants.
- **Edge cases** — empty/null, malformed, boundary sizes, unusual-but-legal combinations.
- **Failure modes** — dependency unavailable, network failure, resource exhaustion, permission denied, hardware absent.
- **Concurrency / timing** — re-entrant calls, interruption mid-operation.
- **Integration cases** — contract this promises to callers, what happens on contract violation.
- **Platform / environment** — OS/hardware/installed-environment variance.

A case list that only covers the happy path is not a case list. Go back and add failure modes explicitly before proceeding.

### MS-5 ↔ MS-6: THE RESEARCH + REASON LOOP [00 · Step 2 + 01 · §1–8]

*Tier 2/3: full loop per case from MS-4. Tier 1: Gate F still fires on any factual claim.*

This is **not** one big research phase followed by one big reasoning phase. It is a tight loop that runs once per claim or case:

```
  FOR each case/claim from MS-4:
    MS-5 [00§2]: RESEARCH
      → search live (Gate F activates)
      → prefer source hierarchy
      → cross-check ≥ 2 sources
      → note at least one candidate approach and its tradeoff
      → log: exact query, sources found, what they confirmed/denied

    MS-6 [01§1-8]: REASON about what MS-5 found
      → Gate R activates:
        → decompose the question — is it actually several questions?
        → hold multiple hypotheses — name alternatives explicitly
        → overcorrection check — if correcting something, verify independently
        → tag the evidence tier (01§4, tiers 1-6)
        → scan for reasoning traps (01§5)
        → tag confidence: high / medium / low
        → devil's-advocate pass
        → run the 7-checkbox pre-conclusion gate (01§8)
      → log: conclusion with tier tag and confidence level

    IF Gate R surfaces doubt about Gate F's findings:
      → loop back to MS-5 for additional verification on that claim
  NEXT case/claim
```

**The critical discipline:** step 6 happens *inside* the research loop, once per claim — not as a separate pass at the end. By the time you leave MS-5/6, every claim has been both fact-checked and reason-checked individually.

### MS-7: EFFICIENCY & PERFORMANCE [02 · Path B§4]

*Tier 2/3 only, for finalist candidates from MS-5/6.*

For each finalist candidate that survived the research+reason loop:
- Reason explicitly about memory footprint, latency, CPU/GPU load, scalability under this project's actual expected load.
- When a more efficient option carries a real cost (less mature, harder setup), state the tradeoff plainly.
- Benchmark claims from vendors/docs are marketing until checked — look for independent benchmarks or be honest the number is unverified.
- If the most efficient option is also the most complex, that's a tradeoff to present — not a reason to silently downgrade.

### MS-8: DRAFT THE DESIGN [00 · Step 3]

*All tiers. Tier 1 enters the sequence here.*

Write out: the approach, the key structural decisions, and what you're explicitly choosing not to do and why. This draft merges:
- Purpose statement (from MS-3)
- Case list (from MS-4)
- Researched + reasoned candidates (from MS-5/6)
- Efficiency reasoning (from MS-7)

into one coherent plan. For anything touching more than one or two files, or requiring an architectural decision, stay in Plan mode — don't switch to Act.

**For Tier 1:** this step is proportionally lighter — a brief statement of approach and key decisions, but the fact-check gate (Gate F) and reasoning gate (Gate R) still apply to any claims made here.

### MS-9: MERGED CRITIQUE [00 · Step 4 + 02 · Path B§5]

*All tiers, proportional to scope.*

**One adversarial pass, not two.** Running the same adversarial energy twice produces diminishing returns and encourages rubber-stamping the second pass. This single pass covers both 00's "how does this break" review AND 02's self-critique:

- **What's the most likely way this breaks?** [00§4]
- **What did I assume without checking — and did I actually go back and check it?** [00§4]
- **Is there a simpler approach with less surface area for bugs?** [00§4]
- **What would a security-minded reviewer flag?** [00§4]
- **Does any claim rest on a single, unverified, or outdated source?** [00§4] → if yes, loop back to MS-5 for that claim.
- **Completeness:** did the case list from MS-4 actually get addressed, or did research quietly narrow back to just the happy path? [02§5]
- **Verification debt:** for every technology/library/pattern, can you point to where you actually checked it live? [02§5]
- **Efficiency debt:** is there a more efficient approach dismissed too quickly? [02§5]
- **Overcorrection check:** if replacing a previous approach, does the replacement rest on equally solid evidence? [02§5 + 01§3]
- **Failure surface:** of everything in the failure-mode list from MS-4, which are handled and which are silently unhandled? Say which is which. [02§5]
- **What would a harsher reviewer say?** Address it or state it as a known limitation. [02§5]

Revise the MS-8 draft based on what this pass surfaces. This second pass is where most of the real thinking happens — do not skip it because the first draft "looks fine."

### MS-10: WRITE THE DECISION LOG [merged format]

*All tiers. This is the single artifact steps 1–9 produce.*

See "The merged decision log" section below. One `Fact-checked:` / evidence-tier pair per load-bearing claim. For a task with five researched claims, expect five such pairs — not one summary line.

**Where the log lives:** post it inline in the chat response, in a fenced block, immediately before MS-11/MS-12 (before switching to Act mode / writing code) — this is what makes the "proof of work" visible to pg in the same turn, not buried in a file he has to go open. For Tier 3 tasks (or any task pg flags as worth keeping), also append it to `memory-bank/decisionLog.md` at the repo root as a durable record, newest entry on top — create that file on first use if it doesn't exist yet. Tier 1 tasks' abbreviated logs stay inline only, no file append needed.

### MS-11: LAST LIVE CHECK [00 · Step 6]

*All tiers.*

Right before switching to Act mode, do one more targeted, live check: exact current syntax, method signatures, and idioms for the specific language/library/version in play. Gate F fires here with extra weight for anything touching auth, cryptography, deserialization, SQL, subprocess/exec calls, file paths built from user input, or network requests.

This matters most when meaningful time has passed since MS-5/6 or the design has shifted since then — the MS-5/6 check may no longer be current for the final design.

### MS-12: WRITE THE CODE [00 · Step 7]

Act mode. Implement the revised design. Keep functions small and testable. Make edits incrementally — each one reviewable and checkpoint-able, not one giant unreviewable diff.

**Gate F remains active:** if mid-implementation you realize you're relying on an unchecked claim (a method signature, a default parameter value, an import path), stop and verify before writing around it.

### MS-13: ACTUALLY RUN IT [00 · Step 8]

Never skip. Run the script, hit the endpoint, execute the test suite, exercise the bot command. Watch the terminal output and fix whatever breaks.

If you cannot execute something in this environment, say so explicitly rather than presenting untested code as verified, and state what you'd want to test if you could.

If execution fails because a fact-checked assumption turned out wrong anyway, treat that as new information: go back to Gate F, re-verify, and update the decision log rather than patching around it silently.

### MS-14: SECURITY PASS [00 · Step 9]

A distinct pass from "does it work." Review specifically for:
- Hardcoded secrets, API keys, tokens, passwords
- Injection points: SQL from string concatenation, shell commands from unsanitized input, template injection
- Unvalidated/unsanitized input reaching file paths, database queries, shell calls
- Unsafe deserialization (`pickle.load`, `yaml.load` without `SafeLoader`, `eval`/`exec`)
- Insecure defaults: disabled TLS, permissive CORS, weak auth, permissive file permissions
- Dependencies with known vulnerabilities — check live, not from training data
- Error messages/logs leaking internals to end users

If `security_scan.py` exists at the repo root, run it. Check its `--help` before assuming arguments.

### MS-15: CLOSE THE LOOP [00 · Step 10]

Before ending the task:
- Update `memory-bank/activeContext.md` (current focus, recent changes, next steps) and `memory-bank/progress.md` (what works, what's left, known issues).
- State plainly what you actually verified (tests run, security checks done, sources checked live) versus what you didn't get to verify (couldn't test against a real API, no live access, source was old/single/unofficial).
- Log any assumptions or residual risk left standing.

---

## The Tier 1 fast path

For Tier 1 tasks (trivial, isolated, blast radius ≤ one file, no new dependency), the full pipeline collapses to:

```
  MS-1:  TRIAGE → state "Tier 1" and why.
  MS-8:  DRAFT (lightweight — one line of approach is fine).
         Gate F still fires on any factual claim.
         Gate R still fires on any non-trivial conclusion.
  MS-9:  CRITIQUE (proportional — quick "how does this break" check).
  MS-10: LOG (abbreviated — tier, what was decided, any fact-checked claims).
  MS-11: LAST LIVE CHECK (still mandatory for auth/crypto/subprocess/SQL/network).
  MS-12: WRITE CODE.
  MS-13: RUN IT.
  MS-14: SECURITY PASS (mandatory if it touches user input, auth, files,
         subprocess, network, or serialization — even for Tier 1).
  MS-15: CLOSE THE LOOP.
```

**Tier 1 is a legitimate fast exit, not a license to skip thinking.** The gates still fire; the heavy planning steps (MS-2 through MS-7) are what's scoped out.

---

## The merged decision log

Instead of three separate log formats running in parallel, one request produces one log. Every field is tagged with its originating file so the reader knows which protocol generated it:

```
──────────────────────────────────────────────────────────────────
DECISION LOG — [task description / request summary]
Date: [YYYY-MM-DD]   Tier: [1/2/3] — [reason]                (02)
Skills active: [list every skill firing on this task, e.g.
  00/01/02/03 + docx, or 00/01/02/03 only]              (composability)
Re-triaged: [none] OR [old tier → new tier; what changed;
  which master steps were re-run] — repeat this line
  per re-trigger if intent shifted more than once            (Gate T)
──────────────────────────────────────────────────────────────────

PURPOSE
  [why this module/change exists, what breaks without it]      (02)

CASES IDENTIFIED                                                (02)
  Happy-path:   [list]
  Edge:         [list]
  Failure:      [list]
  Integration:  [list]
  Platform:     [list]
  Concurrency:  [list, or "N/A — single-threaded context"]

RESEARCH — per claim                                          (00+01)
  Claim 1: [the factual claim]
    Fact-checked: searched "[exact query]";
      [source 1] confirms [specific finding]                    (00)
      [source 2] confirms/contradicts [specific finding]        (00)
    Evidence tier: [1–6 per 01§4]                               (01)
    Worded as: "[confirmed / per docs / design intent is /
      one source claims / working hypothesis]"                  (01)
    Confidence: [high / medium / low] — [reason]                (01)

  Claim 2: [...]
    [same structure]

  [...repeat per load-bearing claim]

UNVERIFIED ASSUMPTIONS                                          (00)
  - [claim] — [why not checked, flagged for user to confirm]
  - [claim] — [relied on memory because: stable/low-stakes,
    stated explicitly per 00's exception clause]

DECISIONS                                                       (00)
  - Decided: using X over Y because Z
  - Rejected: approach Z — breaks when [edge case]
  - Open question: [anything still unresolved]

EFFICIENCY REASONING                                            (02)
  [memory/latency/CPU tradeoff for finalist candidates,
   why chosen option wins for this project's actual constraints]

SELF-CRITIQUE FINDINGS                                        (02+00)
  - [what the merged adversarial pass (MS-9) caught]
  - Fixed: [what was revised before finalizing]
  - Accepted limitation: [what was surfaced but not addressed, and why]

RESIDUAL UNCERTAINTY                                            (01)
  - [anything the devil's-advocate pass couldn't resolve]
  - [any conclusion resting on tier 4+ evidence that drives architecture]

VERIFICATION STATUS                                             (00)
  Verified:     [tests run, security checks done, sources checked live]
  Not verified: [couldn't test against real API, no live access, etc.]
  Residual risk: [assumptions still standing, single-source claims]
──────────────────────────────────────────────────────────────────
```

**One `Fact-checked:` / evidence-tier pair per load-bearing claim** — for a task with five researched claims, expect five such pairs in the log, not one summary line. An entry that states a fact without a `Fact-checked:` or `Verified via docs:` line is incomplete — either add the check or mark it as an unverified assumption.

---

## Handoff points — where control transfers between files

These are the explicit handoff boundaries in the master sequence. At each boundary, the outgoing file's obligations must be complete before the incoming file's step begins:

| From | To | Handoff condition |
|---|---|---|
| MS-1 (02) | MS-2 (00) | Tier stated and logged. If Tier 1, skip to MS-8. |
| MS-4 (02) | MS-5/6 (00+01) | Case list complete — happy path, edge, failure, integration, platform all represented. |
| MS-6 (01) | MS-7 (02) | Every claim from MS-4 has a Fact-checked + Evidence-tier pair. No claim left un-reasoned. |
| MS-7 (02) | MS-8 (00) | Efficiency reasoning complete for all finalist candidates. |
| MS-9 (00+02) | MS-10 (merged) | Merged critique pass done — completeness, verification debt, efficiency debt, overcorrection, failure surface all addressed. Draft revised if needed. |
| MS-10 (merged) | MS-11 (00) | Decision log written — all fields populated. This is the "proof of work" for steps 1–9. |
| MS-14 (00) | MS-15 (00) | Security pass complete — all categories checked, no unresolved flags. |
| Gate T (any step) | MS-1 (re-entry) | `Re-triaged:` logged with old tier, new tier, what changed. Re-triage does not "reset" — steps already validly completed under the old tier don't need redoing, only the gap the new tier opens up. |

**If a handoff condition isn't met:** don't proceed — go back to the step that's incomplete. The handoff conditions exist to prevent "I'll do that later" from turning into "I silently skipped that."

---

## Conflict resolution — when files disagree

This file is glue, not authority. If this master sequence ever conflicts with one of the three source files on a point of substance:

1. **The more specific file wins**, not this one. 00 is authoritative on fact-checking mechanics, 01 on reasoning discipline mechanics, 02 on module-level protocol mechanics.
2. **If two source files conflict with each other** (unlikely by design, since they govern different concerns): flag the conflict explicitly in the decision log, state which file's rule you're following and why, and note it as something for pg to resolve in a future revision.
3. **This file's ordering is authoritative** — the *sequence* in which steps execute is what this file governs, and the source files defer to it on that question.

---

## Worked example — a full request through the master sequence

> Request: "Build a caching layer for the triple store's most-used queries."

### MS-1: TRIAGE
Tier 3 — new subsystem, introduces an architectural decision (cache invalidation strategy), multi-file (cache module + integration into triple store), performance-critical. Full pipeline.

### MS-2: UNDERSTAND
The triple store handles entity-relationship queries for the assistant's knowledge graph. "Most-used queries" implies frequency tracking doesn't exist yet — need to either build it or define "most-used" statically. "Done" means measurable latency reduction on repeated queries without stale data causing incorrect behavior.

### MS-3: PATH B (no file targeted — new module)
*Why:* repeated identical queries to the triple store during a single conversation turn are redundant — the knowledge graph doesn't change mid-turn. Without caching, the same SPARQL-like traversal runs N times. The consumer is `code_agent.py` and other agents that query the triple store via `tool_registry.py`.
*Constraints:* the existing triple store is in Rust (`triple_store.rs`), agents are in Python — cache must live at the Python boundary or cross the FFI. RAM is constrained (desktop assistant).

### MS-4: CASES
- Happy path: cache hit on repeated query within same turn.
- Edge: cache miss (first query, or query not in top-N).
- Edge: very large result set — does caching it blow the RAM budget?
- Failure: cache returns stale data after a write to the triple store.
- Failure: cache grows unbounded over a long session.
- Integration: what contract does the cache promise to callers? Transparent (same API) or explicit (caller opts in)?
- Concurrency: two agents query simultaneously — does the cache handle concurrent reads?
- Platform: N/A — all in-process, no OS-specific behavior.

### MS-5/6: RESEARCH + REASON LOOP (one claim shown)
**Claim: "Python's `functools.lru_cache` is sufficient for this."**
- *Gate F:* searched "python lru_cache thread safety 2026"; official docs confirm it is thread-safe for reads but the underlying function must be hashable-argument-only. Cross-checked with a recent CPython issue confirming no regression.
- *Gate R:* evidence tier 2 (official docs, not run myself yet). Wording: "per official docs, lru_cache is thread-safe for concurrent reads." But — decomposing the question: thread-safe reads ≠ cache-invalidation-safe. If the triple store is written to, lru_cache has no invalidation hook. This is a spectrum, not a binary: lru_cache works for the read path but not the invalidation requirement. Alternative hypothesis: a custom cache with explicit invalidation on write. Confidence: medium — the read path claim is solid, the invalidation gap is a design limitation, not a bug.

*(…repeat for each claim: invalidation strategy, max cache size, eviction policy, FFI boundary placement…)*

### MS-7: EFFICIENCY
- `lru_cache` with `maxsize=256`: ~negligible memory overhead for typical query result sizes (entity dicts, not blobs). Latency: eliminates redundant FFI crossings (~0.5ms each). Tradeoff: custom invalidation wrapper adds ~20 lines of complexity vs. a third-party caching library that handles invalidation natively but adds a dependency.
- Decision: custom wrapper — the simplicity of no new dependency wins given the narrow scope (same-turn caching only, not persistent).

### MS-8: DRAFT
A `QueryCache` class wrapping `lru_cache` with an explicit `invalidate()` method called by the triple store's write path. Lives in `apps/brain/query_cache.py`. Integrated into `tool_registry.py`'s triple-store query functions. `maxsize=256`, scoped to conversation turn (cleared on turn boundary).

### MS-9: MERGED CRITIQUE
- *How does this break?* If `invalidate()` isn't called on every write path, stale data serves silently. Mitigation: audit every write entry point in `triple_store.rs` FFI.
- *Completeness:* the "unbounded growth" failure case from MS-4 is handled by `maxsize`. The "large result set" edge case — checking: if a single cached value is a list of 10K entities, 256 of those is ~significant. Added: a per-entry size cap check.
- *Overcorrection:* not correcting a prior approach, so N/A.
- *Verification debt:* `lru_cache` thread safety checked per docs, not run yet — will confirm at MS-13.
- *Harshest reviewer:* "Why not cache at the Rust layer where it's faster?" — because the Python agents are the callers and the FFI crossing is the bottleneck, not the Rust query itself. Stated as a design rationale, not silently dismissed.

### MS-10: DECISION LOG
*(Filled using the merged format above — all fields populated.)*

### MS-11–15: Implementation, execution, security, close
*(Proceed through standard 00 steps — write, run, security-check, log.)*

---

## Worked example — Tier 1 fast path

> Request: "The retry count in `telegram_bot.py`'s message-send loop is hardcoded to 3, make it configurable via an env var."

### MS-1: TRIAGE
Tier 1 — single value, one file, no new dependency, no architectural surface. "Tier 1 — skipping the module protocol, isolated edit to one constant's source."

### MS-8: DRAFT (lightweight)
Read `os.getenv("TELEGRAM_RETRY_COUNT", "3")`, cast to int, fall back to 3 if unset or malformed. Gate F fires on one claim: confirmed `os.getenv` signature and default-value behavior against Python's own docs (stable stdlib API, low-stakes — quick check, not a deep research loop).

### MS-9: CRITIQUE (proportional)
How does this break? Malformed env var (non-numeric string) — wrap the cast in try/except, fall back to default rather than crashing the bot on startup. That's the one real failure mode for a change this size.

### MS-10: LOG (abbreviated)
```
Tier: 1 — isolated single-constant edit, one file
Skills active: 00/01/03 only (02's full pipeline not invoked)
Fact-checked: os.getenv default-value behavior — Python docs, confirmed
Decided: try/except around int() cast, falls back to 3 on malformed input
```

### MS-11–15
Last live check confirms no version-specific gotcha. Code written, run locally with a bad env var to confirm the fallback path, no security-relevant surface (no user input, no network, no auth) so MS-14 is a quick "N/A, confirmed no relevant surface" rather than a full pass.

---

## Worked example — Gate T firing mid-task

> Request: "Fix the bug where `ai_handler.py` retries a failed Groq call against the same provider instead of falling back."
> *(Partway through MS-3, reading the file reveals the retry logic is tangled with the same function that does per-provider latency tracking and the 429 backoff — fixing the fallback correctly means restructuring how providers are selected, not a one-line fix.)*

### MS-1 (original): TRIAGE
Tier 1 — "looked like a one-line fix: change which provider gets retried."

### Gate T fires (during MS-3, Path A)
"This started as Tier 1 but the fallback logic is entangled with latency tracking and 429 backoff in the same function — separating them safely touches provider-selection behavior broadly, not one line. Re-triaging to Tier 2."
- Re-run MS-1: Tier 2 — small-to-medium restructuring inside an existing pattern (provider routing), no new external dependency, blast radius is one file but multiple responsibilities inside it.
- Pick up MS-2 onward (skipped under the old Tier 1 call): understand the three entangled responsibilities, case-list the failure modes (Groq down, all providers down, malformed latency data), then MS-8/MS-9 against the *real* scope.
- Skills active unchanged — still 00/01/02/03 only, no new skill triggered by this particular drift.

### MS-10: LOG
```
Tier: 1 → 2 (re-triaged during MS-3)
Re-triaged: Tier 1 → Tier 2; fallback logic entangled with latency
  tracking + 429 backoff in same function; re-ran MS-2 through MS-9
  against full scope; MS-1's original one-line assumption was wrong
[... rest of log per Tier 2 lightweight pass ...]
```

---

## Quick reference — what to do right now

- **Just started a request?** → Go to MS-1 (Triage). State a tier.
- **Mid-research, found a source?** → You're at MS-5; before writing it down, do MS-6 (Gate R) for that specific claim before moving to the next one.
- **About to correct an earlier claim (yours, the user's, or another agent's)?** → MS-6 applies with extra weight — 01§3, the overcorrection trap, is exactly for this moment.
- **Design feels done, about to write code?** → Confirm MS-9 (merged critique) actually happened for this specific design, not a generic memory of having critiqued something similar before. Then MS-11 (last live check) before switching to Act mode.
- **Tempted to skip straight to code because the task "feels small"?** → That's MS-1. Go state a Tier explicitly — Tier 1 is a legitimate fast exit, a silent skip is not.
- **Task turns out bigger/smaller/different than what you triaged at MS-1?** → That's Gate T. Stop, state what changed, re-run MS-1, and pick up whatever master steps the new tier requires. Log it as `Re-triaged:`.
- **A second skill (docx, pdf, pptx, codebase-analysis, etc.) becomes relevant partway through?** → Add it to `Skills active:` in the log immediately, don't wait until the end to notice you used it.
- **Hit an unexpected failure during MS-13 (run it)?** → Don't patch around it. Go back to Gate F, re-verify the assumption that failed, update the decision log, revise the design if needed.
- **Unsure which file's rule applies right now?** → Check the `[tag]` on your current master step. The tag tells you which file is authoritative for the rule you're executing.

---

## Non-negotiables — unified from all three files

These are the non-negotiable rules from 00, 01, and 02, collected in one place. Violating any of these is a protocol failure regardless of time pressure, task size, or confidence level.

### From 00 (fact-checking)
- Never state a package name, install command, system dependency, or API behavior as fact without having checked a live source in this session.
- Never accept a single unofficial source for a load-bearing architectural claim.
- Never mark a task complete without having executed it (MS-13).
- Never skip the security pass (MS-14) for anything touching user input, auth, files, subprocess, network, or serialization.

### From 01 (reasoning)
- Never let disproving one claim stand in as proof of its opposite — verify the replacement independently.
- Never state a conclusion in more confident language than its evidence tier supports.
- Never treat a different implementation, older version, or design-intent statement as equivalent to observed current behavior without saying so.
- Never skip the devil's-advocate pass on a conclusion that drives architecture or corrects another claim.
- Never present a load-bearing conclusion without running the Section 8 checklist in this session.

### From 02 (module protocol)
- Never skip or scope down the protocol without explicitly stating the tier and reason — silent skipping ≠ a stated Tier 1 decision.
- Never begin writing code for a Tier 3 module until MS-1 through MS-9 are complete.
- Never let a case list stay happy-path-only — edge cases and failure modes are mandatory.
- Never treat a technology choice as settled without Gate F applied to it specifically.
- Never present a design without the merged critique pass (MS-9) done on this specific design, in this session.
- Never silently drop a failure mode or edge case — name it as an accepted limitation instead.

### From this file (orchestration)
- Never proceed past a handoff point when the handoff condition isn't met — go back to the incomplete step.
- Never run Gate F and Gate R as separate end-of-pipeline passes — they fire inside the research loop (MS-5/6), per claim, not per pipeline.
- Never let three parallel log formats accumulate — use the single merged decision log format from this file.
- If this file's ordering conflicts with a source file's substance, the source file wins on substance and this file wins on sequence.
- Never keep executing against a stale MS-1 triage once intent has visibly drifted — Gate T fires and gets logged (`Re-triaged:`), it is not optional just because work is already in progress.
- Never use a second relevant skill silently — if more than one skill is actually firing on a task, name all of them in `Skills active:`.
