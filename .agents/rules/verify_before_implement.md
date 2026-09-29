# MANDATORY: Verify-Before-Implement Gate (Always-On)

## Core Mandate

Before writing any fix or implementation, **prove the problem is real**.
Never implement a fix for a bug you haven't confirmed exists.
Never add a feature based on an assumption — verify the assumption first.

---

## The Verification Gate (Required Before Every Implementation)

```
[VERIFY] Claim: <what problem/bug/gap am I fixing>
[VERIFY] Evidence: <command output / file read / test failure that confirms it>
[VERIFY] Confirmed: yes / no
```

If `Confirmed: no` — stop. Do not write code. Re-investigate.

---

## How to Verify (In Order of Preference)

1. **Run a failing test** — write a minimal pytest that reproduces the bug.
   If the test passes, the bug doesn't exist. Stop.

2. **Run a command** — `python -c "..."` or PowerShell to reproduce the issue
   directly. Show the actual error/output before writing the fix.

3. **Read the file** — if the claim is "function X has bug Y," read lines
   around X first. Confirm Y is actually present before patching.

4. **Check git log** — `git log -p <file> -n 5` to verify the code wasn't
   intentionally written that way with a known reason.

---

## Anti-Patterns (Strictly Banned)

- ❌ "This probably has a race condition" → immediately rewriting the lock logic
  without reproducing the race
- ❌ "The schema is probably wrong" → patching the schema without first running
  the tool call and seeing the actual 400 error
- ❌ Fixing a bug reported in a summary/audit without re-reading the actual lines
- ❌ Assuming a test failure means what the error message says without reading
  the full traceback

---

## Why This Rule Exists

Agents frequently "fix" things that aren't broken — because an audit report
mentioned a pattern that looked suspicious, or because a summary described a
problem that had already been fixed. Every unconfirmed fix is wasted code that
may introduce new bugs while solving nothing.

The 30-second cost of verification eliminates entire categories of false fixes.
