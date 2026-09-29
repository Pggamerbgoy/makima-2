# MANDATORY: Dead Code Cleanup After Every Edit (Always-On)

## Core Mandate

After applying any code change, **immediately check for and remove dead code**
that the change made unreachable or obsolete. Never leave orphaned code behind.

---

## The Post-Edit Dead Code Gate

After every edit, run through this checklist:

```
[DEAD CODE CHECK] Did my change make any existing function/branch unreachable? <yes/no>
[DEAD CODE CHECK] Did my change make any import unused? <yes/no>
[DEAD CODE CHECK] Did my change make any variable/constant unused? <yes/no>
[DEAD CODE CHECK] Did my change replace a function that is now zero-callsite? <yes/no>
```

If any answer is `yes` → remove the dead code in the same diff.

---

## How to Check

### Unused imports
```powershell
# After editing a file, check imports:
python -m py_compile <file>  # catches syntax errors
# Then visually scan the import block against usages in the file
```

### Zero-callsite functions (after replacing a utility)
```powershell
Get-ChildItem -Path apps -Recurse -Filter '*.py' |
  Select-String -Pattern 'old_function_name'
```
If 0 results → the function is dead → remove it.

### Unreachable branches
After adding a guard clause or early return, check if any branch below it
can now never be reached under any call pattern.

---

## Anti-Patterns (Strictly Banned)

- ❌ Leaving the old implementation alongside the new one "just in case"
- ❌ Commenting out old code instead of deleting it (`git` has history)
- ❌ Keeping an import after removing the only usage of it
- ❌ Leaving a `TODO: remove old X` comment — remove it now, not later
- ❌ Keeping a wrapper function that just calls the new one with identical args

---

## Git Is Your Safety Net

**Never** keep dead code "in case we need it back."
That's what `git log` and `git revert` are for.
Dead code left in place creates confusion, fails linters, and misleads
future readers about what the system actually does.

---

## Why This Rule Exists

Every patch session that doesn't clean up dead code leaves the codebase
slightly worse than it found it. Over dozens of sessions, this accumulates
into thousands of lines of unreachable, confusing, unmaintained code —
which is exactly the duplication/dead-code problem this project has been
spending audit cycles to fix.
