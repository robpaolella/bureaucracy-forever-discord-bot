# 002 — SYNC-SPEC Done-means line

## Ask
Robert: fix the SYNC-SPEC.md line that still pointed at `.claude/hooks/test-git-guard.sh`,
deleted in the Maestro onboarding.

## Done means
- Line 428 points at each repo's own Checks. The git guard and its tests now live in
  Maestro (`/git/maestro/tests/`).
- SYNC-SPEC.md identical in both repos, changed in paired PRs.
