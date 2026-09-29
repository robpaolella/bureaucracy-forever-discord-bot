"""
Looking-for-group for WoW Forever.

/lfg posts a recruitment message, opens a thread under it, and tracks the roster
in SQLite so signups survive restarts. Posts auto-expire after EXPIRE_HOURS.

Loaded by bot.py as an extension.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("wowroles.lfg")

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

DB_PATH = os.environ.get("LFG_DB_PATH", "/data/lfg.db")
LFG_CHANNEL_ID = int(os.environ.get("LFG_CHANNEL_ID", "0") or 0)
EXPIRE_HOURS = 3
FILLED_IDLE_MINUTES = 60

# Slots per role in a standard 5-man.
ROLE_SLOTS = {"tank": 1, "healer": 1, "dps": 3}
ROLE_LABEL = {"tank": "Tank", "healer": "Healer", "dps": "DPS"}

# Discord role names used for pings. Must match your server exactly.
ROLE_NAMES = {"tank": "Tank", "healer": "Healer", "dps": "DPS"}

ROLE_EMOJI = {
    "tank": "<:tank:1549151484145565777>",
    "healer": "<:healer:1549151450859442318>",
    "dps": "<:dps:1549151418617692171>",
}

LIVE_EMOJI = "<a:dungeon_finder:1551239985854812231>"

# Full dungeon list. Edit freely — autocomplete reads straight from here.
DUNGEONS: list[str] = [
    "Alcaz Prison",
    "Blackfathom Deeps",
    "Blackmaw Hold",
    "Blackrock Depths",
    "City of Dalaran",
    "Dire Maul: East Wing",
    "Dire Maul: North Wing",
    "Dire Maul: West Wing",
    "Excavation Site: Wetlands",
    "Gnomeregan",
    "Krol'dok Stronghold",
    "Lower Blackrock Spire",
    "Maraudon",
    "Ragefire Chasm",
    "Razorfen Downs",
    "Razorfen Kraul",
    "Ruins of Lordaeron",
    "Scarlet Monastery: Armory",
    "Scarlet Monastery: Cathedral",
    "Scarlet Monastery: Graveyard",
    "Scarlet Monastery: Library",
    "Scholomance",
    "Shadowfang Keep",
    "Shaper’s Terrace",
    "Stratholme: Living Side",
    "Stratholme: Undead Side",
    "The Deadmines",
    "The Drowned City",
    "The Hall of Thanes",
    "The Stockade",
    "The Temple of Atal'Hakkar",
    "Uldaman",
    "Upper Blackrock Spire",
    "Wailing Caverns",
    "Zul'Farrak",
]

# Optional level brackets, shown in autocomplete when filled in. Blizzard hasn't
# published ranges for the new dungeons yet, so this starts empty on purpose.
# Add entries as you learn them, e.g.  "Gnomeregan": "29-38",
DUNGEON_LEVELS: dict[str, str] = {}

# Common shorthand -> dungeon name, so "smc" or "ubrs" finds the right entry.
ALIASES: dict[str, str] = {
    "brd": "Blackrock Depths",
    "lbrs": "Lower Blackrock Spire",
    "ubrs": "Upper Blackrock Spire",
    "dm": "Dire Maul",
    "smc": "Scarlet Monastery: Cathedral",
    "sml": "Scarlet Monastery: Library",
    "smg": "Scarlet Monastery: Graveyard",
    "sma": "Scarlet Monastery: Armory",
    "st": "The Temple of Atal'Hakkar",
    "zf": "Zul'Farrak",
    "bfd": "Blackfathom Deeps",
    "sfk": "Shadowfang Keep",
    "rfc": "Ragefire Chasm",
    "rfd": "Razorfen Downs",
    "rfk": "Razorfen Kraul",
    "wc": "Wailing Caverns",
    "vc": "The Deadmines",
    "strat": "Stratholme",
    "scholo": "Scholomance",
}

DUNGEON_NAMES = set(DUNGEONS)


def _norm(text: str) -> str:
    """Fold case and apostrophe style so search works whichever quote is typed."""
    return text.lower().replace("\u2019", "'").replace("'", "").replace(":", "")


# --------------------------------------------------------------------------
# STORAGE
# --------------------------------------------------------------------------

class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS posts (
                message_id       INTEGER PRIMARY KEY,
                guild_id         INTEGER NOT NULL,
                channel_id       INTEGER NOT NULL,
                thread_id        INTEGER,
                panel_message_id INTEGER,
                owner_id         INTEGER NOT NULL,
                dungeon          TEXT NOT NULL,
                notes            TEXT,
                status           TEXT NOT NULL DEFAULT 'open',
                created_at       INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signups (
                message_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                role       TEXT NOT NULL,
                label      TEXT,
                PRIMARY KEY (message_id, user_id)
            );
            """
        )
        self.db.commit()

        cols = {row["name"] for row in self.db.execute("PRAGMA table_info(posts)")}
        if "last_activity" not in cols:
            self.db.execute(
                "ALTER TABLE posts ADD COLUMN last_activity INTEGER NOT NULL DEFAULT 0"
            )
            self.db.execute("UPDATE posts SET last_activity = created_at")
            self.db.commit()
        if "panel_message_id" not in cols:
            self.db.execute("ALTER TABLE posts ADD COLUMN panel_message_id INTEGER")
            self.db.commit()

    def create_post(self, **kw) -> None:
        self.db.execute(
            "INSERT INTO posts (message_id, guild_id, channel_id, thread_id, owner_id,"
            " dungeon, notes, status, created_at, last_activity) VALUES (:message_id,"
            " :guild_id, :channel_id, :thread_id, :owner_id, :dungeon, :notes, 'open',"
            " :created_at, :created_at)",
            kw,
        )
        self.db.commit()

    def post(self, mid: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM posts WHERE message_id=?", (mid,)).fetchone()

    def set_thread(self, mid: int, tid: int) -> None:
        self.db.execute("UPDATE posts SET thread_id=? WHERE message_id=?", (tid, mid))
        self.db.commit()

    def set_panel_message(self, mid: int, panel_mid: int) -> None:
        self.db.execute(
            "UPDATE posts SET panel_message_id=? WHERE message_id=?", (panel_mid, mid)
        )
        self.db.commit()

    def set_status(self, mid: int, status: str) -> None:
        self.db.execute("UPDATE posts SET status=? WHERE message_id=?", (status, mid))
        self.db.commit()

    def signups(self, mid: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM signups WHERE message_id=? ORDER BY rowid", (mid,)
        ).fetchall()

    def add_signup(self, mid: int, uid: int, role: str, label: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO signups (message_id, user_id, role, label)"
            " VALUES (?,?,?,?)",
            (mid, uid, role, label),
        )
        self.db.commit()

    def set_role(self, mid: int, uid: int, role: str) -> None:
        """Change a signup's role in place, keeping its place in the roster order."""
        self.db.execute(
            "UPDATE signups SET role=? WHERE message_id=? AND user_id=?", (role, mid, uid)
        )
        self.db.commit()

    def active_signups(self, uid: int, exclude_mid: int) -> list[sqlite3.Row]:
        """The user's signups in other groups that are still running (open or filled)."""
        return self.db.execute(
            "SELECT p.message_id, p.dungeon, p.owner_id, s.role FROM signups s"
            " JOIN posts p ON p.message_id = s.message_id"
            " WHERE s.user_id=? AND s.message_id<>? AND p.status IN ('open','filled')"
            " ORDER BY p.created_at",
            (uid, exclude_mid),
        ).fetchall()

    def remove_signup(self, mid: int, uid: int) -> bool:
        cur = self.db.execute(
            "DELETE FROM signups WHERE message_id=? AND user_id=?", (mid, uid)
        )
        self.db.commit()
        return cur.rowcount > 0

    def next_placeholder_id(self, mid: int) -> int:
        row = self.db.execute(
            "SELECT MIN(user_id) AS m FROM signups WHERE message_id=? AND user_id<0", (mid,)
        ).fetchone()
        return (row["m"] - 1) if row and row["m"] is not None else -1

    def stale_open(self, cutoff: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM posts WHERE status='open' AND created_at < ?", (cutoff,)
        ).fetchall()

    def open_posts(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM posts WHERE status='open'").fetchall()

    def filled_idle(self, cutoff: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM posts WHERE status='filled' AND last_activity < ?", (cutoff,)
        ).fetchall()

    def touch_activity(self, mid: int, ts: int) -> None:
        self.db.execute("UPDATE posts SET last_activity=? WHERE message_id=?", (ts, mid))
        self.db.commit()

    def touch_activity_by_thread(self, thread_id: int, ts: int) -> None:
        self.db.execute("UPDATE posts SET last_activity=? WHERE thread_id=?", (ts, thread_id))
        self.db.commit()


# --------------------------------------------------------------------------
# RENDERING
# --------------------------------------------------------------------------

def role_mention(guild: discord.Guild, role: str) -> str:
    r = discord.utils.get(guild.roles, name=ROLE_NAMES[role])
    return r.mention if r else f"**{ROLE_LABEL[role]}**"


def counts(rows) -> dict[str, int]:
    out = {"tank": 0, "healer": 0, "dps": 0}
    for r in rows:
        out[r["role"]] = out.get(r["role"], 0) + 1
    return out


def missing(rows) -> dict[str, int]:
    have = counts(rows)
    return {k: max(0, ROLE_SLOTS[k] - have.get(k, 0)) for k in ROLE_SLOTS}


def signup_plan(rows, uid: int, role: str) -> tuple[str, str | None]:
    """What a click on "Sign up as <role>" means for this user, and their current role.

    same: already in as that role · change: in as another role, that slot is open
    full: that slot is taken · join: not in the group, slot open
    """
    current = next((r["role"] for r in rows if r["user_id"] == uid), None)
    if current == role:
        return "same", current
    # Moving out of your own slot frees it, so only other people count against the target.
    others = [r for r in rows if r["user_id"] != uid]
    if missing(others)[role] <= 0:
        return "full", current
    return ("change" if current else "join"), current


def needs_phrase(guild: discord.Guild, miss: dict[str, int]) -> str:
    parts = []
    for role in ("tank", "healer", "dps"):
        n = miss[role]
        if n <= 0:
            continue
        m = role_mention(guild, role)
        parts.append(f"a {m}" if n == 1 else f"**({n})** {m}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    lead = ", ".join(parts[:-1])
    return f"{lead} and {parts[-1]}"


def roster_line(guild: discord.Guild, rows) -> str:
    by_role: dict[str, list[str]] = {"tank": [], "healer": [], "dps": []}
    for r in rows:
        if r["user_id"] < 0:
            by_role[r["role"]].append(f"*{r['label'] or 'reserved'}*")
        else:
            by_role[r["role"]].append(f"<@{r['user_id']}>")

    lines = []
    for role in ("tank", "healer", "dps"):
        filled = by_role[role]
        empty = ROLE_SLOTS[role] - len(filled)
        names = filled + ["*open*"] * empty
        lines.append(f"{ROLE_EMOJI[role]} **{ROLE_LABEL[role]}**  " + " \u00b7 ".join(names))
    return "\n".join(lines)


def render(guild: discord.Guild, post: sqlite3.Row, rows) -> str:
    dungeon = post["dungeon"]
    owner = f"<@{post['owner_id']}>"
    status = post["status"]

    if status in ("filled", "closed"):
        marker = "\u2705"
        status_line = "Group is full. Good luck in there."
    elif status == "cancelled":
        marker = "\u274C"
        status_line = f"Cancelled by {owner}."
    elif status == "expired":
        marker = "\U0001F552"
        status_line = f"Expired after {EXPIRE_HOURS} hours with no fill."
    else:
        marker = LIVE_EMOJI
        miss = missing(rows)
        if sum(miss.values()) == 0:
            status_line = "Group is full."
        else:
            status_line = f"{owner} needs {needs_phrase(guild, miss)}"

    lines = [f"{marker} **{dungeon}**", status_line, roster_line(guild, rows)]
    if post["notes"]:
        lines.append(f"> {post['notes']}")
    return "\n".join(lines)


def post_channel(interaction: discord.Interaction) -> discord.abc.GuildChannel | None:
    """Where an /lfg post goes: #looking-for-group when LFG_CHANNEL_ID is set, else where it was run."""
    if not LFG_CHANNEL_ID:
        return interaction.channel
    channel = interaction.guild.get_channel(LFG_CHANNEL_ID)
    return channel if isinstance(channel, discord.TextChannel) else None


class LfgButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"wf:lfg:(?P<action>tank|healer|dps|leave|manage):(?P<mid>\d+)",
):
    def __init__(self, action: str, mid: int, disabled: bool = False):
        self.action = action
        self.mid = mid

        if action == "leave":
            btn = discord.ui.Button(
                label="Leave", style=discord.ButtonStyle.danger,
                custom_id=f"wf:lfg:leave:{mid}", disabled=disabled,
            )
        elif action == "manage":
            btn = discord.ui.Button(
                label="Manage", style=discord.ButtonStyle.secondary,
                custom_id=f"wf:lfg:manage:{mid}", disabled=disabled,
            )
        else:
            btn = discord.ui.Button(
                label=f"Sign up as {ROLE_LABEL[action]}",
                style=discord.ButtonStyle.success,
                custom_id=f"wf:lfg:{action}:{mid}", disabled=disabled,
            )
        super().__init__(btn)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["mid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Lfg = interaction.client.get_cog("Lfg")  # type: ignore[assignment]
        await cog.handle_button(interaction, self.action, self.mid)


def build_view(guild: discord.Guild, post: sqlite3.Row, rows) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    mid = post["message_id"]
    if post["status"] != "open":
        return v
    miss = missing(rows)
    for role in ("tank", "healer", "dps"):
        if miss[role] > 0:
            v.add_item(LfgButton(role, mid))
    v.add_item(LfgButton("leave", mid))
    v.add_item(LfgButton("manage", mid))
    return v


# --------------------------------------------------------------------------
# SETUP FLOW (ephemeral)
# --------------------------------------------------------------------------

class NamedRoleSelect(discord.ui.UserSelect):
    """Tag a Discord member who's already filling this role slot."""

    def __init__(self, role: str, max_values: int):
        self.role = role
        super().__init__(
            placeholder=f"Already have a {ROLE_LABEL[role]}? Pick them",
            min_values=0,
            max_values=max_values,
            row=("tank", "healer", "dps").index(role),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.named[self.role] = list(self.values)  # type: ignore[attr-defined]
        await interaction.response.defer()


class HaveSelect(discord.ui.Select):
    """Slots filled by someone who isn't in this Discord at all."""

    def __init__(self, own_role: str):
        opts = []
        for role in ("tank", "healer", "dps"):
            room = ROLE_SLOTS[role] - (1 if role == own_role else 0)
            for i in range(room):
                suffix = f" #{i + 1}" if ROLE_SLOTS[role] > 1 else ""
                opts.append(
                    discord.SelectOption(
                        label=f"{ROLE_LABEL[role]}{suffix}",
                        value=f"{role}:{i}",
                    )
                )
        super().__init__(
            placeholder="Slots filled by someone not in Discord (optional)",
            min_values=0,
            max_values=len(opts),
            options=opts,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.have = list(self.values)  # type: ignore[attr-defined]
        await interaction.response.defer()


class SetupView(discord.ui.View):
    def __init__(self, cog: Lfg, dungeon: str, own_role: str, notes: str | None):
        super().__init__(timeout=300)
        self.cog = cog
        self.dungeon = dungeon
        self.own_role = own_role
        self.notes = notes
        self.named: dict[str, list] = {"tank": [], "healer": [], "dps": []}
        self.have: list[str] = []

        for role in ("tank", "healer", "dps"):
            room = ROLE_SLOTS[role] - (1 if role == own_role else 0)
            if room >= 1:
                self.add_item(NamedRoleSelect(role, room))

        self.add_item(HaveSelect(own_role))

    @discord.ui.button(label="Post it", style=discord.ButtonStyle.success, row=4)
    async def post_it(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.create_post(interaction, self)

    @discord.ui.button(label="Never mind", style=discord.ButtonStyle.secondary, row=4)
    async def nope(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


def resolve_named(setup: SetupView, poster_id: int) -> tuple[dict[str, list], list[str]]:
    """Drop the poster and any bots silently; dedupe across roles, keeping the
    first pick and noting every one dropped for that reason."""
    named: dict[str, list] = {"tank": [], "healer": [], "dps": []}
    seen: set[int] = set()
    dupe_notes: list[str] = []

    for role in ("tank", "healer", "dps"):
        for member in setup.named.get(role, []):
            if member.id == poster_id or member.bot:
                continue
            if member.id in seen:
                dupe_notes.append(
                    f"{member.mention} was picked for more than one role \u2014 "
                    "kept their first pick."
                )
                continue
            seen.add(member.id)
            named[role].append(member)

    return named, dupe_notes


def _join_mentions(ids: list[int]) -> str:
    parts = [f"<@{i}>" for i in ids]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


# --------------------------------------------------------------------------
# MANAGE PANEL (ephemeral, owner/officer only)
# --------------------------------------------------------------------------

class RemoveSelect(discord.ui.Select):
    def __init__(self, cog: Lfg, mid: int, rows, guild: discord.Guild):
        self.cog, self.mid = cog, mid
        opts = []
        for r in rows:
            if r["user_id"] < 0:
                name = r["label"] or "reserved slot"
            else:
                mem = guild.get_member(r["user_id"])
                name = mem.display_name if mem else f"user {r['user_id']}"
            opts.append(
                discord.SelectOption(
                    label=f"{ROLE_LABEL[r['role']]} — {name}"[:100],
                    value=str(r["user_id"]),
                )
            )
        super().__init__(placeholder="Remove someone from the roster...",
                         min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction) -> None:
        uid = int(self.values[0])
        self.cog.store.remove_signup(self.mid, uid)
        who = f"*{'a reserved slot'}*" if uid < 0 else f"<@{uid}>"
        await self.cog.refresh(interaction.guild, self.mid)
        await self.cog.say_in_thread(self.mid, f"{who} was removed from the group.")
        await interaction.response.edit_message(content="Removed.", view=None)


class AddSelect(discord.ui.Select):
    def __init__(self, cog: Lfg, mid: int, miss: dict[str, int]):
        self.cog, self.mid = cog, mid
        opts = [
            discord.SelectOption(label=f"Add a {ROLE_LABEL[r]}", value=r)
            for r in ("tank", "healer", "dps") if miss[r] > 0
        ]
        super().__init__(placeholder="Mark a slot filled (someone joined in game)...",
                         min_values=1, max_values=1, options=opts or
                         [discord.SelectOption(label="Group is full", value="none")])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await interaction.response.edit_message(content="Nothing to add.", view=None)
        role = self.values[0]
        pid = self.cog.store.next_placeholder_id(self.mid)
        self.cog.store.add_signup(self.mid, pid, role, label="filled in game")
        await self.cog.refresh(interaction.guild, self.mid)
        await self.cog.say_in_thread(
            self.mid, f"A **{ROLE_LABEL[role]}** slot was filled in game."
        )
        await interaction.response.edit_message(content="Roster updated.", view=None)


class InGroupView(discord.ui.View):
    """Private reply to someone already in the group who clicked a role button."""

    def __init__(self, cog: Lfg, mid: int, role: str | None):
        super().__init__(timeout=300)
        self.cog = cog
        self.mid = mid
        self.role = role
        if role is None:
            self.remove_item(self.change)
        else:
            self.change.label = f"Change role to {ROLE_LABEL[role]}"

    @discord.ui.button(label="Change role", style=discord.ButtonStyle.success)
    async def change(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.change_role(interaction, self.mid, self.role)

    @discord.ui.button(label="Leave group", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.leave(interaction, self.mid, edit=True)


class SwitchGroupView(discord.ui.View):
    """Confirm before moving someone out of their other running group(s) into this one."""

    def __init__(self, cog: Lfg, mid: int, role: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.mid = mid
        self.role = role

    @discord.ui.button(label="Switch to this group", style=discord.ButtonStyle.success)
    async def switch(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.join(interaction, self.mid, self.role, edit=True, leave_others=True)

    @discord.ui.button(label="Stay where I am", style=discord.ButtonStyle.secondary)
    async def stay(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.reply(interaction, "No change. You're still in your other group.", edit=True)


def other_groups_phrase(others) -> str:
    """Name the other groups, e.g. **Deadmines** (Healer) and **Stockades** (DPS, you lead it)."""
    parts = [
        f"**{o['dungeon']}** ({ROLE_LABEL[o['role']]}{', you lead it' if o['leads'] else ''})"
        for o in others
    ]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


class ManageView(discord.ui.View):
    def __init__(self, cog: Lfg, mid: int, rows, guild: discord.Guild):
        super().__init__(timeout=180)
        self.cog, self.mid = cog, mid
        if rows:
            self.add_item(RemoveSelect(cog, mid, rows, guild))
        self.add_item(AddSelect(cog, mid, missing(rows)))

    @discord.ui.button(label="Group filled", style=discord.ButtonStyle.success, row=2)
    async def filled(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.close_post(interaction, self.mid, "filled")

    @discord.ui.button(label="Cancel group", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.close_post(interaction, self.mid, "cancelled")


# --------------------------------------------------------------------------
# COG
# --------------------------------------------------------------------------

class Lfg(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = Store(DB_PATH)
        self.expire_loop.start()

    async def cog_unload(self) -> None:
        self.expire_loop.cancel()

    # ---------- helpers ----------

    async def fetch_message(self, post: sqlite3.Row) -> discord.Message | None:
        channel = self.bot.get_channel(post["channel_id"])
        if channel is None:
            return None
        try:
            return await channel.fetch_message(post["message_id"])
        except discord.HTTPException:
            return None

    async def fetch_panel_message(self, post: sqlite3.Row) -> discord.Message | None:
        if not post["thread_id"] or not post["panel_message_id"]:
            return None
        thread = self.bot.get_channel(post["thread_id"])
        if thread is None:
            return None
        try:
            return await thread.fetch_message(post["panel_message_id"])
        except discord.HTTPException:
            return None

    async def refresh(self, guild: discord.Guild, mid: int) -> None:
        post = self.store.post(mid)
        if post is None:
            return
        rows = self.store.signups(mid)
        content = render(guild, post, rows)

        msg = await self.fetch_message(post)
        if msg is not None:
            await msg.edit(
                content=content,
                view=build_view(guild, post, rows),
                allowed_mentions=discord.AllowedMentions.none(),
            )

        panel = await self.fetch_panel_message(post)
        if panel is not None:
            await panel.edit(
                content=content,
                view=build_view(guild, post, rows),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def say_in_thread(self, mid: int, text: str) -> None:
        post = self.store.post(mid)
        if not post or not post["thread_id"]:
            return
        thread = self.bot.get_channel(post["thread_id"])
        if thread is None:
            return
        try:
            await thread.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Couldn't post in thread %s", post["thread_id"])
            return
        self.store.touch_activity(mid, int(time.time()))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not isinstance(message.channel, discord.Thread):
            return
        self.store.touch_activity_by_thread(message.channel.id, int(time.time()))

    async def shut_thread(self, mid: int) -> None:
        """Announce the lock, then archive and lock the thread. Notice must go out
        before archiving — posting into an already-archived thread un-archives it."""
        await self.say_in_thread(
            mid, "Thread closed and locked. Run `/lfg` if you need a new group."
        )
        post = self.store.post(mid)
        if post and post["thread_id"]:
            thread = self.bot.get_channel(post["thread_id"])
            if thread is not None:
                try:
                    await thread.edit(archived=True, locked=True)
                except discord.HTTPException:
                    pass

    # ---------- command ----------

    @app_commands.command(name="lfg", description="Recruit for a dungeon group.")
    @app_commands.guild_only()
    @app_commands.describe(
        dungeon="Which dungeon are you running?",
        role="What are you playing in this group?",
        notes="Optional: specific classes you need, e.g. warlock for summons.",
    )
    @app_commands.choices(
        role=[
            app_commands.Choice(name="Tank", value="tank"),
            app_commands.Choice(name="Healer", value="healer"),
            app_commands.Choice(name="DPS", value="dps"),
        ]
    )
    async def lfg(
        self,
        interaction: discord.Interaction,
        dungeon: str,
        role: app_commands.Choice[str],
        notes: str | None = None,
    ):
        if dungeon not in DUNGEON_NAMES:
            return await interaction.response.send_message(
                f"I don't know a dungeon called `{dungeon}`. Pick one from the list.",
                ephemeral=True,
            )
        view = SetupView(self, dungeon, role.value, notes)
        await interaction.response.send_message(
            f"**{dungeon}** \u2014 you're the {ROLE_LABEL[role.value]}. "
            "If anyone else is already in, mark them below, then post"
            + (f" in <#{LFG_CHANNEL_ID}>." if LFG_CHANNEL_ID else "."),
            view=view,
            ephemeral=True,
        )

    @lfg.autocomplete("dungeon")
    async def dungeon_autocomplete(self, interaction: discord.Interaction, current: str):
        cur = _norm(current.strip())
        expanded = _norm(ALIASES.get(cur, "")) if cur in ALIASES else ""

        hits = [
            n for n in DUNGEONS
            if not cur or cur in _norm(n) or (expanded and expanded in _norm(n))
        ]

        def label(n: str) -> str:
            lvl = DUNGEON_LEVELS.get(n)
            return f"{n}  ({lvl})" if lvl else n

        return [app_commands.Choice(name=label(n), value=n) for n in hits[:25]]

    # ---------- post creation ----------

    async def create_post(self, interaction: discord.Interaction, setup: SetupView) -> None:
        # /lfg runs anywhere; the post and its thread always go to #looking-for-group when it is set.
        channel = post_channel(interaction)
        if channel is None:
            return await interaction.response.edit_message(
                content=f"I can't post in <#{LFG_CHANNEL_ID}>: it's missing or not a text channel. "
                "Ask an admin to check `LFG_CHANNEL_ID`.",
                view=None,
            )
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.create_public_threads):
            return await interaction.response.edit_message(
                content=f"I need **Send Messages** and **Create Public Threads** in {channel.mention}.",
                view=None,
            )

        named, dupe_notes = resolve_named(setup, interaction.user.id)

        unnamed_counts = {"tank": 0, "healer": 0, "dps": 0}
        for token in setup.have:
            role, _idx = token.split(":")
            unnamed_counts[role] += 1

        for role in ("tank", "healer", "dps"):
            total = len(named[role]) + unnamed_counts[role] + (1 if role == setup.own_role else 0)
            if total > ROLE_SLOTS[role]:
                slots = ROLE_SLOTS[role]
                return await interaction.response.edit_message(
                    content=(
                        f"Too many people picked for **{ROLE_LABEL[role]}** \u2014 only "
                        f"{slots} slot{'s' if slots != 1 else ''} there. Fix it and try again."
                    ),
                    view=setup,
                )

        await interaction.response.edit_message(content="Posting...", view=None)

        msg = await channel.send("Setting up...")
        self.store.create_post(
            message_id=msg.id,
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            thread_id=None,
            owner_id=interaction.user.id,
            dungeon=setup.dungeon,
            notes=setup.notes,
            created_at=int(time.time()),
        )
        self.store.add_signup(msg.id, interaction.user.id, setup.own_role)

        for role, members in named.items():
            for member in members:
                self.store.add_signup(msg.id, member.id, role)

        for token in setup.have:
            role, idx = token.split(":")
            pid = self.store.next_placeholder_id(msg.id)
            self.store.add_signup(msg.id, pid, role, label="already in group")

        post = self.store.post(msg.id)
        rows = self.store.signups(msg.id)

        try:
            thread = await msg.create_thread(
                name=f"{setup.dungeon} \u2014 {interaction.user.display_name}"[:100],
                auto_archive_duration=1440,
            )
            self.store.set_thread(msg.id, thread.id)
        except discord.HTTPException:
            log.warning("Couldn't create thread for %s", msg.id)
        else:
            # Discord only shows a static preview of the starter message inside its
            # own thread, so buttons there aren't clickable. Post a live copy instead.
            try:
                panel = await thread.send(
                    content=render(interaction.guild, post, rows),
                    view=build_view(interaction.guild, post, rows),
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False),
                )
                self.store.set_panel_message(msg.id, panel.id)
                await panel.pin()
            except discord.HTTPException:
                log.warning("Couldn't post/pin panel message for %s", msg.id)

        await msg.edit(
            content=render(interaction.guild, post, rows),
            view=build_view(interaction.guild, post, rows),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False),
        )

        named_ids = [member.id for members in named.values() for member in members]
        if named_ids:
            plural = "were" if len(named_ids) != 1 else "was"
            await self.say_in_thread(
                msg.id,
                f"{_join_mentions(named_ids)} {plural} added to the group by the leader. "
                "Hit Leave on the post if that's wrong.",
            )

        confirm = f"Posted: {msg.jump_url}"
        if dupe_notes:
            confirm += "\n" + "\n".join(dupe_notes)
        await interaction.edit_original_response(content=confirm)

    # ---------- button handling ----------

    async def handle_button(self, interaction: discord.Interaction, action: str, mid: int):
        post = self.store.post(mid)
        if post is None:
            return await interaction.response.send_message(
                "This group is no longer tracked.", ephemeral=True
            )
        if post["status"] != "open" and action != "manage":
            return await interaction.response.send_message(
                "This group is closed.", ephemeral=True
            )

        if action == "manage":
            is_owner = interaction.user.id == post["owner_id"]
            if not (is_owner or interaction.user.guild_permissions.manage_roles):
                return await interaction.response.send_message(
                    "Only the group leader or an officer can manage this.", ephemeral=True
                )
            rows = self.store.signups(mid)
            return await interaction.response.send_message(
                "Adjust the roster or close the group.",
                view=ManageView(self, mid, rows, interaction.guild),
                ephemeral=True,
            )

        if action == "leave":
            return await self.leave(interaction, mid, edit=False)

        # signing up for a role
        plan, current = signup_plan(self.store.signups(mid), interaction.user.id, action)
        if plan == "same":
            return await interaction.response.send_message(
                f"You're already in this group as **{ROLE_LABEL[action]}**.",
                view=InGroupView(self, mid, None),
                ephemeral=True,
            )
        if plan == "full":
            if current:
                return await interaction.response.send_message(
                    f"You're in this group as **{ROLE_LABEL[current]}**. The "
                    f"{ROLE_LABEL[action]} slots are full, so you can't switch to it.",
                    view=InGroupView(self, mid, None),
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                f"The {ROLE_LABEL[action]} slots just filled up.", ephemeral=True
            )
        if plan == "change":
            return await interaction.response.send_message(
                f"You're in this group as **{ROLE_LABEL[current]}**.",
                view=InGroupView(self, mid, action),
                ephemeral=True,
            )

        others = self.others(interaction.user.id, mid)
        if others:
            here = self.store.post(mid)["dungeon"]
            return await interaction.response.send_message(
                f"You're signed up for {other_groups_phrase(others)}. Signing up for "
                f"**{here}** as **{ROLE_LABEL[action]}** takes you out of "
                f"{'that group' if len(others) == 1 else 'those groups'}.",
                view=SwitchGroupView(self, mid, action),
                ephemeral=True,
            )
        await self.join(interaction, mid, action, edit=False, leave_others=False)

    def others(self, uid: int, mid: int) -> list[dict]:
        return [
            {**dict(o), "leads": o["owner_id"] == uid}
            for o in self.store.active_signups(uid, mid)
        ]

    async def reply(self, interaction: discord.Interaction, text: str, *, edit: bool) -> None:
        """Answer a public button with a new private message, or a private view in place."""
        if edit:
            await interaction.response.edit_message(content=text, view=None)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    def still_open(self, mid: int) -> bool:
        post = self.store.post(mid)
        return post is not None and post["status"] == "open"

    async def leave(self, interaction: discord.Interaction, mid: int, *, edit: bool) -> None:
        if edit and not self.still_open(mid):
            return await self.reply(interaction, "This group is closed.", edit=True)
        uid = interaction.user.id
        if not self.store.remove_signup(mid, uid):
            return await self.reply(interaction, "You're not signed up for this one.", edit=edit)
        await self.reply(interaction, "You're off the list.", edit=edit)
        await self.refresh(interaction.guild, mid)
        await self.say_in_thread(mid, f"<@{uid}> has unlisted themselves from this group.")

    async def change_role(self, interaction: discord.Interaction, mid: int, role: str) -> None:
        # The private view can be clicked minutes later: check again.
        if not self.still_open(mid):
            return await self.reply(interaction, "This group is closed.", edit=True)
        uid = interaction.user.id
        plan, current = signup_plan(self.store.signups(mid), uid, role)
        if plan == "same":
            return await self.reply(interaction, f"You're already in as **{ROLE_LABEL[role]}**.", edit=True)
        if plan != "change":
            text = (f"The {ROLE_LABEL[role]} slots filled up in the meantime." if current
                    else "You're no longer in this group. Pick a role on the post to join.")
            return await self.reply(interaction, text, edit=True)
        self.store.set_role(mid, uid, role)
        await self.reply(interaction, f"You're now in as **{ROLE_LABEL[role]}**.", edit=True)
        await self.refresh(interaction.guild, mid)
        await self.say_in_thread(
            mid, f"<@{uid}> switched from **{ROLE_LABEL[current]}** to **{ROLE_LABEL[role]}**."
        )

    async def join(self, interaction: discord.Interaction, mid: int, role: str, *, edit: bool, leave_others: bool) -> None:
        uid = interaction.user.id
        if not self.still_open(mid):
            return await self.reply(interaction, "This group is closed.", edit=edit)
        plan, _ = signup_plan(self.store.signups(mid), uid, role)
        if plan != "join":
            text = ("You're already in this group." if plan in ("same", "change")
                    else f"The {ROLE_LABEL[role]} slots just filled up.")
            return await self.reply(interaction, text, edit=edit)
        # Re-read at confirm time: the member may have joined or left groups since the prompt.
        others = self.others(uid, mid) if leave_others else []
        self.store.add_signup(mid, uid, role)
        here = self.store.post(mid)["dungeon"]
        for o in others:
            self.store.remove_signup(o["message_id"], uid)
        left = f" You've left {other_groups_phrase(others)}." if others else ""
        await self.reply(interaction, f"You're in as **{ROLE_LABEL[role]}**.{left}", edit=edit)
        await self.refresh(interaction.guild, mid)
        await self.say_in_thread(
            mid, f"<@{uid}> has signed up as **{ROLE_LABEL[role]}** for this group."
        )
        for o in others:
            await self.refresh(interaction.guild, o["message_id"])
            await self.say_in_thread(
                o["message_id"], f"<@{uid}> left this group to join a **{here}** group."
            )

    # ---------- closing ----------

    async def close_post(self, interaction: discord.Interaction, mid: int, status: str):
        self.store.set_status(mid, status)
        await interaction.response.edit_message(content=f"Group marked **{status}**.", view=None)
        await self.refresh(interaction.guild, mid)
        if status == "filled":
            await self.say_in_thread(
                mid,
                "Group's full. This thread stays open for coordination and will close "
                f"after {FILLED_IDLE_MINUTES} minutes of quiet.",
            )
            return
        await self.say_in_thread(mid, "This group has been **cancelled**.")
        await self.shut_thread(mid)

    # ---------- expiry ----------

    @tasks.loop(minutes=5)
    async def expire_loop(self):
        now = int(time.time())

        cutoff = now - EXPIRE_HOURS * 3600
        for post in self.store.stale_open(cutoff):
            guild = self.bot.get_guild(post["guild_id"])
            if guild is None:
                continue
            self.store.set_status(post["message_id"], "expired")
            await self.refresh(guild, post["message_id"])
            await self.say_in_thread(
                post["message_id"],
                f"This group expired after {EXPIRE_HOURS} hours. "
                "Run `/lfg` again if you're still looking.",
            )
            await self.shut_thread(post["message_id"])

        idle_cutoff = now - FILLED_IDLE_MINUTES * 60
        for post in self.store.filled_idle(idle_cutoff):
            guild = self.bot.get_guild(post["guild_id"])
            if guild is None:
                continue
            self.store.set_status(post["message_id"], "closed")
            await self.refresh(guild, post["message_id"])
            await self.shut_thread(post["message_id"])

    @expire_loop.before_loop
    async def before_expire(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    bot.add_dynamic_items(LfgButton)
    await bot.add_cog(Lfg(bot))
