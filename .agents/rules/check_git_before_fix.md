# MANDATORY: Check Git History Before Changing Existing Logic (Always-On)

## Core Mandate

Before changing any existing logic — especially code that looks "wrong" or
"suspicious" — **check git history** to verify it wasn't intentionally written
that way with a known reason.

Changing intentional code without understanding why it was written that way
introduces regressions and reverses deliberate engineering decisions.

---

## The Git-Before-Fix Gate

Before modifying any non-trivial existing logic, run:

```powershell
git log --oneline -10 apps/brain/path/to/file.py
git log -p -5 apps/brain/path/to/file.py  # see actual diffs
```

Then check `docs/DEVLOG.md` for any entry mentioning the function/module.

```
[GIT CHECK] File/function being changed: <name>
[GIT CHECK] Last meaningful commit: <hash + message>
[GIT CHECK] Was this intentional? <yes/no/unclear>
[GIT CHECK] DEVLOG entry? <yes: line X / no>
```

If `Was this intentional: yes` → do NOT change it without a clear reason that
overrides the original rationale. Document the override in `DEVLOG.md`.

---

## Common Cases This Prevents

| Looks Wrong | Was Actually Intentional |
|---|---|
| `FAILSAFE = False` in pyautogui | Set intentionally for CI/headless test env |
| `status IN ('paused', 'active')` CAS query | Fixed deadlock — not a typo |
| Empty `tools=[]` filtered out before API call | Prevents 400 from strict cloud APIs |
| `try_parse_json` fallback returning `{}` | Deliberate graceful degradation |
| `asyncio.to_thread()` instead of `await` | Windows constraint — no subprocess event loop |

---

## DEVLOG.md Is Authoritative

`docs/DEVLOG.md` is the engineering decision log. Before changing any behavior
that seems odd, search it:

```powershell
Select-String -Path docs/DEVLOG.md -Pattern 'function_name|module_name'
```

If a DEVLOG entry explains the current behavior → understand it before overriding it.
If you override it anyway → add a new DEVLOG entry explaining why.

---

## Why This Rule Exists

Multiple sessions have nearly reversed deliberate fixes (e.g. the pyautogui
FAILSAFE guard, the CAS status expansion, the duckduckgo_search removal) because
the code "looked wrong" to a fresh agent without context. Git history and DEVLOG
are the institutional memory that prevents this. Use them.
