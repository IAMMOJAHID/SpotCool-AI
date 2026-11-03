# SpotCool AI Code Review Evaluation Suite

> Reference: [How the Claude Code team does code review — Continuous Improvement via Evals](https://claude.dev/blog/solving-code-review-at-anthropic/)

This directory houses the regression benchmark suite for automated code review agents. Whenever review prompts, system instructions, or `REVIEW.md` are modified, they must be benchmarked against these evaluations.

## Structure
* **`code_review_evals.json`**: Benchmark test cases containing known traps, past incidents, and clean baseline PRs.
* **Evaluation Metrics**:
  1. **Recall**: Did the reviewer catch 100% of P0 domain traps (instant louver jumps, bounding box depth leaks, PMV hallucinations, loop blocking)?
  2. **Precision / False-Positive Rate**: Did the reviewer generate any spurious P0/P1 complaints on the clean baseline PR?
  3. **Evidence Quality**: Did the reviewer provide exact file paths, line numbers, and actionable reproduction steps?

## Continuous Improvement Cycle
```
[Production / Review Bug Slipped Through]
                   │
                   ▼
  [Capture Diff into evals/*.json]
                   │
                   ▼
     [Tune REVIEW.md / Prompts]
                   │
                   ▼
  [Verify 100% Trap Recall & 0 FP]
                   │
                   ▼
              [Deploy]
```
