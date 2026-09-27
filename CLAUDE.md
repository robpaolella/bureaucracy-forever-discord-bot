# Bureaucracy — Discord bot

The guild's Discord bot (discord.py, Docker). Cogs: `bot.py` (class / main / group-finder role
panel), `lfg.py` (5-man LFG posts), `recruit.py` (applications with Accept / Decline). The
site at https://www.bureauguild.com lives in `~/git/bureaucracy-forever`; the two talk over
signed HTTP (see that repo's `lib/bot-auth.ts` and `/api/bot/*`).

## Running

The bot runs on this server from this folder: `docker compose up -d --build`. The compose
project is pinned to `bureauforever-classroleselector` so it keeps managing the existing
`wow-roles-bot` container. `.env` holds the token and channel ids and is never committed;
`data/` holds the SQLite files and is never committed either. `.env.example` lists the names.

## Development workflow

Follow @.claude/rules/git-workflow.md on every change. It applies even when I have not
mentioned git.

- Never commit to `main`. Branch as `<type>/<short-kebab-description>`.
- Conventional Commits. One logical change per commit. Every commit passes `make check`.
- Before a PR: `make check`, `make test`, `docker compose build`, then the `code-reviewer`
  subagent.
- Open PRs with `gh pr create`. With green checks and nothing blocking from the reviewer,
  squash-merge it yourself, delete the branch, pull `main`. Bring me decisions in plain
  language, not git.
- Deploying is a separate step from merging: after a merge, `docker compose up -d --build`
  from this folder restarts the bot on the new code. Say when a merge needs a deploy.

`/start-work` begins a task and `/ship` ends it. A PreToolUse hook blocks commits on `main`,
force pushes and destructive history operations; if it blocks you, follow the message.

Never print the contents of `.env`; read variable names only.
