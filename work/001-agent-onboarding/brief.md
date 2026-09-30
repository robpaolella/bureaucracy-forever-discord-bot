# 001 — Onboard repo to Maestro standard

## Ask
Bring this repo's agent instructions in line with the Maestro standard: one
`AGENTS.md` with real content, `CLAUDE.md` as a one-line pointer, `.claude/`
removed since Maestro now supplies the git workflow, ship, start-work, review
and git guard behavior generically.

## Starting point
All instructions live in `CLAUDE.md` plus `.claude/` (rules, skills, agents,
hooks, settings.json). An open PR (#11) had already rewritten `CLAUDE.md` with
richer repo-specific content (stack, checks, layout, deploy notes) for the old
per-user `~/.claude` setup, but Robert doesn't use that setup anymore — fold in
what still applies, drop what points at `~/.claude`. Two old one-off Claude Code
handoff prompts (`CLAUDE_CODE_PROMPT.md`, `LFG_UPDATE_PROMPT.md`) are obsolete;
Robert confirmed removing them. `SYNC-SPEC.md` is a shared contract with the
website repo — do not touch it.

## Done means
- `AGENTS.md` has real, verified repo-specific content (Stack, Checks, Run
  locally, Layout, Deploy, Watch out for, Reference material).
- `CLAUDE.md` is the single line `@AGENTS.md`.
- `.claude/` is deleted.
- `.githooks/pre-commit` and `.githooks/pre-push` point at
  `/git/maestro/docs/git-workflow.md` instead of `.claude/rules/git-workflow.md`.
- The two obsolete prompt files are deleted.
- Every command in the Checks section has been run and proven (or failure
  recorded in `backlog.md`).
- `work/` and `backlog.md` exist.
- PR opened (not merged); old PR #11 closed with a pointer to the new one.
