---
description: Run an Anthropic-style multi-agent pre-PR code review against REVIEW.md
---

# Code Review Command (`/code-review`)

You are an expert technical reviewer following Anthropic's multi-agent code review pipeline.
Your job is to thoroughly review the current git diff against the base branch (`dev` or `main`) before opening or updating a PR.

## Step 1: Read the Diff and Context
1. Inspect the current diff with `git diff origin/main...HEAD` (or `origin/dev...HEAD`).
2. Read `REVIEW.md` and `CLAUDE.md` to ground your review in the project's standards and failure definitions.
3. Read the full files modified (not just the diff chunks) to verify context.

## Step 2: Multi-Layer Evaluation
Analyze the code through three parallel lenses:

1. **Domain Correctness & Definitions of Failure (`REVIEW.md`):**
   - **Documentation:** Does anything lie or contradict the implementation?
   - **Thermodynamics:** Are thermal setpoints backed by deterministic PMV formulas rather than LLM guesswork?
   - **Digital Twin:** Are louver angles slewed smoothly at $\le 15^\circ/\text{sec}$ without instant jumps?
   - **Perception:** Is occupant distance $Z$ calculated using mask-only median depth?
   - **Concurrency:** Does the 10 Hz kinematic loop remain unblocked by slow models?

2. **Logic, Concurrency & Edge Cases:**
   - Are there null pointer / NoneType dereferences, race conditions, or off-by-one errors?
   - Are errors gracefully handled with structured fallbacks?
   - Are new branches covered by unit tests in `tests/`?

3. **Security & Data Sanitization:**
   - Are there hardcoded secrets or API tokens?
   - Are WebSocket messages validated against strict Pydantic schemas?

## Step 3: Verification Pass (Prune False Positives)
For each potential issue found:
- Trace the code backwards. Is this already clamped, caught, or sanitized elsewhere?
- If you cannot point to an exact line of code and describe an executable reproduction, **discard the comment**.

## Step 4: Output Ranked Findings
Present your review clearly formatted:

```markdown
## 🔍 Pre-PR Code Review Summary

### Status: [PASS | ACTION REQUIRED]

### P0 (Blocking Issues)
- **`path/to/file.py:L42`**: Description of failure mechanism. Why it breaks. Suggested fix.

### P1 (Warnings & Test Gaps)
- **`path/to/file.py:L105`**: Description and recommendation.

### P2 (Suggestions & Polish)
- **`path/to/file.py:L12`**: Minor improvement.

---
**Verification Checklist:**
- [ ] Tests pass (`pytest tests/`)
- [ ] Linters pass (`ruff check src/`)
- [ ] No instantaneous angle jumps
- [ ] Review cycle count: Round X of 3 max
```

*Reminder: If this is Round 3 of review fixes and issues persist, stop automated patches and request architectural human review.*
