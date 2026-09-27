---
name: ship
description: Finish a unit of work — verify it, review it, commit it, and open a pull request. Use when a task is complete and ready for review. Runs checks and the code-reviewer before anything is pushed.
---

# Ship

## Current state

- Branch: !`git rev-parse --abbrev-ref HEAD`
- Uncommitted: !`git status --short`
- Commits on this branch: !`git log --oneline main..HEAD 2>/dev/null | head -20`

## Steps

Do these in order. If a step fails, fix it and re-run that step — do not skip
ahead.

1. **Refuse to proceed on `main`.** If the branch above is `main` or `master`,
   stop. Create a branch, move the work onto it, and continue.

2. **Run the checks.** In order, stopping at the first failure:

   ```
   make check
   make test
   docker compose build
   ```

   `make check` runs ruff and byte-compiles every module; `make test` runs
   pytest when a `tests/` folder exists. If a target is missing, say so and
   skip it — do not invent a substitute command.

3. **Verify it actually works.** For any change a member would see in Discord,
   run the bot against the test guild (or `docker compose up --build` locally
   with a test token) and exercise the command or button. Reading the code
   back is not verification. Report what you checked.

4. **Commit what is outstanding.** Group the remaining changes into logical
   commits using the Conventional Commit format in
   `.claude/rules/git-workflow.md`. Never one catch-all commit.

5. **Review.** Hand the branch diff to the `code-reviewer` subagent. Fix
   everything it marks Blocking. For Worth-fixing items, either fix them or list
   them in the PR body as deliberate follow-ups. Do not open the PR with
   unaddressed blocking findings.

6. **Push and open the PR:**

   ```
   git push -u origin HEAD
   gh pr create --title "<conventional commit subject>" --body "..."
   ```

   The body covers **What**, **Why**, **How to verify** (exact routes and
   widths), and **Risks / follow-ups**.

7. **Merge.** Only if step 2 was green, step 5 left nothing blocking, and no
   real decision is open. If the PR has remote checks, wait for them:
   `gh pr checks --watch` must end with every check passing. Then:

   ```
   gh pr merge --squash --delete-branch --subject "<PR title>"
   git switch main && git pull --ff-only
   ```

   If anything above is not true, leave the PR open and go to step 8.

8. **Report back** with what actually happened: merged (commit on `main`) or
   left open and why, what the checks said, and any decision you need from me,
   in plain language.
