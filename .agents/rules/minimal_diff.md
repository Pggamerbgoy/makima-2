# MANDATORY: Minimal Diff Protocol (Always-On)

## Core Mandate

Make the **smallest possible code change** that correctly fixes the problem.
This rule is non-negotiable and applies to every edit — bug fixes, refactors,
feature additions, and one-liners alike.

---

## What This Means in Practice

### ✅ Allowed
- Changing 1-3 lines inside a function to fix a bug in one branch
- Adding a single guard clause (`if x is None: return`)
- Appending one new method to an existing class
- Fixing an import or a typo

### ❌ Strictly Banned
- Rewriting an entire function (50+ lines) when the bug is in 2 lines of it
- Restructuring a class just because you touched one method in it
- Reformatting unrelated code while applying a fix ("while I'm here...")
- Adding new parameters / return values that weren't asked for
- Changing variable names across a whole file as part of a logic fix
- Splitting or merging files that weren't part of the task

---

## The Minimal Diff Gate (Required Before Every Edit)

Before writing any code change, explicitly state:

```
[MINIMAL DIFF] What is the exact bug/gap? <one sentence>
[MINIMAL DIFF] Smallest fix: <what lines change and why>
[MINIMAL DIFF] What does NOT need to change: <what I'm leaving alone>
```

If you cannot answer "What does NOT need to change," your scope is too broad.
Narrow it before writing.

---

## Refactor ≠ Fix

If the real problem requires a larger restructure, that is a **separate task**.
Do the minimal fix first. Log the refactor opportunity in `docs/DEVLOG.md`.
Never bundle an unrequested refactor into a bug fix.

---

## Why This Rule Exists

Large diffs introduce unintended regressions in untouched code paths.
Every extra line changed is a line that can break a test, introduce a subtle
bug, or conflict with another in-flight change. The goal is surgical precision —
change only what must change, leave everything else untouched.
