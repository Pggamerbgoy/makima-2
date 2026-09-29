# Severity calibration — extended examples

Use these alongside the severity table in SKILL.md when a finding doesn't
map cleanly onto the four tiers. Organized by domain rather than language,
since the stakes of a pattern usually depend more on what the code *does*
than what it's written in.

## Authentication & authorization

- **Critical:** An endpoint that mutates or reveals another user's data
  with no ownership check — e.g. `DELETE /orders/:id` that deletes any
  order by ID regardless of who's logged in. This is an active, exploitable
  vulnerability the moment it ships.
- **High:** An authorization check that exists but relies on
  client-supplied data (a role or user ID trusted from a request body or
  cookie instead of a verified session/token) — not yet proven exploitable
  without more digging, but the pattern is inherently unsafe.
- **Medium:** An internal admin tool with weak authorization, if it's only
  reachable from a trusted internal network and that's independently
  verified — the blast radius is genuinely bounded, though "internal
  network" is worth double-checking rather than taking on faith.
- **Low:** A permissions check that's technically redundant because an
  earlier middleware layer already enforces it — not wrong, just
  duplicated logic worth simplifying.

## Payments & financial data

- **Critical:** Any hardcoded live-mode payment API key or webhook secret,
  regardless of whether it's currently reachable — treat it as already
  compromised the moment it's in version control history, even if later
  removed from the current file (history retains it).
- **High:** A webhook handler that doesn't verify the signature of
  incoming payment-provider webhooks — meaning anyone who knows or guesses
  the endpoint URL can fake a "payment succeeded" event.
- **High:** Silent failure in a charge/refund path (see the worked example
  in SKILL.md) — money-adjacent code that fails without telling anyone.
- **Medium:** Idempotency not enforced on a payment-creation endpoint,
  where a network retry could theoretically double-charge — real risk, but
  usually mitigated in practice by the payment provider's own idempotency
  keys if those are being passed correctly (check before escalating to
  High).

## Data handling & privacy

- **Critical:** Logging that includes raw passwords, full credit card
  numbers, or full SSNs in plaintext log output.
- **High:** Logging that includes other clearly sensitive PII (full
  addresses, health information, government ID numbers) where the
  logging infrastructure isn't confirmed to be access-restricted and
  retention-limited.
- **Medium:** A database query that pulls more columns than the calling
  code actually uses, including sensitive ones — not a leak by itself
  unless that data reaches a response or log, but worth flagging as
  needless exposure surface.
- **Low:** Missing field-level comments on why a particular piece of data
  is collected — a documentation gap adjacent to privacy, not a privacy
  gap itself.

## Concurrency & reliability

- **Critical:** A race condition in code that handles money, inventory, or
  any other resource where a double-execution has real-world consequence
  (double-booking, double-charging, double-shipping) and the race is
  plausibly triggerable under realistic load.
- **High:** A background job with no retry logic and no dead-letter
  handling for a task where failure has real consequence (e.g., a job that
  sends a critical notification and just drops it forever on any
  transient error).
- **Medium:** A cache with no invalidation strategy where staleness is
  merely annoying, not harmful (a "last updated" timestamp displayed a few
  minutes late).
- **Low:** A retry loop with a fixed short delay instead of exponential
  backoff, on a low-traffic internal integration where the extra load is
  negligible.

## Test coverage

- **High:** Zero test coverage on a module that's both complex (lots of
  branching logic) and central (imported/called from many places) —
  changes here are both likely to introduce bugs and likely to have wide
  blast radius when they do.
- **Medium:** Zero test coverage on a simple, rarely-changed utility —
  real gap, low urgency.
- **Medium:** Tests exist but only cover the happy path; every error
  branch is untested — common enough to be almost the default state of a
  codebase, still worth calling out explicitly rather than letting
  "coverage exists" hide that the coverage that exists doesn't test
  failure.
- **Low:** A skipped/`xfail` test with a linked tracking issue and a clear
  reason — this is *handled* tech debt, not a fresh finding; mention it
  only to confirm it's still accurate, not as a new gap.

## Dependencies & supply chain

- **High:** A dependency with a known, unpatched CVE that's actually
  reachable in how the codebase uses it (not just present in
  `requirements.txt`/`package.json` but unused).
- **Medium:** A dependency several major versions behind current, using a
  deprecated API surface that still works today but is likely to break on
  the next forced upgrade (a transitive dependency bump, a language
  runtime upgrade).
- **Low:** A dependency that's simply outdated with no known
  vulnerability and no deprecated-API usage — "consider updating
  eventually" territory, don't dress this up as urgent.

## Feature completeness

- **Medium (usually, not High):** A documented API parameter or CLI flag
  that silently does nothing — no crash, no error, it's just ignored. This
  is worse than an outright missing feature because it fails silently:
  someone using the flag believes it worked. Escalate to High if the
  ignored behavior is safety- or correctness-related (e.g., a documented
  `--dry-run` flag that isn't actually respected).
- **Low:** A feature that's genuinely just unbuilt yet and not documented
  as existing anywhere — this is a roadmap item, not a gap; mention it in
  "Feature Gaps & Incomplete Work" with a descriptive tone, not an alarmed
  one.
