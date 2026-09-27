# Claude Code handoff — adding the LFG feature

Run Claude Code in the existing bot directory and paste everything below the line.

---

I'm adding a looking-for-group feature to the Discord bot already running in this
directory. This is an update to the existing container, not a second bot.

New and changed files are in ~/Downloads (adjust if I put them elsewhere):
- lfg.py — NEW file, drop it in
- bot.py — CHANGED (one added line that loads the lfg extension)
- Dockerfile — CHANGED (copies lfg.py, creates /data)
- docker-compose.yml — CHANGED (mounts ./data:/data for the SQLite database)
- .env.example — CHANGED (two new variables)

Please do this in order and check in with me where noted:

1. Back up first. Copy bot.py, Dockerfile and docker-compose.yml to *.bak in this
directory. Then show me a diff of each incoming file against the current one.
Stop and ask me before applying if any diff contains changes beyond what I
described above — I've been hand-editing bot.py and I don't want edits lost.

2. Apply the files once I confirm the diffs look right.

3. Create the data directory: mkdir -p data. This holds the LFG SQLite database.
Do not add it to git; check .gitignore covers it and add "data/" if not.

4. Add these two lines to .env (not .env.example), preserving everything already
in there. Leave LFG_CHANNEL_ID blank for now unless I give you a channel ID —
blank means /lfg works in any channel.
   LFG_CHANNEL_ID=
   LFG_DB_PATH=/data/lfg.db
Confirm .env is still chmod 600 and don't print the token.

5. Verify both files parse: python3 -m py_compile bot.py lfg.py

6. Rebuild and restart: docker compose up -d --build
Then check the logs. Success is "Logged in as <name>" followed by "Commands
synced." with no traceback. If the lfg extension fails to load you'll see an
ExtensionError in the log — report it to me rather than trying fixes.

7. Confirm the volume actually mounted: docker compose exec bot ls -la /data
It'll be empty until someone runs /lfg, but the directory must exist and be
writable by uid 10001.

8. Tell me: container status, whether the volume is mounted, and remind me to run
/lfg in Discord to test. The dungeon field uses autocomplete, so I should type a
few letters rather than expecting a dropdown of all 35.

Constraints:
- Don't change the logic in bot.py or lfg.py. If you spot a bug, tell me and wait.
- Don't put the token anywhere except .env, and never echo it.
- Don't expose ports. Don't commit to git.
- The ./data directory is now real state — never delete or recreate it without
  asking me first.
