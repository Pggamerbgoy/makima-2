---
name: codebase-analysis
description: Codebase analysis, living documentation specs, architecture mapping, and progress tracking for existing codebases. Make sure to use this skill whenever analyzing an unfamiliar codebase, creating living documentation, or maintaining system architecture maps.
---

<!--
  HOW TO USE THIS FILE (pg ke liye, agent ke liye nahi):

  Yeh quick-rules ke 4-file system ka 5th deep file hai — alag concern
  cover karta hai (existing codebase samajhna + project docs zinda
  rakhna), rigorous-code-development/problem-reasoning/module-design/
  master-workflow ka replacement nahi. quick-rules abhi isko point nahi
  karta — agar wire karna hai to ek line add karni hogi:

    .agents/skills/codebase-analysis/SKILL.md → codebase analysis
    protocol, living-docs spec

  Woh edit maine khud nahi kiya kyunki quick-rules already tuned/
  versioned hai (v3 changelog etc.) — teri call hai ki wire karna hai
  ya standalone rakhna hai. Path already Antigravity convention se
  match karta hai (`.agents/skills/<name>/SKILL.md`), semantically
  triggered — koi manual toggle nahi chahiye.
-->

# Codebase Analysis & Living Documentation — fetch on Tier 2/3 when
# working inside an existing codebase, or whenever `docs/ARCHITECTURE.md`
# doesn't exist yet for the project.

You have no memory between sessions. This file exists so every session
starts from real understanding of the codebase — not assumption — and
leaves behind a trail accurate enough that the next session doesn't
re-derive the same context from scratch.

---

## When this fires

- First task in a project with no `docs/ARCHITECTURE.md` yet → run Phase 1 in full before Path A/B from `module-design/SKILL.md`.
- Any later task → Phase 2 only (read before, update after).
- Major structural change (new service/layer, framework swap, dependency overhaul) → re-run Phase 1's affected sections, don't just patch around it.

---

## Phase 1 — Initial deep analysis (once per project, re-run on major structural change)

```
[ ] Structure   — directory tree, architectural boundaries, who owns what
[ ] Stack       — languages/frameworks/libraries + actual installed
                   versions from lockfiles (Tier 1 evidence — read the
                   file, don't recall it)
[ ] Entry points— where execution starts; trace one real request/task
                   end-to-end through every layer it touches
[ ] Data flow   — external deps: APIs, DBs, queues, other services
[ ] Conventions — naming, error handling, logging, config patterns
                   actually in use (not "standard" ones)
[ ] Risk zones  — TODO/FIXME/HACK density, thin-tested critical paths,
                   places where code and docs already disagree
```

Same rule as the case list in `02-module.md`: no row gets deleted for
seeming irrelevant. Mark `N/A — [reason]` instead.

**Output:** `docs/ARCHITECTURE.md`. Updated in place when structure
changes — this is a map, not a changelog.

---

## Phase 2 — Continuous tracking while working

**Before touching code:** read `docs/ARCHITECTURE.md` and
`docs/PROGRESS.md` if they exist, instead of re-deriving context.

**After any meaningful change:** update the relevant doc in the same
turn — not "later." Meaningful = a future session would need this to
avoid re-doing or contradicting the work.

**Doc and code disagree → fix the doc immediately.** Same standard as
Gate F: an unverified claim and a stale doc are the same failure.

| File | Purpose | Update trigger |
|---|---|---|
| `docs/ARCHITECTURE.md` | Structure, stack, conventions, data flow | Structural change |
| `docs/PROGRESS.md` | Done / in-flight / next | End of session, or completed subtask |
| `docs/DECISIONS.md` | Why, alternatives rejected | Any non-trivial decision — **skip this file entirely if the project already logs via `devlog.py`; don't run two decision logs in parallel** |
| `docs/KNOWN_ISSUES.md` | Bugs, tech debt, deliberate shortcuts | Shortcut taken, or bug found-not-fixed |

**Entry discipline:**
- Short and factual — one or two lines beats a paragraph.
- New entries prepended under a dated heading; don't rewrite history.
- Past ~200 lines, compress older entries into a single "Earlier" block instead of deleting.
- Document the *why* and the *non-obvious* — not what's self-evident from reading the function.

---

## Non-negotiables

- Never start Tier 2/3 work in an existing codebase without reading `docs/ARCHITECTURE.md` first if it exists, or producing it first if it doesn't.
- Never let a doc silently drift from the code it describes — fix in the same turn you notice the mismatch.
- Never spin up a second decision log if `devlog.py` (or an equivalent) already owns that job.
- This file governs documentation habits only — it does not license unsolicited refactors, renames, or restructuring. Those still need an explicit ask, same as `00-quick.md`'s verbatim-delivery rule.
- If the project already has a different `docs/` layout, adapt to it — don't impose this one. Ask only if the mismatch would actually cause confusion.
