---
name: start-work
description: Start a new unit of work on a clean, correctly named branch. Use at the beginning of any task that will change files, and whenever the current branch's work is already merged or belongs to a different concern.
---

# Start work

## Current state

- Branch: !`git rev-parse --abbrev-ref HEAD`
- Working tree: !`git status --short`
- Behind/ahead of origin: !`git status -sb | head -1`

## Steps

1. **Check the working tree.** If there are uncommitted changes, stop and ask
   whether to commit them on the current branch, stash them, or discard them.
   Never carry someone else's uncommitted work onto a new branch silently.

2. **Confirm the task is one unit of work.** If what I asked for splits cleanly
   into two unrelated changes, say so and propose the split before branching.

3. **Update main and branch from it:**

   ```
   git switch main
   git pull --ff-only
   git switch -c <type>/<short-kebab-description>
   ```

   Type is one of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`,
   `build`. See `.claude/rules/git-workflow.md` for naming.

4. **State the plan** in two or three lines before editing anything: what you
   will change, roughly which files, and what "done" looks like. Wait for my
   go-ahead if the plan touches migrations, auth, or anything under
   `design-handover/`.

5. Work in small commits as you go. Do not wait until the end.
