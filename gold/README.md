# gold/ — Grader only

This directory holds reference test suites (T_meta, R_meta) used **only for
post-hoc grading**. They are never read by the gate.

- `runner.py` / `scorer.py` (the gate) never open this directory.
- `grader.py` reads it, and refuses to grade if any R_gate input overlaps a
  gold input (tautology guard).
- `canary_check.py` writes an unpredictable string into `grades/<run_id>/`
  and greps the entire gate output tree for it after every run. A hit
  invalidates the run.

Isolation is proven by the canary check, not by directory layout.