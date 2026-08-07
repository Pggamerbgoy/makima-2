---
trigger: always_on
---

# MANDATORY: Skill-First Execution Protocol

## Core Rule (Strict — No Exceptions)

Before starting ANY task — code changes, debugging, new features, refactors,
architecture decisions, bug fixes, config changes, or even "small" one-line
fixes — you MUST perform a Skill Check before writing a single line of code,
response, or plan. This is non-negotiable regardless of how simple, urgent,
or obvious the task appears.

## Step 1: Mandatory Skill Check Output Format

The FIRST thing in every response must be:

```
[SKILL CHECK] Task: <one-line summary of what is being asked>
[SKILL CHECK] Applicable skill(s): <skill name(s)> — <why each applies>
[SKILL CHECK] Loaded in full: yes/no
```

If genuinely no skill applies:
```
[SKILL CHECK] No skill matches. Reason: <specific justification, not "seems simple">
```

You are not allowed to skip straight to a plan, a diff, or code. This line
must appear before anything else, every single time, with no exceptions for
task size, urgency, or repetition (i.e. "we already discussed this" is not a
valid reason to skip it either).

## Step 2: Load Full Instructions, Not Just Metadata

Antigravity only loads skill metadata (name + description) by default and
lazy-loads full content. You must explicitly load and re-read the FULL body
of any matched skill — never rely on memory of what a skill said in a
previous turn, even earlier in the same conversation. Skills can be updated
between sessions; stale memory of a skill's content is not acceptable
justification for deviating from what it currently says.

Step 3: Project-Specific Skill Routing (Makima)

Priority Ordering — Workflow Skill Is ALWAYS First, Unconditionally: The
master workflow skill (master-workflow) must be consulted and loaded FIRST on
EVERY task, with no exceptions and no threshold to clear — not "whenever more
than one skill applies," not "when the task looks complex enough to warrant
it." Even when only one other skill seems relevant, or when it looks like no
other skill applies at all, master-workflow is still consulted first. The
previous wording of this rule ("whenever more than one skill applies") was
itself a loophole: a task that clearly matched exactly one routing-table row
(e.g. "disputed claim → problem-reasoning") could be read as never
triggering the "more than one skill" condition, letting the model skip
straight to that one skill and never load master-workflow at all. That
loophole is closed. The rule is now unconditional: master-workflow is
consulted first, every single time, regardless of how many other skills end
up applying — zero, one, or several.

master-workflow defines the overall sequencing and structure the task should
follow; other skills (reasoning discipline, module design, coding discipline)
operate within that structure, not alongside it or ahead of it. The
[SKILL CHECK] line must therefore ALWAYS list master-workflow first, in every
single response, even when the routing table below points to only one other
skill:

[SKILL CHECK] Applicable skill(s): master-workflow (consulted first,
defines overall sequencing) → rigorous-code-development (for the actual
implementation discipline within that sequence)

Never list the workflow skill second, and never omit it, even when the
routing table below appears to name only a single other skill. If
master-workflow genuinely does not apply (e.g. a pure documentation question
with no code/plan/architecture component), you must still explicitly state
that you checked and it doesn't apply, and say why — silent omission is not
distinguishable from forgetting to check, so it is never acceptable.

Use the rest of this section as a routing table — check every row, not just
the first match. Every row below implicitly has "preceded by master-workflow"
attached to it, even though it isn't repeated on every line:

Any real (non-throwaway) code change to Makima — apps/brain, apps/ui, Rust core, agent files, orchestrator, ai_handler — → rigorous-code-development is mandatory. This includes "quick fixes," one-line changes, and config edits that affect runtime behavior. Task size is explicitly NOT a valid reason to skip this skill — this is the single most common failure mode from past sessions and must be actively guarded against.
Any concurrency, locking, async, or race-condition-adjacent change (Semaphore, Lock, asyncio.gather, shared state across agents) — → rigorous-code-development's devil's-advocate/critique step is mandatory before the plan is presented, not just before code is written. Do not present a "final" plan without at least one explicit self-critique pass.
Any change touching ai_handler.py, provider routing, or backend selection — → cross-check for shared/singleton state impact (e.g. CircuitBreaker, RateLimitManager) before concluding a change is safe.
Any task producing a devlog-worthy decision (architecture choice, rejected alternative, tradeoff) — → append an entry via the devlog.py convention. Do not silently skip logging because the task felt minor.
Any task before merging/finalizing code — → mentally run through security_scan.py categories (hardcoded secrets, injection points, unsafe input handling, insecure defaults) even if the script itself isn't invoked, and flag anything that would trip a HIGH finding.
Any conclusion, correction, root-cause diagnosis, or disputed technical claim — → problem-reasoning is mandatory (decompose, multiple hypotheses, overcorrection check, evidence-tier tagging, devil's-advocate pass) before the conclusion is presented as settled.
New module, new subsystem, or "improve/fix X" with no file named yet — → module-design is mandatory before an editor is opened: why the module exists, every case it must handle, current-best approach per case, self-critique — then code.
Working inside an existing codebase with no `docs/ARCHITECTURE.md` yet, or the first task in a project — → codebase-analysis Phase 1 runs before module-design's Path A/B.
Need the condensed, fast-reference version of the fact-check + reasoning + tier rules without opening the full deep files — → quick-rules.
Any debugging or verification of Makima UI ↔ Backend command routing (media playback, browser automation, messaging) — → test the WebSocket endpoint directly (`ws://127.0.0.1:8080/ws`) with a raw `WSMessage` JSON payload (`{"v": 1, "type": "user_message", ...}`) before assuming an agent is broken, and verify `_tool_failed()` handling.
Any testing or execution of browser automation (`browser_agent`, `media_agent`, Playwright) — → ensure browser is launched with `headless=False` so the browser window is visible to the user on desktop.
Spreadsheet, Word doc, PDF, or slide deck output — → use the matching document-creation skill (xlsx/docx/pdf/pptx) **[PLANNED — not yet built in this project as of 2026-07-26; if genuinely needed before it exists, say so explicitly rather than silently improvising a substitute]**.
New skill creation or editing an existing skill/rule — → skill-creator guidance applies **[PLANNED — not yet built in this project as of 2026-07-26]**; don't hand-roll skill structure from scratch without checking it once it exists.
Any plan, architecture proposal, or design presented for approval — → rigorous-planning is mandatory before the plan is shown **[PLANNED — not yet built in this project as of 2026-07-26; module-design's Path A/B + self-critique pass covers this ground until rigorous-planning exists]**, in addition to whatever coding skill applies to the eventual implementation.

## Step 4: The "This Seems Small" Trap

The most common failure mode observed across past sessions is skipping the
Skill Check because a task "seems small," "seems like a one-liner," "is just
a quick fix," or "is obviously safe." This reasoning is explicitly banned.
Examples of tasks that seemed small but were not:

- "Just cap max active agents at 4" → touched concurrency, locking,
  deadlock-prone code, and required multiple critique rounds to get right.
- "Just move the JSON parse fallback warning" → looked cosmetic, but sits
  inside the same function as concurrency-sensitive dispatch logic.
- "Just change task_id to parent_task_id" → single-line change that
  triggered a multi-layer deadlock investigation.

If a task looks small, that is precisely when the Skill Check matters most,
because small-looking changes in shared/concurrent code are the highest-risk
category, not the lowest.

## Step 5: No Retroactive Compliance

You cannot generate code or a plan first and then retroactively add a
[SKILL CHECK] line to make it look compliant. The check must happen first,
genuinely inform what follows, and be visibly connected to the actual plan
(i.e. if `rigorous-code-development` is cited, the response must actually
show decompose → research → draft → critique steps, not just cite the name
and skip to code).

## Step 6: Conflicts With User Instructions

If a user explicitly asks to skip the skill check ("just give me the code
fast," "skip the process this time," "don't overthink it") — you must still
output the one-line [SKILL CHECK] block. It costs one line and does not
meaningfully slow anything down. Flag the conflict briefly, then proceed:

```
[SKILL CHECK] Task: <summary>
[SKILL CHECK] Applicable skill(s): rigorous-code-development
[SKILL CHECK] Note: user requested skipping process — check still logged;
proceeding with an abbreviated version of the workflow rather than skipping
entirely.
```

Do not fully skip it even under direct pressure to do so — abbreviate the
depth of the workflow if truly needed, but never remove the check itself.

## Step 7: Multiple Skills, Combined

When more than one skill applies, name all of them explicitly and state how
they combine, rather than picking only the most obvious one:

```
[SKILL CHECK] Applicable skill(s): rigorous-code-development (for the
research/critique workflow) + xlsx (because the deliverable is a formatted
spreadsheet, not just code)
```

## Step 8: Self-Verification Before Responding

Before finalizing any response, silently check:
- Did I output [SKILL CHECK] as the very first thing?
- Is master-workflow listed FIRST in the Applicable skill(s) line — not
  second, not omitted — regardless of how many other skills apply? (This is
  a common failure mode: routing straight to the one obviously-matching skill,
  e.g. problem-reasoning or rigorous-code-development, without prefixing
  master-workflow, because the routing table's per-row arrows don't repeat
  the master-workflow requirement on every line. It's still required on
  every line.)
- Did I actually load full skill content, or am I working from a vague
  memory/summary of it?
- Does my actual response follow the cited skill's process (decompose,
  research, critique), or did I just name-drop it and proceed as normal?
- If I claimed "no skill matches," is that actually true, or did I just not
  look carefully at the routing table in Step 3?

If any answer is "no" or "unsure," stop and redo the Skill Check properly
before continuing.

## Why This Rule Exists

Across past sessions, skills were repeatedly skipped for two reasons: (1)
skill descriptions were too vague for automatic triggering under Antigravity's
lazy-load metadata system, and (2) the model judged tasks as "simple enough"
to bypass process discipline, which repeatedly turned out to be false for
concurrency-adjacent and shared-state code specifically. This rule removes
both judgment calls — the check is unconditional, every time, and task size
is explicitly disqualified as a reason to skip it.

## FINAL ENFORCEMENT — READ THIS LAST

If your response does not begin with the exact text "[SKILL CHECK]",
that response is INVALID and must be discarded and redone before showing
it to the user. This is a hard formatting gate, not a suggestion. This
applies regardless of task size, complexity, or how much you feel like
the actual technical content matters more than the header — the header
comes first, unconditionally, every single time. A long, detailed, high-quality
technical analysis that skips this header is still a protocol violation and
is not an acceptable response, no matter how good the analysis itself is.

Equally: if your [SKILL CHECK] line names any skill without master-workflow
listed FIRST, that response is ALSO invalid, for the same reason — this is
not a lesser or secondary rule, it carries the same hard-gate status as the
header itself. "Only one other skill clearly applied, so I skipped
master-workflow" is explicitly not a valid justification — that is precisely
the loophole this file closes. There is no task, however small, single-skill,
or obvious, where master-workflow is not the first name on the line.


The moment you receive approval to proceed with an already-discussed plan
("go ahead," "proceed," "approved," etc.), that exact message is your
trigger to output a fresh [SKILL CHECK] header before the very next tool
call — not after it, not "because we just discussed it." Approval is the
trigger, not an exemption.