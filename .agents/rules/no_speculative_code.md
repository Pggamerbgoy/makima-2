# MANDATORY: No Speculative Code (Always-On)

## Core Mandate

Only implement what was **explicitly asked for**.
Never add code based on guesses about what might be needed in the future.

---

## Banned Patterns (Strictly Prohibited)

### ❌ TODO Stubs
```python
# TODO: add retry logic here
# TODO: handle edge case X
```
If it needs to be done, do it now or don't mention it. Stubs become permanent.

### ❌ Unused Parameters
```python
def process(data, verbose=False, timeout=None, retry_count=3):  # only data is used
```
Never add parameters that aren't used in the current implementation.
Add them when they're actually needed.

### ❌ "Just In Case" Branches
```python
if mode == "fast":
    ...
elif mode == "slow":
    ...
elif mode == "ultra":  # nobody asked for this
    ...
```

### ❌ Future-Proof Abstractions
Don't create a base class / factory / registry / plugin system when
a single concrete implementation was asked for. Generalize only when
the second concrete case actually exists.

### ❌ Unrequested Logging / Metrics
Don't add `logger.debug(...)` lines, telemetry hooks, or counters to code
you're touching for an unrelated reason.

### ❌ Defensive Code for Impossible Cases
```python
if user is None:  # user is always set at this call site
    return
```
Don't guard against cases that cannot happen at the actual call site.
Read the callers before adding defensive branches.

---

## The Scope Gate (Required Before Writing)

```
[SCOPE] What was explicitly asked: <exact request>
[SCOPE] What I'm implementing: <what I'll write>
[SCOPE] What I'm NOT adding: <speculative things I'm leaving out>
```

---

## When to Deviate

If you genuinely see a critical safety issue (e.g. a null-deref that WILL crash
in production), fix it and **document it explicitly** in the response:

> "Also fixing an unrelated crash at line X — this would have caused a
> production outage, not adding speculatively."

This is the ONLY exception, and it must be stated explicitly, not silently added.

---

## Why This Rule Exists

Speculative code:
- Is never tested (because it handles cases that don't exist yet)
- Becomes maintenance burden permanently
- Bloats diffs and makes reviews harder
- Often conflicts with future requirements when the real case arrives
- Makes "small" tasks generate 10x the code actually needed

YAGNI (You Aren't Gonna Need It) — implement the minimum that satisfies
the current requirement, nothing more.
