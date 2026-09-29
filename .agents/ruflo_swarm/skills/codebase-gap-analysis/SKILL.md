---
name: codebase-gap-analysis
description: Finds gaps, weaknesses, and missing or incomplete work in a codebase — untested modules, swallowed errors, security-shaped patterns, stub functions, dead code, missing input validation, half-finished features, and opportunities for improvement. Make sure to use this skill whenever the user asks to "find gaps," "audit the codebase," "what's missing," "what should I improve," "review for issues," "find tech debt," "what features are incomplete," or wants a structured assessment of a repository's health — even if they don't use the word "gap" or "audit" explicitly, e.g. "is this code ready for production," "what would a senior engineer flag here," or "what am I missing before I ship this." Complements the codebase-analysis skill (which maps what a codebase IS) by focusing on what's WRONG, MISSING, or WEAK in it.
---

# Codebase Gap Analysis

A skill for finding what's wrong, missing, weak, or unfinished in an existing
codebase, and turning that into a report someone can actually act on.

This is a different job from `codebase-analysis`. That skill answers "what
does this codebase do and how is it built" — a map. This skill answers "where
does this codebase fall short, and what should happen next" — an audit. They
are complementary, not competitors: if `docs/ARCHITECTURE.md` already exists
from a `codebase-analysis` pass, read it first instead of re-deriving
structure from scratch. If it doesn't exist, do a lightweight version of that
mapping yourself — you can't find gaps in a system you don't understand yet.

## Why this skill exists

Left alone, an LLM asked to "review this codebase" tends to do one of two
unhelpful things: skim a few files and produce generic advice that would
apply to almost any codebase ("add more tests," "improve documentation"), or
latch onto the first three issues it notices and stop looking, missing
everything else. Neither is useful to someone who actually has to act on the
output. This skill exists to force something closer to what an experienced
engineer does during a real code review: scan broadly and cheaply first,
then spend judgment where it's warranted, then write findings down in a form
where someone can tell at a glance what's urgent and what's optional.

## When this fires

Use this skill whenever the user wants an assessment of a codebase's
weaknesses rather than just a description of it. Concretely:

- "Find gaps in this codebase" / "audit this repo" / "what's missing here"
- "Is this ready for production" / "what would block a launch"
- "What should I improve before I ship this"
- "Find tech debt" / "find dead code" / "find unfinished features"
- "Review this for security issues" (this skill's security pass is a
  heuristic first filter, not a substitute for a real security audit — say
  so in the output, see Non-negotiables)
- "What features are half-built" / "what's stubbed out"
- A vague "review my code" where context (size of the codebase, mention of
  shipping soon, mention of a review) suggests they want more than a style
  pass

Don't use it for a request to review a single small diff or function in
isolation with no broader codebase context — that's a normal code review,
handle it directly. This skill earns its keep on something at least
directory-sized, where systematic scanning beats reading file-by-file.

## The core idea: scan wide, then reason narrow

Grepping a large codebase for twenty different suspicious patterns by hand,
one regex at a time, burns a huge amount of effort on pure mechanics before
any actual judgment happens. The bundled `scripts/scan_gaps.py` does that
mechanical pass in one shot — it walks the repository, applies a set of
heuristic patterns (bare excepts, debug leftovers, hardcoded-secret-shaped
strings, stub functions, commented-out code, TODO markers, and more), and
also does a rough test-coverage heuristic (source files with no
similarly-named test file anywhere in the repo). It writes everything to a
JSON report.

Run it first:

```bash
python /path/to/codebase-gap-analysis/scripts/scan_gaps.py <repo_root> --out /tmp/gaps.json
```

Then read `/tmp/gaps.json`. Treat every entry in it as a **candidate**, not
a finding. The script is deliberately high-recall and low-precision — it
will flag plenty of things that turn out to be fine (a bare `except:` in a
cleanup block that's genuinely meant to catch everything, a `TODO` that's
actually just a style note, a "stub" that's an intentional abstract method).
Your job is to open each flagged location in context, decide whether it's
real, and discard what isn't. A report full of false positives is worse
than no report — it trains the reader to stop trusting it. Precision in the
final write-up matters more than recall in the raw scan.

If the codebase is small enough to read directly, or the language isn't one
the default pattern set covers well, you can skip the script and reason
directly from `grep`/`read` — the categories below still apply, you're just
gathering the candidates by hand instead of via the script.

## Phase 0: Get oriented first

Before hunting for gaps, understand what "good" looks like *for this
specific codebase* — a gap is a deviation from an implied standard, and you
can't spot deviations without knowing the standard.

1. **Check for existing documentation.** If `docs/ARCHITECTURE.md` exists
   (from a `codebase-analysis` pass or otherwise), read it. If
   `docs/KNOWN_ISSUES.md` exists, read it too — don't re-report something the
   team already knows about and has triaged; instead, note whether it's
   still accurate.
2. **Read the README and any CONTRIBUTING/design docs.** These tell you what
   the codebase is *supposed* to do, which is what makes "half-built
   feature" detectable — a README that mentions an endpoint or CLI flag that
   doesn't exist yet, or a partially wired-up feature flag, is a much
   stronger signal than any regex.
3. **Identify the stack and its idioms.** A bare `except:` means something
   different in a quick data-science script than in a payment-processing
   service. Calibrate severity to the actual stakes of the code, not a
   universal rulebook.
4. **Find the tests directory (or lack of one) and CI config.** This tells
   you what's already enforced automatically — don't flag something a
   pre-commit hook or CI job already catches every time; note the gap in
   *what's not covered* by existing automation instead.

Skipping this phase is the single most common cause of a low-value gap
report — one that reads like it was generated against a generic checklist
instead of this specific codebase.

## Phase 1: Structural and correctness gaps

Work through these categories. For each, the scanner surfaces candidates;
you confirm or discard by reading context.

### Error handling
- Bare or overly broad exception handlers that swallow real failures
  (`except:`, `except Exception:` with no re-raise or logging, empty
  `catch {}` blocks).
- Errors caught but never surfaced anywhere — no log, no metric, no
  re-raise, no user-facing message. The failure just vanishes.
- Inconsistent error handling strategy across similar code paths (one
  handler retries, a near-identical one fails silently, another crashes the
  process) — inconsistency is itself worth flagging even when each
  individual instance is "fine."

### Input validation and trust boundaries
- Places where external input (HTTP request bodies, CLI args, file
  uploads, environment variables treated as untrusted, webhook payloads)
  reaches a sink — a database query, a shell command, a filesystem path, a
  template render, a deserializer — without validation or sanitization in
  between.
- Direct dict-style access to request data (`request.GET['x']`) instead of
  a validated/defaulted access, especially when nothing upstream guarantees
  the key exists.
- `eval`, `exec`, `os.system`, `subprocess` with `shell=True`, dynamic
  imports, or dynamic SQL construction — not automatically wrong, but each
  one needs its input traced back to confirm it's not attacker- or
  user-controlled.

### Security-shaped patterns
This skill's scan here is a **cheap first filter**, not a real security
audit — say this explicitly in the output (see Non-negotiables). Still
worth surfacing:
- Strings matching credential shapes (`api_key = "..."`, `sk-...`,
  AWS-key-shaped strings) committed directly in source.
- Secrets read from config but with an insecure fallback (default password,
  key hardcoded as a fallback if the environment variable is unset — a real
  pattern seen in production incidents, and worth calling out specifically
  because it looks safe at a glance).
- Missing authentication/authorization checks on routes that look like they
  should have one, inferred by comparison to sibling routes that do.
- Overly permissive CORS, disabled TLS verification, disabled certificate
  checks — anything that reads like "turned off for local dev and never
  turned back on."

### Dead code and stubs
- Functions whose entire body is `pass`, `...`, or a bare
  `raise NotImplementedError` — confirm whether they're an intentional
  abstract interface (fine) or an abandoned implementation (a gap).
- Large commented-out code blocks — usually safe to flag as "clean up or
  finish," rarely worth deep investigation individually, but worth noting
  if there are many, since it suggests version-control-as-safety-net
  discomfort that's worth mentioning as a pattern.
- Functions or modules with no incoming references anywhere in the
  codebase. Confirm they're not part of a public API or plugin entry point
  before flagging as truly dead — check `__all__`, exports, or a plugin
  registry first.
- Feature flags that are permanently on or off in every environment (the
  flag exists but the codepath it guards is never actually variable
  anymore) — these are safe deletions that reduce cognitive load.

### Test coverage
- Use the scanner's rough untested-module heuristic as a starting list, not
  a verdict — it just checks for a similarly-named test file, so it will
  miss integration-style test suites that don't mirror source file names
  one-to-one. Sanity-check a few before trusting the full list.
- Beyond raw file coverage, look for: tests that assert almost nothing
  (call a function, assert no exception, don't check the actual return
  value); tests that are skipped or marked `xfail` with no tracking issue;
  error paths and edge cases with zero test coverage even in
  well-tested-looking modules (the happy path is tested, the failure path
  never is — a very common and very real gap).

### Consistency and maintainability
- The same logic implemented more than once with subtle differences (not
  DRY, but specifically look for the *subtle differences* — identical
  copy-paste is lower-priority than copy-paste-with-a-bug-introduced).
- Inconsistent naming, error-handling, or logging conventions across
  modules that otherwise look like they should follow the same pattern —
  this raises onboarding cost and hides real deviations inside noise.
- Configuration or constants duplicated in multiple places instead of a
  single source of truth — a classic source of "fixed it in one place,
  forgot the other" bugs.

## Phase 2: Feature and completeness gaps

This is the category most likely to require actually reading product-facing
material (README, docs, issue tracker if accessible, comments describing
intent) rather than pattern-matching code shape:

- **Documented-but-unimplemented behavior**: the README, API docs, or
  docstrings describe a capability, flag, endpoint, or parameter that the
  code doesn't actually implement, or implements only partially.
- **Half-wired features**: a feature flag, config option, or code path that
  exists but is never actually reachable from anywhere a user or caller can
  trigger it — the plumbing exists, the faucet was never connected.
- **Asymmetric API surfaces**: a CRUD-shaped resource that has Create, Read,
  Update but no Delete (or similar asymmetries) with no evident reason —
  worth flagging as a likely oversight rather than a deliberate design
  choice, but say "likely" since sometimes it's intentional.
- **Error messages and edge-case UX**: happy-path flows that are polished
  but failure flows that dump a stack trace, a generic "something went
  wrong," or nothing at all to the end user.
- **Internationalization / accessibility gaps**, if the project's stated
  audience or existing patterns suggest they matter (a project with
  existing i18n scaffolding that new strings bypass; missing alt text or
  labels in a project that otherwise has them elsewhere) — don't invent
  this requirement out of nowhere for a project that's never signaled it
  cares, but don't skip it either if the codebase already has the
  infrastructure and new code just isn't using it.
- **Observability gaps**: code paths, especially error paths and
  background jobs, with no logging or metrics at all, in a codebase that
  otherwise instruments things — meaning when this path breaks in
  production, nobody will know until a user reports it.

## Phase 3: Improvement opportunities (lower stakes, still valuable)

Not everything worth reporting is a "gap" in the sense of something broken
or missing — some of the most valuable findings are "this works, but here's
a meaningfully better way to do it." Include a modest number of these, but
don't let them crowd out the higher-stakes categories above:

- A hand-rolled implementation of something the standard library or an
  already-a-dependency library already does correctly and more robustly.
- An algorithm or data structure with an obviously better time/space
  complexity available for the actual data sizes involved (don't recommend
  a complexity rewrite for a list of 12 items — check scale first).
- A dependency that's outdated enough that its currently-used API is
  deprecated, or that has a known better-maintained alternative — only
  flag this if you have reasonably solid grounds for it, not vague
  "consider updating your dependencies" filler.
- Configuration or setup steps that are manual today but could be
  automated (a documented manual deploy step that could be a script or CI
  job) — genuinely useful and often overlooked because it's not "broken."

## Severity and priority

Every finding gets a severity so the report is usable at a glance instead
of a wall of undifferentiated bullets. Use judgment, not a rigid formula,
but calibrate roughly like this:

| Severity | Meaning | Examples |
|---|---|---|
| **Critical** | Actively exploitable, actively broken, or actively losing data/money right now | Unauthenticated write endpoint, hardcoded production credential, SQL built via string concatenation from user input |
| **High** | Not on fire today, but a realistic near-term incident or a significant correctness risk | Swallowed errors in a payment or auth path, an untested and complex module central to core logic, a race condition in concurrent code |
| **Medium** | Real weakness, but bounded blast radius or requires an unlikely trigger | Missing tests on a rarely-changed internal utility, inconsistent error handling in a low-traffic admin tool, a stale dependency with no known active CVE |
| **Low** | Genuinely worth fixing eventually, not worth interrupting anyone's day for | Commented-out code, minor naming inconsistency, a TODO describing a nice-to-have |

Explain *why* something got the severity it did in one clause, not just the
label — "Critical: this endpoint has no auth check and every sibling
endpoint in the file does" is far more useful and far more trustworthy than
"Critical: missing auth."

## Output format

Unless the user asks for a different shape, structure the final report like
this. Use it as a strong default, not a rigid template you can't deviate
from if the codebase genuinely doesn't have findings in a section — say so
briefly ("No significant test-coverage gaps found beyond X") rather than
omitting the section silently, so the reader knows it was checked.

```markdown
# Codebase Gap Analysis: <project name>

## Summary
2-4 sentences: overall health impression, the single most important thing
to fix, and roughly how many findings fell into each severity tier.

## Critical
(Findings that should block shipping or get fixed immediately. Omit the
section header's contents and instead write "None found" if genuinely empty
-- don't stretch a Medium into a Critical just to fill this section.)

### <short title>
- **Where:** `path/to/file.py:123`
- **What:** one or two sentences, concrete, no hedging if you're confident
- **Why it matters:** the actual consequence, not just "this is bad practice"
- **Suggested fix:** concrete enough to act on, not "add validation"

## High
(same structure)

## Medium
(same structure, can be slightly more compressed -- a table is fine here if
there are many similar-shaped findings)

## Low / Nice-to-have
(compressed list format is fine, these don't need the full four-field
treatment)

## Feature Gaps & Incomplete Work
(Phase 2 findings -- these often aren't "bugs" so keep the tone
descriptive rather than alarmed)

## Improvement Opportunities
(Phase 3 findings)

## What this scan does NOT cover
Be explicit about the boundaries -- see Non-negotiables below. This section
is not optional.
```

If `docs/KNOWN_ISSUES.md` exists in the project (per the `codebase-analysis`
skill's convention), offer to append new confirmed findings there rather
than only leaving them in a one-off report that nobody will look at again —
ask the user first, don't do it silently, since it's their file to own.

## Working with very large codebases

For anything too large to read end-to-end, don't try to force uniform
coverage of every file — prioritize:

1. Entry points and the most-imported/most-called modules first (a bug in
   a leaf utility used nowhere important matters far less than one in code
   every request touches).
2. Anything touching auth, payments, user data, or external input — these
   categories carry outsized consequence regardless of how central the
   file is architecturally.
3. Recently changed files, if you have access to git history (`git log
   --since` / `git diff` against a stable branch) — recent code has had
   less time to get battle-tested and is disproportionately where real
   incidents originate.
4. Anything the scanner flagged multiple times in the same file — a file
   with five different flagged categories is a stronger signal than five
   files with one flag each.

Say explicitly in the report which parts of the codebase you covered
thoroughly versus sampled, so the user knows the actual scope of the
guarantee they're getting.

## Non-negotiables

- **Never present this as a substitute for a real security audit,
  professional penetration test, or compliance review**, if the codebase
  handles anything sensitive (auth, payments, health data, PII). Say so
  explicitly in the "What this scan does NOT cover" section every time
  security topics come up in the findings — the heuristic patterns here
  catch shapes, not proofs, and miss entire classes of vulnerability
  (business-logic flaws, most authorization bugs, timing attacks, and
  anything requiring runtime/dynamic analysis).
- **Never fabricate a finding to fill out a section.** An honest "nothing
  significant found here" is more valuable than a padded list — the whole
  point of severity tiers is that the reader can trust them, and that
  trust breaks the first time they investigate a "Critical" and find
  nothing there.
- **Never report something already tracked and accepted** in
  `docs/KNOWN_ISSUES.md` as if it's new, without checking that file first
  when it exists. If it's still there and still accurate, you can mention
  it briefly as confirmed-still-relevant rather than re-discovering it as
  news.
- **Don't let raw scanner output reach the user unfiltered.** The JSON from
  `scan_gaps.py` is an intermediate artifact for your own reasoning, not a
  deliverable — every finding in the final report should reflect that you
  actually looked at the surrounding code and believe it's real.
- **This skill does not license unsolicited fixes.** Finding and reporting
  gaps is the job here; actually changing code is a separate ask the user
  should explicitly make, the same way `codebase-analysis` distinguishes
  documentation habits from unsolicited refactors. If the user asks you to
  also fix what you found, that's a natural and welcome follow-up — just
  don't assume it.
- **Calibrate to the codebase's actual stakes.** A prototype, a personal
  script, and a payment-processing service warrant very different levels
  of alarm for the same pattern. A bare `except:` in a weekend project's
  plotting script is a Low; the same pattern wrapping a database write in
  a production service is at least a Medium, possibly High depending on
  what happens next in the code.

## Common false positives — filter these out before they reach the report

The scanner is intentionally noisy. These are the recurring false-positive
shapes worth checking for before writing anything up, so the report doesn't
train the reader to distrust it:

- **`except Exception:` that re-raises or logs.** The scanner flags the
  line, but read the block — `except Exception as e: log.exception(e);
  raise` is a completely reasonable pattern (log with full context, then
  propagate). The real problem is `except Exception: pass` or
  `except Exception: continue` with nothing else — that's the one that's
  actually swallowing failures.
- **`pass` as a legitimate no-op, not a stub.** `pass` inside a context
  manager's `__exit__`, an intentionally-empty exception handler that's
  commented as intentional, or a placeholder in an abstract base class
  method that's meant to be overridden are all fine. Confirm by checking
  whether the surrounding class is actually abstract/an interface.
- **`eval`/`exec` on a fixed, non-user-controlled string.** Some codebases
  use `eval` on a literal string for legitimate metaprogramming reasons
  (rebuilding a class from a fixed template, for instance). Trace the
  actual input before flagging — if it's provably not reachable by
  external input, this drops from a security finding to, at most, a Low
  "consider a safer alternative for clarity" note.
- **TODO comments that are actually just narration.** `# TODO: this is
  intentionally simple for now` is different from `# TODO: this breaks
  for negative numbers, fix before release`. Read the comment text, not
  just its presence — the scanner can't tell these apart, you can.
- **"Untested module" false positives from the coverage heuristic.**
  Integration-style test suites, table-driven tests in a single file
  covering many modules, or tests named after behavior rather than the
  module under test (`test_checkout_flow.py` covering five different
  source modules) will all show up as false gaps under the filename-match
  heuristic. Spend a minute confirming a sample of "untested" modules
  against how the test suite is actually organized before trusting the
  full list.
- **Duplicated-looking code that's actually intentionally decoupled.**
  Two services independently implementing similar validation logic isn't
  automatically a DRY violation — in a microservices or plugin
  architecture, deliberate duplication to avoid a shared-library coupling
  is a legitimate design choice. Check whether the modules are meant to
  evolve independently before recommending consolidation.

## Language- and stack-specific calibration notes

The pattern categories in Phase 1 apply everywhere, but what counts as
"the idiomatic way to do this here" varies. A few notes worth knowing going
in, not an exhaustive list:

- **Python:** broad `except Exception` is more common and more often
  legitimate here than in most languages (e.g. a plugin loader that must
  not crash on one bad plugin) — weight it toward Medium unless it's on a
  path handling money, auth, or user data, where it goes back up to High.
  Type hints being absent isn't automatically a gap in an older or
  data-science-flavored codebase, but their *inconsistent* presence (half
  the codebase typed, half not, with no migration in progress) is worth
  noting as a maintainability finding.
- **JavaScript/TypeScript:** an untyped `any` scattered through an
  otherwise-strict TypeScript codebase is a meaningfully different finding
  than the same pattern in a plain-JS project — in the former it's
  actively opting out of guarantees the rest of the codebase relies on. An
  empty `catch {}` block is almost never intentional and should default to
  at least Medium, higher if it's around a network call or a payment SDK.
- **Go:** an ignored error return (`_, _ = someCall()` or a bare
  `someCall()` where the second return is dropped) is the Go-specific
  equivalent of a swallowed exception and deserves the same severity
  treatment as a bare `except:` elsewhere — Go doesn't have exceptions, so
  the scanner's Python/JS-shaped patterns won't catch this; grep for
  discarded error returns by hand when scanning Go code.
- **Frontend/UI code generally:** missing loading states, missing error
  states, and missing empty states around data fetching are the frontend
  equivalent of unhandled error paths in a backend — include them in the
  "error handling" category conceptually even though they don't match any
  of the scanner's text patterns; they require actually reading the
  component logic.
- **Infrastructure-as-code (Terraform, CloudFormation, Kubernetes
  manifests):** treat overly permissive IAM policies, wildcard resource
  scopes, and disabled encryption-at-rest settings the same way you'd
  treat a hardcoded credential in application code — Critical or High,
  not Low, even though nothing here looks like a typical "bug."

## Worked example

A short illustration of the scan-then-reason flow, condensed:

**Scanner output (excerpt from `gaps.json`):**
```json
{
  "categories": {
    "broad_exception_handling": [
      {"file": "billing/charge.py", "line": 84,
       "snippet": "except Exception:", "note": "overly broad except Exception"}
    ],
    "hardcoded_secrets_suspects": [
      {"file": "config/settings.py", "line": 12,
       "snippet": "STRIPE_KEY = \"sk_live_...\" # fallback if env unset",
       "note": "possible hardcoded credential"}
    ]
  },
  "possibly_untested_modules": ["billing/charge.py", "billing/refund.py"]
}
```

**After reading the surrounding code**, `billing/charge.py:84`'s
`except Exception:` turns out to swallow the error and return `None` with
no logging, directly inside the function that submits a customer's card
charge. `config/settings.py:12` is a real live Stripe secret key, committed
as a hardcoded fallback used whenever the environment variable is unset —
meaning it's almost certainly the key that actually gets used in any
misconfigured environment, not just a placeholder.

**What makes it into the report:**

```markdown
## Critical

### Live Stripe secret key committed as a hardcoded fallback
- **Where:** `config/settings.py:12`
- **What:** `STRIPE_KEY` falls back to a hardcoded live-mode secret key
  whenever `STRIPE_KEY` isn't set in the environment, rather than failing
  loudly. This key is now exposed to anyone with repo access and will
  silently activate in any environment where the env var is missing.
- **Why it matters:** a live payment-processing credential in source
  control is an active financial and security exposure, not a theoretical
  one -- it should be treated as already compromised.
- **Suggested fix:** rotate the key immediately via the Stripe dashboard,
  remove the hardcoded fallback, and make the app fail to start (not
  silently degrade) if `STRIPE_KEY` is unset.

## High

### Charge failures are silently swallowed
- **Where:** `billing/charge.py:84`
- **What:** the charge-submission function catches `Exception` broadly and
  returns `None` with no logging, metric, or re-raise. A failed charge
  looks identical to a successful `None`-returning path to any caller that
  doesn't explicitly check.
- **Why it matters:** payment failures here become invisible -- nobody
  gets paged, no log line exists to debug a customer's "I was charged but
  got nothing" report, and the caller can't distinguish failure from a
  legitimate empty result.
- **Suggested fix:** catch specific expected exceptions (the payment
  SDK's own exception types), log the failure with enough context to
  debug it, and either re-raise or return a distinct failure value the
  caller is forced to handle.
```

Note what did **not** make it into the report from the raw scan: the
`possibly_untested_modules` entry for `billing/charge.py` and
`billing/refund.py` was folded into the High finding above as context
("and it's untested, which is part of why this went unnoticed") rather
than written up as a separate, weaker "add tests" bullet — a merged,
well-motivated finding is more useful than two thin ones covering the same
ground.

## Reference

- `scripts/scan_gaps.py` — the bundled heuristic scanner described above.
  Run it once per analysis session; re-run it if you're doing a follow-up
  pass after fixes have landed to confirm findings actually cleared.
- `references/severity_examples.md` — a longer set of worked examples for
  calibrating severity across languages and domains, useful when a finding
  doesn't cleanly match the table above.
