# Backlog

- Checks (`make check`, `make test`) couldn't be run directly in this onboarding
  sandbox: no pip/venv and no Docker available. CI runs them on Python 3.12 on
  every push/PR and has been green on main (verified via `gh run list`), so
  this is a sandbox gap, not a repo defect.
- `SYNC-SPEC.md:428` references `.claude/hooks/test-git-guard.sh`, a file that never
  existed (the real hook was `git-guard.sh`, now removed). Pre-existing drift, not
  touched here since SYNC-SPEC.md is a shared contract with the site repo — fix in
  both repos together.
