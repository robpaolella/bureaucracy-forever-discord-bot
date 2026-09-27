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


def _clean(value: str) -> str | None:
    """Blank optional modal fields come back as '' — treat that as absent."""
    value = value.strip()
    return value or None


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
# RENDERING
# --------------------------------------------------------------------------

def class_roles_held(member: discord.Member) -> list[str]:
    names = {r.name for r in member.roles}
    held = []
    for c in CLASSES:
        if c + MAIN_SUFFIX in names:
            held.append(c + MAIN_SUFFIX)
        if c in names:
            held.append(c)
    return held


def build_embed(
    user: discord.Member,
    app_type: str,
    main_char: str,
    alts: str,
    logs: str | None,
    referred_by: str | None,
    extra: str | None,
) -> discord.Embed:
    label = APP_TYPE_LABEL[app_type]
    e = discord.Embed(
        title=f"{label} application — {user.display_name}",
        color=APP_TYPE_COLOR[app_type],
    )
    e.add_field(name="Applicant", value=f"{user.mention} (`{user}`)", inline=False)
    e.add_field(name="Main character name", value=main_char, inline=False)
    e.add_field(name="Any level 60 alts?", value=alts, inline=False)
    if logs:
        e.add_field(name="Logs", value=logs, inline=False)
    if referred_by:
        e.add_field(name="Referred by anyone in Bureaucracy?", value=referred_by, inline=False)
    if extra:
        e.add_field(name="Anything else we should know?", value=extra, inline=False)

    e.add_field(
        name="Account created",
        value=f"<t:{int(user.created_at.timestamp())}:R>",
        inline=True,
    )
    if user.joined_at:
        e.add_field(
            name="Joined server",
            value=f"<t:{int(user.joined_at.timestamp())}:R>",
            inline=True,
        )

    held = class_roles_held(user)
    e.add_field(name="Class roles", value=", ".join(held) if held else "None yet.", inline=False)
    return e


# --------------------------------------------------------------------------
# APPLY BUTTONS (persistent, static — no per-user data)
# --------------------------------------------------------------------------

class ApplyView(discord.ui.View):
    """Lives forever on one message. custom_ids must stay stable."""

    def __init__(self, cog: "Recruit"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Apply as a Raider",
        style=discord.ButtonStyle.success,
        custom_id="wf:recruit:apply:raider",
    )
    async def apply_raider(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.start_application(interaction, "raider")

    @discord.ui.button(
        label="Apply as Social",
        style=discord.ButtonStyle.secondary,
        custom_id="wf:recruit:apply:social",
    )
    async def apply_social(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.cog.start_application(interaction, "social")


# --------------------------------------------------------------------------
# APPLICATION MODALS
# --------------------------------------------------------------------------

class RaiderModal(discord.ui.Modal, title="Raider application"):
    main_char = discord.ui.TextInput(
        label="Main character name", style=discord.TextStyle.short,
        required=True, max_length=100,
    )
    alts = discord.ui.TextInput(
        label="Any level 60 alts?", style=discord.TextStyle.short,
        required=True, max_length=300,
        placeholder="Names and classes, or 'none'",
    )
    logs = discord.ui.TextInput(
        label="Logs", style=discord.TextStyle.paragraph,
        required=True, max_length=1000,
        placeholder="WoW Forever main preferred. Alt, Classic or retail also fine.",
    )
    referred_by = discord.ui.TextInput(
        label="Referred by anyone in Bureaucracy?", style=discord.TextStyle.short,
        required=False, max_length=100,
        placeholder="Their name, or leave blank",
    )
    extra = discord.ui.TextInput(
        label="Anything else we should know?", style=discord.TextStyle.paragraph,
        required=False, max_length=1000,
    )

    def __init__(self, cog: "Recruit"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.submit_application(
            interaction, "raider",
            main_char=self.main_char.value.strip(),
            alts=self.alts.value.strip(),
            logs=self.logs.value.strip(),
            referred_by=_clean(self.referred_by.value),
            extra=_clean(self.extra.value),
        )


class SocialModal(discord.ui.Modal, title="Social application"):
    main_char = discord.ui.TextInput(
        label="Main character name", style=discord.TextStyle.short,
        required=True, max_length=100,
    )
    alts = discord.ui.TextInput(
        label="Any level 60 alts?", style=discord.TextStyle.short,
        required=True, max_length=300,
        placeholder="Names and classes, or 'none'",
    )
    logs = discord.ui.TextInput(
        label="Logs", style=discord.TextStyle.paragraph,
        required=False, max_length=1000,
        placeholder="Optional for social applicants. Paste any if you have them.",
    )
    referred_by = discord.ui.TextInput(
        label="Referred by anyone in Bureaucracy?", style=discord.TextStyle.short,
        required=False, max_length=100,
        placeholder="Their name, or leave blank",
    )
    extra = discord.ui.TextInput(
        label="Anything else we should know?", style=discord.TextStyle.paragraph,
        required=False, max_length=1000,
    )

    def __init__(self, cog: "Recruit"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.submit_application(
            interaction, "social",
            main_char=self.main_char.value.strip(),
            alts=self.alts.value.strip(),
            logs=_clean(self.logs.value),
            referred_by=_clean(self.referred_by.value),
            extra=_clean(self.extra.value),
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
        cog: "Recruit" = interaction.client.get_cog("Recruit")  # type: ignore[assignment]
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
        await interaction.channel.send(
            "**Interested in joining Bureaucracy?** Pick the option that fits you best.",
            view=ApplyView(self),
        )
        await interaction.edit_original_response(content="Panel posted.")

    # ---------- apply flow ----------

    async def start_application(self, interaction: discord.Interaction, app_type: str) -> None:
        if self.store.pending_for(interaction.user.id) is not None:
            return await interaction.response.send_message(
                "You already have a pending application. An officer needs to decide "
                "on it before you can apply again.",
                ephemeral=True,
            )
        modal = RaiderModal(self) if app_type == "raider" else SocialModal(self)
        await interaction.response.send_modal(modal)

    async def submit_application(
        self,
        interaction: discord.Interaction,
        app_type: str,
        *,
        main_char: str,
        alts: str,
        logs: str | None,
        referred_by: str | None,
        extra: str | None,
    ) -> None:
        guild = interaction.guild
        user: discord.Member = interaction.user  # type: ignore[assignment]

        app_id = self.store.create_application(
            user_id=user.id,
            guild_id=guild.id,
            app_type=app_type,
            main_char=main_char,
            alts=alts,
            logs=logs,
            referred_by=referred_by,
            extra=extra,
            created_at=int(time.time()),
        )

        channel = self.bot.get_channel(OFFICER_REVIEW_CHANNEL_ID)
        if channel is None:
            log.warning("Officer review channel %s not found.", OFFICER_REVIEW_CHANNEL_ID)
            return await interaction.response.send_message(
                "Your application was saved, but I couldn't reach the officer review "
                "channel — let an officer know.",
                ephemeral=True,
            )

        officer_role = discord.utils.get(guild.roles, name=OFFICER_ROLE_NAME)
        embed = build_embed(user, app_type, main_char, alts, logs, referred_by, extra)
        view = discord.ui.View(timeout=None)
        view.add_item(RecruitDecisionButton("accept", user.id))
        view.add_item(RecruitDecisionButton("decline", user.id))

        msg = await channel.send(
            content=officer_role.mention if officer_role else f"@{OFFICER_ROLE_NAME} (role not found)",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False,
                roles=[officer_role] if officer_role else [],
            ),
        )
        self.store.set_message(app_id, msg.id)

        try:
            thread = await msg.create_thread(
                name=f"Application — {user.display_name}"[:100],
                auto_archive_duration=1440,
            )
            self.store.set_thread(app_id, thread.id)
        except discord.HTTPException:
            log.warning("Couldn't create thread for application %s", app_id)

        await interaction.response.send_message(
            "Your application was received. An officer will be in touch.", ephemeral=True
        )

    # ---------- officer decision ----------

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
