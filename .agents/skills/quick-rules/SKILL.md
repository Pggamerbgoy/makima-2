---
name: quick-rules
description: Essential coding guidelines, fact-checking gates, reasoning discipline, triage levels, and master workflow sequence for all coding and engineering tasks. Make sure to use this skill whenever embarking on any coding task, refactoring, feature implementation, or system design.
---

<!--
  HOW TO USE THIS FILE (pg ke liye, agent ke liye nahi):

  This project runs on Google Antigravity (Gemini-based agent), not Cline.
  Antigravity has two separate mechanisms, and they are NOT interchangeable:

    - Rules (`.agents/rules/*.md`, `trigger: always_on` in frontmatter) —
      genuinely resident in every turn's context. `.agents/rules/reasoning.md`
      holds that role in this project — it forces a [SKILL CHECK] step and
      routes to whichever skill below actually applies.
    - Skills (`.agents/skills/<name>/SKILL.md`, this file's own format) —
      semantically triggered. Antigravity sees this file's `name` +
      `description` at conversation start and only loads the full body when
      the task matches. This file is a Skill, not a Rule — it is NOT
      structurally guaranteed to be in context on every turn by itself.

  1. This file's actual role now: the fast, condensed reference that
     `.agents/rules/reasoning.md`'s routing table points to when a task
     needs the compact version of the fact-check + reasoning + tier rules
     without pulling in a full deep file. It is not "always on" by its own
     mechanism — reasoning.md is what makes sure it gets checked/loaded.
  2. The four deep files live at `.agents/skills/<name>/SKILL.md`, each its
     own Skill, semantically triggered independently:
       .agents/skills/rigorous-code-development/SKILL.md
       .agents/skills/problem-reasoning/SKILL.md
       .agents/skills/module-design/SKILL.md
       .agents/skills/master-workflow/SKILL.md
     They are not "toggled off" the way Cline's .clinerules popover worked —
     that concept doesn't exist here. They're independently available and
     the agent fetches whichever one a task actually needs.
  3. Yeh file un chaaron ka REPLACEMENT nahi hai — DISTILLATION hai. Har
     rule wahi hai, bas theory/examples/worked-cases hata diye gaye hain.
  4. v2 changelog: converted the case-enumeration list from inline prose
     to a literal checkbox format after a real Tier-3 run (voice module
     Phase 2) dropped the Platform category silently even though it was
     directly relevant (Windows-only audio hardware) — inline bulleted
     lists get skimmed and skipped, checkboxes get filled in. Added an
     explicit "N/A needs a reason" rule so silent omission isn't legal.
     Added a "constraints need real numbers" rule after the same run
     carried a vague "operate efficiently given constraints" placeholder
     forward instead of pulling an actual RAM/latency figure. Added a
     "block only on what's actually blocking" rule after the same run
     fully paused on three clarifying questions instead of researching
     the non-blocking parts of the case list in parallel.
  5. v3 changelog: migrated from Cline (.clinerules, manual popover toggle,
     "always active" file) to Google Antigravity (Rules vs. Skills split,
     semantic triggering, no manual toggle). Fixed stale `deep/NN-name.md`
     pointers to the actual `.agents/skills/<name>/SKILL.md` paths — the old
     pointers referenced a `.clinerules/deep/` folder that was never created
     under the current setup and would have resolved to nothing.
-->

# Quick Rules — Condensed Reference

You are the coding/reasoning agent working in this repository (currently running on Gemini via Google Antigravity). This is the compact, on-demand-fetched version of four deeper protocol files. Follow every rule below on every task, every tier, no exceptions. When a rule below points to a deep file, fetch and read that file before proceeding — don't guess what it says from the pointer.

```
.agents/skills/rigorous-code-development/SKILL.md → full fact-checking mechanics, security pass detail, worked examples
.agents/skills/problem-reasoning/SKILL.md          → full reasoning discipline, evidence tiers, overcorrection deep-dive
.agents/skills/module-design/SKILL.md              → full module design protocol, case enumeration templates
.agents/skills/master-workflow/SKILL.md            → full master sequence, flowchart, merged decision-log spec
```

---

## The prime directive

**A confident-sounding claim is not a verified claim.** Package names, install commands, system dependencies, API signatures, defaults, and "requires X" statements must be checked against a live source in this session — not recalled. If you catch yourself writing "this requires..." or "the correct package is..." without having searched this session, stop and search. Hedging ("I believe X") is not verification — either check it or mark it `Assumed, not verified`.

**A checklist item you silently skip is the same failure as a claim you never checked.** Every checklist in this file must be filled in line by line, in the output, not held implicitly in your head. If a category genuinely doesn't apply, write `[category]: N/A — [one-line reason]`. An absent line is indistinguishable from a forgotten one — to you on review, and to pg reading it.

---

## Step 0 — Triage (every task, every time)

State the tier out loud before doing anything else. Silent scoping-down is not allowed; a stated tier is.

| Tier | Shape | What fires |
|---|---|---|
| **1** | Single function/edit, no new dependency, blast radius = 1 file | Gates F+R only. Say "Tier 1" and proceed straight to writing. |
| **2** | Small addition inside an existing, understood pattern | One-line purpose, proportional case list, short critique. Research only for what's genuinely new. |
| **3** | New module/subsystem, multi-file, new dependency, or high-stakes (perf/security/hard-to-reverse/stated hard constraint) | Full pipeline. **Fetch `deep/02-module.md` and `deep/03-master.md` now.** |

Unsure? Round up, not down.

---

## Gate F — Fact-check (fires on every factual claim, any tier)

Before stating a package name, API behavior, system dependency, version constraint, or default value as fact:

1. Search live — don't guess a URL or trust memory.
2. Rank sources: official docs > official registry/repo > changelog > recent (<12mo) third-party > forum > old/undated.
3. Cross-check load-bearing claims against ≥2 sources.
4. Read past the first line — "install X for feature Y" ≠ "X is required."
5. Check dates — a fresh official page beats an old highly-ranked blog post.
6. Verify exact package names against the registry — near-miss names (`kokoro` vs `kokoro-onnx`) are different projects.
7. Verify version support explicitly — don't assume "recent" means "supported."

**Exception:** stable, low-stakes, version-independent knowledge (e.g. `dict.get()` semantics) can skip the live check — but say so explicitly.

**Output every time:** `Fact-checked: searched "[query]" → [source] confirms [finding]`

---

## Gate R — Reasoning discipline (fires on every conclusion, any tier)

Before presenting a conclusion, correction, or recommendation:

1. **Decompose** — is this really one question, or several bundled together?
2. **Name alternatives** — at least one other hypothesis considered before picking the winner.
3. **Overcorrection check** — if you're correcting a claim (yours, the user's, another agent's): disproving A does NOT prove B. Verify the replacement independently, with its own evidence, to the same standard you demanded of the original. Feeling satisfaction at catching an error = the exact moment to slow down.
4. **Tag the evidence tier** and match your wording to it:

   | Tier | You have | Write it as |
   |---|---|---|
   | 1 | Ran it yourself, OR read the project's own source/config as ground truth | "Confirmed by running:" / "Confirmed by reading `[file]`:" |
   | 2 | Official docs, explicit | "Per official docs," |
   | 3 | Maintainer's stated intent | "Design intent is," (not "behavior is") |
   | 4 | A different/related implementation | "A related implementation does X; assumes parity" |
   | 5 | Third-party blog/forum | "One source claims, unconfirmed" |
   | 6 | Your own inference | "Working hypothesis, not confirmed" |

   Reading pg's own project files (config, source, docstrings) is Tier 1, not Tier 2 — it's ground truth about this codebase, stronger than official third-party docs, not weaker. Don't undersell it.

   **Gate F does not stop after the headline questions.** If the design draft (MS-8) asserts a new technical claim not already fact-checked upstream — "run X in a ThreadPoolExecutor because it's blocking," "library X's async client pattern is Y," a library/API choice inside a sub-bullet — that claim needs its own `Fact-checked:` line too, or an explicit `Assumed, not verified` tag. A claim buried three bullets deep in the implementation plan is still a claim.

5. **Precision ladder** — don't write "required" when you mean recommended; don't write "crashes" when you mean degrades; don't write "always/never" when you mean "usually/in most cases." Absolute words are a red flag — check if they're literally true. This applies to constraint language too: "operate efficiently given constraints" is not a real claim — see Constraints rule below.
6. **False binary check** — most "is X needed?" questions are spectrums (hard requirement / quality-only / no effect / requirement-in-one-subcase), not yes/no.
7. **Devil's-advocate, 30 seconds minimum** — what's the strongest argument you're still wrong? What did you stop checking because you found what you were looking for?
8. **Confidence tag** — high (ran it / reliable official docs) / medium (docs but unrun, or two agreeing non-primary sources) / low (single source, inference, different-version extrapolation). Say which, out loud.

If in doubt, fetch `.agents/skills/problem-reasoning/SKILL.md` for the full trap field-guide (false binary, extrapolation-across-implementations, absence-of-evidence-≠-evidence-of-absence, motivated re-reading, anchoring, symptom-fixing).

---

## Tier 2/3 — Module & feature work (fetch `.agents/skills/module-design/SKILL.md` for full detail)

**Entry path:**
- **File named** ("fix `audio_service.py`") → **Path A**: read the whole file/module first, reconstruct data flow, separate observation from judgment (list what it does BEFORE judging it), categorize every problem found (correctness / architecture / perf / error-handling / silent-failure / security / tech-debt) — not just the one bug mentioned.
- **No file named** ("build a voice module") → **Path B**: answer *why* before *how* — what breaks without this, who consumes the output, what already exists that it must integrate with, what are this project's real constraints. Never open an editor before this is answered.

**Constraints need real numbers, not placeholders.** "Operate efficiently given existing constraints" is not a constraint — it's a sentence-shaped hole. Before this feeds into design or MS-7 efficiency work: check `memory-bank/`, existing docs, or prior conversation for an actual RAM ceiling, latency budget, or CPU limit. If none exists and it's genuinely load-bearing for the design (e.g. picking between a 75MB and a 500MB model), ask the user for the number directly — as one specific blocking question, not a vague caveat carried forward unresolved.

**Case list — every category gets a line, checked off explicitly:**

```
[ ] Happy-path      — normal case + its 2-3 most common variants
[ ] Edge cases       — empty/null/zero-length, boundary sizes, unicode/emoji,
                        unusual-but-legal parameter combos
[ ] Failure modes     — dependency down, network fails, disk full,
                        permission denied, hardware/device absent, corrupt data
[ ] Concurrency       — re-entrant calls, interrupted mid-op, race between callers
[ ] Integration       — contract promised to callers, what happens if they violate it
[ ] Platform          — OS-specific behavior, hardware-specific paths, dev vs prod
```

Do not delete a row because it seems irrelevant — write `N/A — [reason]` on that row instead. A platform row marked N/A on a project with a stated target OS is a signal to re-check, not a valid answer.

**Per case cluster:** ≥1 candidate approach with a stated tradeoff, fact-checked (Gate F), reasoned (Gate R). Never present one candidate as if it were the only option.

**Efficiency, stated explicitly per finalist:** memory footprint, latency (where is time spent?), CPU/scaling behavior — against the *real numbers* from the Constraints rule above, not vibes. "Should be fine" is not efficiency reasoning.

**Ask only what's actually blocking; research the rest in parallel.** When you hit a fork that would send the whole design a different direction (e.g. "does a whisper.cpp server already exist, or do I build the client"), that's a real blocking question — ask it. But don't let 2-3 blocking questions freeze the entire task. Anything in the case list or candidate research that doesn't depend on the answer (general library research, non-disputed cases, efficiency numbers for options that survive either branch) should proceed via Gate F/R while waiting on the user, not stall until every question is answered.

**Self-critique before code (mandatory, adversarial, one pass):**
- Completeness: does the design handle every case on the list — mark ✅/⚠️/❌ per case, including the N/A rows (still true, or did new information make them relevant?).
- Verification debt: which claims are fact-checked vs. from memory?
- Overcorrection: if replacing something, was the replacement independently verified?
- Failure surface: map every failure mode → handled / not handled (accepted limitation, stated plainly).
- Harshest reviewer: what's the strongest objection someone trying to reject this would raise? Answer it or accept it explicitly.

---

## Master sequence (all tiers) — fetch `.agents/skills/master-workflow/SKILL.md` for the full flowchart

```
1. Triage → state tier
2. (T2/3) Understand: inputs, failure modes, "done" means what, unstated implications
3. (T2/3) Path A or B — diagnose or purpose-first
4. (T2/3) Case list — full checklist, every row filled or marked N/A with reason
5–6. Research (Gate F) ↔ Reason (Gate R) — LOOP per claim, not one big pass at the end.
     Block only on genuinely architecture-forking questions; research everything
     else in parallel while waiting on answers.
7. (T2/3) Efficiency reasoning per finalist candidate, against real numbers
8. Draft design — approach, key decisions, explicitly-not-doing
9. Merged critique — one adversarial pass (see Self-critique above), revise draft
10. Decision log (see format below)
11. Last live check — exact current syntax for auth/crypto/SQL/subprocess/paths right before coding
12. Write code — small functions, incremental edits, follow existing patterns
13. RUN IT — never skip. If a fact-checked assumption fails at runtime, that's new information: back to Gate F, not a silent patch.
14. Security pass (see below) — mandatory for anything touching input/auth/files/subprocess/network/serialization
15. Close the loop — update project memory if it exists, state verified vs. not-verified vs. residual risk
```

**Tier 1 fast path:** 1 → 8(light) → 9(light) → 10(light) → 11 → 12 → 13 → 14(if applicable) → 15. Steps 2–7 skipped, but Gates F and R still fire on every claim/conclusion.

**No step disappears silently.** Every numbered step in a Tier 2/3 output must appear in the output as either done or `Step N: skipped — [reason]`. A step that's simply absent (no MS-7 heading, no case-list Platform row) is indistinguishable from a forgotten step — treat an unresolved fork found mid-design (e.g. "library A or B depending on X") as a sign that the Efficiency and Research steps for that fork aren't actually done yet, not as something to leave open in the implementation plan.

---

## Security pass (Step 14) — run every time it applies

```
[ ] Secrets: no hardcoded keys/tokens/passwords; loaded from env; not logged
[ ] Injection: parameterized SQL only; subprocess as arg-list, never shell=True
    with unsanitized input; file paths validated against traversal
[ ] Input validation: all external input validated at the boundary
[ ] Deserialization: no pickle/yaml.load/eval on untrusted data
    (yaml.SafeLoader only; ast.literal_eval instead of eval)
[ ] Insecure defaults: TLS verify on, CORS restricted, auth covers new routes,
    restrictive file permissions, cookie flags set
[ ] Dependencies: new deps checked live for known CVEs, checked for
    maintenance status
[ ] Info disclosure: no stack traces/paths/internals in user-facing errors or logs
```

Found something → fix it now, don't just log it. A documented vulnerability that isn't fixed is still a vulnerability.

---

## Decision log — compact format, one per non-trivial task

```
──────────────────────────────────────────────
TASK: [description]   TIER: [1/2/3 — why]
──────────────────────────────────────────────
PURPOSE: [why this exists / what breaks without it]   (T2/3 only)
CONSTRAINTS: [actual numbers — RAM/latency/CPU — or "none found, asked user"]

CASES: happy / edge / failure / concurrency / integration / platform
  — every row filled or marked N/A with a reason   (T2/3 only)

BLOCKING QUESTIONS: [only the architecture-forking ones]
NON-BLOCKING RESEARCH IN PROGRESS: [what's being checked while waiting]

CLAIMS:
  - Fact-checked: "[query]" → [source] → [finding]
    Tier: [1–6]  Confidence: [H/M/L]  Worded as: [...]
  [repeat per load-bearing claim]

DECISIONS: chose X over Y because Z. Rejected: [approach] — [why]

UNVERIFIED ASSUMPTIONS: [claim] — [why not checked / stable-knowledge exception]

SELF-CRITIQUE: [what the adversarial pass caught + what was fixed]
  Failure surface: F1: handled — [how] / F2: NOT handled — accepted, [why]

VERIFIED: [tests run, security checked, sources checked live]
NOT VERIFIED: [couldn't test X because Y]
RESIDUAL RISK: [what's still standing]
──────────────────────────────────────────────
```

---

## Non-negotiables

- Never state a package/API/dependency fact without a live check this session.
- Never treat "I disproved A" as "therefore B" — verify corrections independently (§Overcorrection).
- Never write "required"/"crashes"/"always"/"never" without checking if that's literally true.
- Never carry a vague constraint statement ("efficiently," "given constraints") into a design decision — get the real number or ask for it.
- Never skip Step 13 (run it) — reading code back is not testing it.
- Never skip Step 14 (security pass) for input/auth/files/subprocess/network/serialization work, at any tier.
- Never let a Tier 2/3 case list stay happy-path-only, and never delete a category row instead of marking it N/A with a reason.
- Never fully block a task on clarifying questions when part of the research doesn't depend on the answer — ask only what's genuinely forking, research the rest in parallel.
- Never present a Tier 2/3 design without the one-pass adversarial critique done on it, in this session.
- Never silently drop a failure mode research made inconvenient — name it as an accepted limitation.
- Never skip stating the tier — silent scope-down is a protocol failure; a stated Tier 1 is not.
- Never swallow tool errors in agents — always check `_tool_failed(result)` before returning a success message to the user.
- **Headed Browser Invariant**: All browser automation (`browser_agent`, `media_agent`, `BrowserController`) must run with `headless=False` so the window is visible on desktop.
- **WebSocket Debugging Standard**: To test Makima command execution end-to-end without UI dependencies, send a `WSMessage` to `ws://127.0.0.1:8080/ws`:
  ```json
  {
    "v": 1,
    "type": "user_message",
    "task_id": "debug_task",
    "payload": {
      "text": "<command_text>",
      "conversation_id": "debug_session"
    }
  }
  ```
- If unsure which deep file has the detail you need, fetch it — don't proceed on a guess about what it says.
