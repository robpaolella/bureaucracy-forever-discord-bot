# Handoff prompt for Claude Code

Copy everything below the line into Claude Code, running in the directory that
contains this package.

---

I have a Discord bot package in this directory that I want deployed as a Docker
container on this Debian server. Files present should be: `bot.py`,
`requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`,
`.gitignore`, `README.md`.

Read `README.md` first — it documents what the bot does, the required Discord
roles, and the intended deployment. Follow it rather than improvising.

Please do the following, checking in with me where noted:

**1. Verify the package.**
Confirm all files are present and `bot.py` is syntactically valid
(`python3 -m py_compile bot.py`). Report anything missing before continuing.

**2. Check the host.**
Report whether Docker Engine and the Compose plugin are installed
(`docker --version`, `docker compose version`) and whether my user is in the
`docker` group. If Docker is missing, show me the install commands from the
README and ask before running anything with sudo. Don't install Docker silently.

**3. Set up configuration.**
Create `.env` from `.env.example` and `chmod 600` it.
Do NOT ask me to paste my bot token into the chat — it's a live credential and I
don't want it in a transcript. Instead, open `.env` in an editor for me, or give
me a single command to write it myself. Confirm afterwards that `.env` contains
both `DISCORD_TOKEN` and `GUILD_ID` without printing their values.

**4. Build and run.**
`docker compose up -d --build`, then check the logs. Success looks like
`Logged in as <name>` followed by `Commands synced.`
If the container restart-loops, diagnose from the logs — a bad token is the usual
cause — and tell me what you found instead of retrying blindly.

**5. Confirm resilience.**
Verify `restart: unless-stopped` is in effect and that the Docker service is
enabled at boot (`systemctl is-enabled docker`). Tell me if it isn't.

**6. Report back.**
Give me a short summary: container name and status, where the files live, the log
command, and the exact next steps I need to do inside Discord (run `/rolecheck`,
fix anything it flags, then `/rolepanel` in my role-selection channel).

Constraints:
- Don't change the logic in `bot.py`. If you spot a real bug, tell me and wait.
- Don't commit anything to git or create a repo unless I ask.
- Don't put the token in any file other than `.env`, and never echo it.
- Don't expose any ports. This bot only makes outbound connections.
