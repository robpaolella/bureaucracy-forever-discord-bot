# Bureaucracy — Discord bot

The Bureaucracy guild's Discord bot: class/main/group-finder role panel, 5-man LFG posts,
recruitment applications, and the Discord side of the site sync. It runs in Docker on
this server. The site (https://www.bureauguild.com) lives in `~/git/bureaucracy-forever`.

## Stack
Python 3.12, discord.py 2.x, SQLite files under `data/`, Docker Compose. ruff and pytest
(`requirements-dev.txt`). CI runs `make check` and `make test` on Python 3.12.

## Checks
Run in this order before any PR. `/ship` reads this section.
```
make check              # ruff, then byte-compile every module
make test               # pytest
docker compose build
```
They need Python 3.12 with `requirements-dev.txt` installed. This host only has Python 3.9
and no `python` binary, so run the first two in a container:
```
docker run --rm -v "$PWD":/app -w /app python:3.12-slim sh -c \
  'apt-get update -qq && apt-get install -y -qq make >/dev/null && pip install -q -r requirements-dev.txt && make check && make test'
```

## Setup after clone
```
git config core.hooksPath .githooks
```

## Run locally
```
cp .env.example .env    # token, guild and channel ids; chmod 600
docker compose up -d --build
```
The compose project is pinned to `bureauforever-classroleselector`, so it keeps managing
the existing `wow-roles-bot` container. Leave `SITE_API_URL` or `BOT_SHARED_SECRET` blank
to run without the sync.

## Layout
- `bot.py` — entry point, role panel, `/rolepanel`, `/rolecheck`, `/emojidump`; loads the extensions below.
- `lfg.py` — `/lfg` posts and signup buttons.
- `recruit.py` — `/recruitpanel`, applications with Accept / Decline.
- `sync/` — Discord ↔ site sync: signed API client (`api.py`), outbox runner and `tick`, renderers for applications and raids.
- `tests/` — pytest suites.
- `data/` — SQLite files, mounted at `/data`; never committed.

## Deploy
This server, from this folder: `docker compose up -d --build`. Merging a PR doesn't
deploy. Say when a merge needs one. `.env` holds the token, channel/role ids and
`BOT_SHARED_SECRET` (same value as the site's on Vercel); `.env.example` lists the names.

## Verifying a change
For anything a member sees in Discord, run the bot against the test guild (or
`docker compose up --build` locally with a test token) and exercise the command or button.
PR "How to verify" lists the exact slash commands, buttons or messages to try and what
should happen.

## Watch out for
- Never print the contents of `.env`. Read variable names only.
- The bot's role must sit above every role it assigns, or role changes fail silently. `/rolecheck` catches it.
- The sync never touches the Officer role. It reads it only (`ROLE_OFFICER_ID`).

## Reference material
- `SYNC-SPEC.md` — shared contract with the site: auth, site API, outbox jobs, rendering. The same file lives in both repos; change both in the same PR.
- `README.md` — Discord setup, required role names and order.
