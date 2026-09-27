"""
Guild recruitment applications for WoW Forever.

/recruitpanel posts two persistent Apply buttons. Each opens a five-field modal;
completed applications are posted to the officer review channel with
Accept/Decline buttons, and answers are tracked in SQLite so a pending
application survives restarts.

Loaded by bot.py as an extension.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("wowroles.recruit")

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

DB_PATH = os.environ.get("RECRUIT_DB_PATH", "/data/recruit.db")
RECRUIT_CHANNEL_ID = int(os.environ.get("RECRUIT_CHANNEL_ID", "0") or 0)
OFFICER_REVIEW_CHANNEL_ID = int(os.environ.get("OFFICER_REVIEW_CHANNEL_ID", "0") or 0)

OFFICER_ROLE_NAME = "Officer"
GUEST_ROLE = "Guest"
MEMBER_ROLE = "Guild Member"

# Must match bot.py's CLASSES / MAIN_SUFFIX so we can read an applicant's class
# roles off the panel. Kept as its own copy — this cog doesn't import bot.py,
# same as lfg.py keeps its own copy of the group-finder role names.
CLASSES = [
    "Druid",
    "Hunter",
    "Mage",
    "Paladin",
    "Priest",
    "Rogue",
    "Shaman",
    "Warlock",
    "Warrior",
]
MAIN_SUFFIX = " Main"

APP_TYPE_LABEL = {"raider": "Raider", "social": "Social"}
APP_TYPE_COLOR = {"raider": 0x2ECC71, "social": 0x5865F2}


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
            CREATE TABLE IF NOT EXISTS applications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                message_id  INTEGER,
                thread_id   INTEGER,
                app_type    TEXT NOT NULL,
                main_char   TEXT NOT NULL,
                alts        TEXT NOT NULL,
                logs        TEXT,
                referred_by TEXT,
                extra       TEXT,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  INTEGER NOT NULL,
                decided_by  INTEGER,
                decided_at  INTEGER
            );
            """
        )
        self.db.commit()

    def create_application(self, **kw) -> int:
        cur = self.db.execute(
            "INSERT INTO applications (user_id, guild_id, app_type, main_char, alts,"
            " logs, referred_by, extra, status, created_at) VALUES (:user_id, :guild_id,"
            " :app_type, :main_char, :alts, :logs, :referred_by, :extra, 'pending',"
            " :created_at)",
            kw,
        )
        self.db.commit()
        return cur.lastrowid

    def set_message(self, app_id: int, message_id: int) -> None:
        self.db.execute(
            "UPDATE applications SET message_id=? WHERE id=?", (message_id, app_id)
        )
        self.db.commit()

    def set_thread(self, app_id: int, thread_id: int) -> None:
        self.db.execute(
            "UPDATE applications SET thread_id=? WHERE id=?", (thread_id, app_id)
        )
        self.db.commit()

    def pending_for(self, user_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM applications WHERE user_id=? AND status='pending'"
            " ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    def set_decision(self, app_id: int, status: str, decided_by: int, decided_at: int) -> None:
        self.db.execute(
            "UPDATE applications SET status=?, decided_by=?, decided_at=? WHERE id=?",
            (status, decided_by, decided_at, app_id),
        )
        self.db.commit()


# --------------------------------------------------------------------------
# APPLY BUTTONS (persistent, static — no per-user data)
# --------------------------------------------------------------------------

SITE_PUBLIC_URL = os.environ.get("SITE_PUBLIC_URL", "https://www.bureauguild.com").rstrip("/")


class ApplyView(discord.ui.View):
    """Two link buttons to the site's form (SYNC-SPEC §8 #apply). Intake is web-only."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Apply as a Raider", style=discord.ButtonStyle.link, url=f"{SITE_PUBLIC_URL}/apply?path=raider"))
        self.add_item(discord.ui.Button(label="Apply as Social", style=discord.ButtonStyle.link, url=f"{SITE_PUBLIC_URL}/apply?path=social"))


APPLY_MESSAGE = (
    "**Interested in joining Bureaucracy?** Pick the option that fits you best.\n"
    "You'll sign in with Discord on the site, so join the server first."
)


# --------------------------------------------------------------------------
# OFFICER DECISION BUTTONS
# --------------------------------------------------------------------------

class RecruitDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"wf:recruit:(?P<action>accept|decline):(?P<uid>\d+)",
):
    """Button whose target user is encoded in the custom_id, so it survives restarts."""

    def __init__(self, action: str, uid: int):
        self.action = action
        self.uid = uid
        super().__init__(
            discord.ui.Button(
                label="Accept" if action == "accept" else "Decline",
                style=discord.ButtonStyle.success if action == "accept" else discord.ButtonStyle.danger,
                custom_id=f"wf:recruit:{action}:{uid}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["uid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "Only officers with Manage Roles can decide this.", ephemeral=True
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Recruit = interaction.client.get_cog("Recruit")  # type: ignore[assignment]
        await cog.decide(interaction, self.action, self.uid)


# --------------------------------------------------------------------------
# COG
# --------------------------------------------------------------------------

class Recruit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = Store(DB_PATH)

    # ---------- panel ----------

    @app_commands.command(name="recruitpanel", description="Post the recruitment apply buttons here.")
    @app_commands.checks.has_permissions(administrator=True)
    async def recruitpanel(self, interaction: discord.Interaction):
        perms = interaction.channel.permissions_for(interaction.guild.me)
        if not perms.send_messages:
            return await interaction.response.send_message(
                "I can't post in this channel — give my role **Send Messages** here.",
                ephemeral=True,
            )
        await interaction.response.send_message("Posting...", ephemeral=True)
        await interaction.channel.send(APPLY_MESSAGE, view=ApplyView())
        await interaction.edit_original_response(content="Panel posted.")

    # ---------- decisions on posts made before intake moved to the web ----------

    async def decide(self, interaction: discord.Interaction, action: str, uid: int) -> None:
        app = self.store.pending_for(uid)
        if app is None:
            return await interaction.response.send_message(
                "No pending application found for that user — it may already be decided.",
                ephemeral=True,
            )

        guild = interaction.guild
        member = guild.get_member(uid)
        outcome = "accepted" if action == "accept" else "declined"
        note = ""

        if member is None:
            note = " They've left the server, so no roles were changed."
        elif action == "accept":
            gmember = discord.utils.get(guild.roles, name=MEMBER_ROLE)
            guest = discord.utils.get(guild.roles, name=GUEST_ROLE)
            try:
                if gmember is not None:
                    await member.add_roles(gmember, reason=f"Recruitment accepted by {interaction.user}")
                if guest is not None and guest in member.roles:
                    await member.remove_roles(guest, reason=f"Recruitment accepted by {interaction.user}")
            except discord.Forbidden:
                return await interaction.response.send_message(
                    "I can't change roles — check my role position and Manage Roles permission.",
                    ephemeral=True,
                )

        self.store.set_decision(app["id"], outcome, interaction.user.id, int(time.time()))

        label = APP_TYPE_LABEL[app["app_type"]]
        line = (
            f"{label} application from <@{uid}> was **{outcome}** by "
            f"{interaction.user.mention}.{note}"
        )

        await interaction.response.edit_message(
            content=line, view=None, allowed_mentions=discord.AllowedMentions.none()
        )

        if app["thread_id"]:
            thread = self.bot.get_channel(app["thread_id"])
            if thread is not None:
                try:
                    await thread.send(line, allowed_mentions=discord.AllowedMentions.none())
                    await thread.edit(archived=True)
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot) -> None:
    bot.add_dynamic_items(RecruitDecisionButton)
    cog = Recruit(bot)
    bot.add_view(ApplyView(cog))
    await bot.add_cog(cog)
