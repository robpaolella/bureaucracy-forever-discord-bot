# WoW Forever — class role bot

A single-purpose Discord bot. Posts one persistent message with four buttons that
let members pick a main class, manage alts, and set group-finder roles.

Stateless: every bit of state lives in Discord roles. No database, no volumes,
no inbound ports. If the container dies, nothing is lost.

---

## What it does

| Button | Effect |
| --- | --- |
| Set my main | Grants `<Class> Main` + `<Class>`. Removes the previous `Main` role, keeps the old class tag. |
| Manage my alts | Multi-select of the other eight classes. Unticking removes the tag. |
| Group finder role | Tank / Healer / DPS. |
| What do I have? | Private summary. |

All responses are ephemeral — the channel stays clean.

Admin commands (require Manage Server):

- `/rolecheck` — verifies every role exists and the bot outranks all of them. **Run this first.**
- `/rolepanel` — posts the panel in the current channel. Run once.
- `/emojidump` — lists custom emoji as paste-ready `<:name:id>` strings.

---

## Required roles

Exactly these names, case-sensitive:

```
Druid  Hunter  Mage  Paladin  Priest  Rogue  Shaman  Warlock  Warrior
Druid Main  Hunter Main  Mage Main  Paladin Main  Priest Main
Rogue Main  Shaman Main  Warlock Main  Warrior Main
Tank  Healer  DPS
```

Change `CLASSES`, `MAIN_SUFFIX`, or `GROUP_ROLES` at the top of `bot.py` if yours differ.

**Order matters.** Top to bottom: `Administrator` (no color) -> `Officer` -> bot's own
role -> all `X Main` roles (colored) -> `Guild Member` -> `Guest` -> `Tank`/`Healer`/`DPS`
-> the nine class roles (no color).

The bot's role must sit **above** every role it assigns. This is the single most
common failure and it fails silently. `/rolecheck` catches it.

---

## Discord setup

1. https://discord.com/developers/applications -> **New Application**.
2. **Bot** tab -> **Reset Token** -> copy. This is `DISCORD_TOKEN`.
3. Privileged Gateway Intents: leave **all off**. This bot needs none.
4. **OAuth2 -> URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Permissions: `View Channels`, `Send Messages`, `Embed Links`, `Manage Roles`
   - Or use this permission integer directly: `268454912`
5. Open the generated URL, add to your server.
6. Server Settings -> Roles -> drag the bot's role just below `Officer`.

Get your server ID: Discord Settings -> Advanced -> Developer Mode on, then
right-click the server icon -> Copy Server ID.

---

## Deploy on Debian

Assumes Debian 12 or 13.

### 1. Install Docker (skip if present)

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and back in for the group change to apply.

### 2. Configure

```bash
cd /opt/wow-roles-bot          # or wherever you put the files
cp .env.example .env
chmod 600 .env
nano .env                      # paste token and guild ID
```

### 3. Run

```bash
docker compose up -d --build
docker compose logs -f
```

You want to see `Logged in as <BotName>` and `Commands synced.`
Ctrl-C stops following logs; the container keeps running.

### 4. Finish in Discord

Run `/rolecheck` in any channel. Fix whatever it reports. Then run `/rolepanel`
in your role-selection channel. Done — the panel survives restarts.

---

## Operations

```bash
docker compose logs -f              # follow logs
docker compose restart              # restart
docker compose down                 # stop and remove
docker compose up -d --build        # rebuild after editing bot.py
```

Logs are capped at 3 x 10 MB. `restart: unless-stopped` means it survives reboots
once Docker's own service is enabled (`sudo systemctl enable docker`).

### Editing the panel text or emoji

Edit `bot.py`, then `docker compose up -d --build`. The old panel message keeps
working (button IDs are unchanged) but still shows the old embed — delete it and
re-run `/rolepanel` to refresh the wording.

---

## Custom emoji

Custom emoji are free — no boost needed. Two options:

- **Server emoji**: Server Settings -> Emoji -> Upload. 50 static slots at boost
  level 0, plus a separate 50 animated.
- **Application emoji**: Developer Portal -> your app -> Emojis. Up to 2000, costs
  no server slots, and only your bot can use them. Better fit for panel decoration.

Either way: run `/emojidump`, copy the strings, paste them into `CLASS_EMOJI` and
`GROUP_EMOJI` in `bot.py`, rebuild.

Images: 128x128 square, under 256 KB, PNG or WebP.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Slash commands don't appear | `GUILD_ID` unset (global sync takes ~1h), or missing `applications.commands` scope — re-invite. |
| "I couldn't change your roles" | Bot's role is below the class roles, or Manage Roles is off. |
| `/rolecheck` lists missing roles | Name mismatch — check spelling and capitalization exactly. |
| `LoginFailure: Improper token` | Token wrong or regenerated. Reset it in the portal, update `.env`, restart. |
| Container restart-loops | `docker compose logs` — almost always a bad token. |
| Everyone shows the same color | A colored role sits above the `Main` roles. Check `Guild Member`. |

---

## Security notes

- `.env` is gitignored and should be `chmod 600`. The token is a full bot credential.
- The container runs as a non-root user (uid 10001).
- No ports are exposed. The bot makes only outbound connections to Discord.
- If the token ever leaks, reset it in the Developer Portal immediately.

## Discord ↔ web sync

`sync/` polls the site's outbox (`GET /api/bot/outbox`), runs each job in order per
application or raid, and acks it; it calls `POST /api/bot/tick` every minute so the site can
generate, post, remind, lock and close raids. Clicks in Discord only call the site; what
members see comes from the jobs the site queues. The contract is `SYNC-SPEC.md`, kept
identical in both repos.

Applications are web-only: `/recruitpanel` posts two link buttons to the site's form. Each
web application becomes a post in the `#applications` forum with Accept / Decline; officer
messages in the thread become private notes on the web (needs the Message Content intent).

Requires the variables in the sync block of `.env.example`. Deploy after a merge with
`docker compose up -d --build` from this folder.
