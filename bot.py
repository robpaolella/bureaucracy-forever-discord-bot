"""
WoW Forever — class role selection bot.

Posts a persistent button panel that lets members:
  1. Pick their MAIN class   -> grants "<Class> Main" + "<Class>"
  2. Pick their ALT classes  -> grants "<Class>" only
  3. Pick group-finder roles -> Tank / Healer / DPS

Also handles new arrivals: assigns Guest on join and posts a triage message
with Guild Member / Guest buttons for officers to answer. Guild Member then asks
Social, Raider or Raider Trial and grants the matching roles.

Role names in Discord must match the names configured below exactly.
Requires the SERVER MEMBERS privileged intent (for on_member_join).
"""

from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("GUILD_ID")  # optional: makes slash commands appear instantly

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

MAIN_SUFFIX = " Main"          # "Warlock" -> "Warlock Main"
GROUP_ROLES = ["Tank", "Healer", "DPS"]

# New-member triage
GUEST_ROLE = "Guest"
MEMBER_ROLE = "Guild Member"
NEW_USER_CHANNEL_ID = int(os.environ.get("NEW_USER_CHANNEL_ID", "0") or 0)


def _role_id(name: str) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else 0


# The rank roles a new guild member can get, by id (the same settings the site sync uses).
RANK_ROLE_ENV = {"raider": "ROLE_RAIDER_ID", "trial": "ROLE_TRIAL_ID", "social": "ROLE_SOCIAL_ID"}
RANK_ROLE_IDS = {key: _role_id(env) for key, env in RANK_ROLE_ENV.items()}

# Triage choice -> (button label, rank roles it grants). The other rank roles come off.
TRIAGE_RANKS = {
    "social": ("Social", ("social",)),
    "raider": ("Raider", ("raider",)),
    "trial": ("Raider Trial", ("raider", "trial")),
}

# Unicode fallbacks. To use custom emoji, replace the value with the full
# code string and nothing else, e.g.  "Druid": "<:druid:1234567890123456789>"
# Animated emoji need the leading a:  "Druid": "<a:druid:1234567890123456789>"
# Run /emojidump in Discord to get these strings without hunting for IDs.
CLASS_EMOJI = {
    "Druid": "<:druid:1549151520333897799>",
    "Hunter": "<:hunter:1549151544044167291>",
    "Mage": "<:mage:1549151562973323335>",
    "Paladin": "<:paladin:1549151584615927868>",
    "Priest": "<:priest:1549151601078444133>",
    "Rogue": "<:rogue:1549151618576945262>",
    "Shaman": "<:shaman:1549151639942864982>",
    "Warlock": "<:warlock:1549151656648646706>",
    "Warrior": "<:warrior:1549151673530843207>",
}

GROUP_EMOJI = {
    "Tank": "<:tank:1549151484145565777>",
    "Healer": "<:healer:1549151450859442318>",
    "DPS": "<:dps:1549151418617692171>",
}

EMBED_COLOR = 0xFFD100  # guild gold; try 0xC79C6E for a muted leather tone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wowroles")


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------

def main_role_names() -> list[str]:
    return [c + MAIN_SUFFIX for c in CLASSES]


def find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=name)


def current_main(member: discord.Member) -> str | None:
    """Return the class name of the member's current main, or None."""
    names = {r.name for r in member.roles}
    for c in CLASSES:
        if c + MAIN_SUFFIX in names:
            return c
    return None


def triage_rank_roles(choice: str) -> tuple[list[str], list[str]]:
    """Rank role keys to add and to remove for a triage choice. Pure."""
    grant = list(TRIAGE_RANKS[choice][1])
    return grant, [k for k in RANK_ROLE_IDS if k not in grant]


def missing_roles(guild: discord.Guild) -> list[str]:
    wanted = CLASSES + main_role_names() + GROUP_ROLES + [GUEST_ROLE, MEMBER_ROLE]
    return [n for n in wanted if find_role(guild, n) is None]


async def fail(interaction: discord.Interaction, msg: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


HIERARCHY_HELP = (
    "I couldn't change your roles. An admin needs to check that my role is **above** "
    "all the class roles in Server Settings -> Roles, and that I have **Manage Roles**."
)


# --------------------------------------------------------------------------
# MAIN CLASS PICKER  (ephemeral, single select)
# --------------------------------------------------------------------------

async def apply_main(interaction: discord.Interaction, chosen: str) -> None:
    """Grant `<chosen> Main` and `<chosen>`, drop any other main flag, then carry on to the roster."""
    member: discord.Member = interaction.user  # type: ignore[assignment]
    guild = interaction.guild
    # Role edits plus a call to the site can outlast Discord's three seconds; answer now, edit later.
    await interaction.response.defer()

    new_main = find_role(guild, chosen + MAIN_SUFFIX)
    new_tag = find_role(guild, chosen)
    if new_main is None or new_tag is None:
        return await fail(
            interaction,
            f"The roles `{chosen + MAIN_SUFFIX}` and/or `{chosen}` don't exist yet. "
            "Ask an admin to create them.",
        )

    mains = set(main_role_names())
    drop = [r for r in member.roles if r.name in mains and r.name != new_main.name]
    add = [r for r in (new_main, new_tag) if r not in member.roles]

    try:
        if drop:
            await member.remove_roles(*drop, reason="Main class change")
        if add:
            await member.add_roles(*add, reason="Main class selection")
    except discord.Forbidden:
        return await fail(interaction, HIERARCHY_HELP)

    note = ""
    if drop:
        old = drop[0].name.removesuffix(MAIN_SUFFIX)
        note = (
            f"\nYou're no longer flagged as a **{old} Main**. You kept the `{old}` "
            "class tag — drop it under **Manage alts** if you don't play one anymore."
        )

    if drop or add:
        done = (
            f"{CLASS_EMOJI.get(chosen, '')} Main set to **{chosen}**. "
            f"You now have `{chosen} Main` and `{chosen}`.{note}"
        )
    else:
        done = f"{CLASS_EMOJI.get(chosen, '')} Your main stays **{chosen}**."
    # With the site sync running, carry on to spec, role and name so the web roster gets the main.
    sync = interaction.client.get_cog("SyncCog")
    api = getattr(sync, "api", None)
    if api is not None:
        # Imported here so this file still runs when the sync extension is not configured.
        from sync.mains import continue_main_flow

        if await continue_main_flow(interaction, chosen, api, done):
            return
    await interaction.edit_original_response(content=done, view=None)


class MainSelect(discord.ui.Select):
    def __init__(self, member: discord.Member):
        existing = current_main(member)
        # No option is pre-selected: Discord sends nothing when the pre-selected option is picked
        # again, which left no way to keep the class and change the spec or name.
        options = [discord.SelectOption(label=c, emoji=CLASS_EMOJI.get(c)) for c in CLASSES]
        super().__init__(
            placeholder=(
                f"Your main is {existing}. Pick it again to keep it..."
                if existing
                else "Choose your main class..."
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await apply_main(interaction, self.values[0])


class KeepMainButton(discord.ui.Button):
    """The one-click way to keep the class and go straight to spec, role and name."""

    def __init__(self, main: str):
        super().__init__(
            label=f"Keep {main}",
            style=discord.ButtonStyle.primary,
            emoji=CLASS_EMOJI.get(main),
        )
        self.main = main

    async def callback(self, interaction: discord.Interaction) -> None:
        await apply_main(interaction, self.main)


class MainSelectView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=180)
        self.add_item(MainSelect(member))
        existing = current_main(member)
        if existing:
            self.add_item(KeepMainButton(existing))


# --------------------------------------------------------------------------
# ALT PICKER  (ephemeral, multi select, main excluded)
# --------------------------------------------------------------------------

class AltSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, main: str):
        held = {r.name for r in member.roles}
        self.choices = [c for c in CLASSES if c != main]
        options = [
            discord.SelectOption(
                label=c,
                emoji=CLASS_EMOJI.get(c),
                default=(c in held),
            )
            for c in self.choices
        ]
        super().__init__(
            placeholder="Select every class you also play (or none to clear)...",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        member: discord.Member = interaction.user  # type: ignore[assignment]
        guild = interaction.guild

        desired = set(self.values)
        held = {r.name for r in member.roles}

        to_add, to_remove = [], []
        for c in self.choices:
            role = find_role(guild, c)
            if role is None:
                continue
            if c in desired and c not in held:
                to_add.append(role)
            elif c not in desired and c in held:
                to_remove.append(role)

        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="Alt class update")
            if to_add:
                await member.add_roles(*to_add, reason="Alt class update")
        except discord.Forbidden:
            return await fail(interaction, HIERARCHY_HELP)

        if desired:
            body = "Your alts: " + ", ".join(
                f"{CLASS_EMOJI.get(c, '')} **{c}**" for c in self.choices if c in desired
            )
        else:
            body = "All alt class tags cleared."

        await interaction.response.edit_message(content=body, view=None)


class AltSelectView(discord.ui.View):
    def __init__(self, member: discord.Member, main: str):
        super().__init__(timeout=180)
        self.add_item(AltSelect(member, main))


# --------------------------------------------------------------------------
# GROUP FINDER PICKER  (optional — delete this block and its button if unwanted)
# --------------------------------------------------------------------------

class GroupSelect(discord.ui.Select):
    def __init__(self, member: discord.Member):
        held = {r.name for r in member.roles}
        options = [
            discord.SelectOption(
                label=g,
                emoji=GROUP_EMOJI.get(g),
                default=(g in held),
            )
            for g in GROUP_ROLES
        ]
        super().__init__(
            placeholder="What can you fill in a group?",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        member: discord.Member = interaction.user  # type: ignore[assignment]
        guild = interaction.guild

        desired = set(self.values)
        held = {r.name for r in member.roles}

        to_add, to_remove = [], []
        for g in GROUP_ROLES:
            role = find_role(guild, g)
            if role is None:
                continue
            if g in desired and g not in held:
                to_add.append(role)
            elif g not in desired and g in held:
                to_remove.append(role)

        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="Group finder update")
            if to_add:
                await member.add_roles(*to_add, reason="Group finder update")
        except discord.Forbidden:
            return await fail(interaction, HIERARCHY_HELP)

        body = (
            "Group finder roles: " + ", ".join(f"**{g}**" for g in GROUP_ROLES if g in desired)
            if desired
            else "Group finder roles cleared."
        )
        await interaction.response.edit_message(content=body, view=None)


class GroupSelectView(discord.ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=180)
        self.add_item(GroupSelect(member))


# --------------------------------------------------------------------------
# THE PERSISTENT PANEL
# --------------------------------------------------------------------------

class RolePanel(discord.ui.View):
    """Lives forever on one message. custom_ids must stay stable."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Set my main",
        style=discord.ButtonStyle.primary,
        emoji="\u2B50",
        custom_id="wowforever:main",
    )
    async def set_main(self, interaction: discord.Interaction, _: discord.ui.Button):
        gone = missing_roles(interaction.guild)
        if gone:
            return await fail(
                interaction,
                "Setup problem — these roles are missing: " + ", ".join(f"`{g}`" for g in gone),
            )
        existing = current_main(interaction.user)
        prompt = (
            f"{CLASS_EMOJI.get(existing, '')} Your main is **{existing}**. Keep it to change your "
            "spec, raid role or name, or pick a different class."
            if existing
            else "\U0001F5FA\uFE0F Which calling is yours? You'll get both the **Main** flag and the class tag."
        )
        await interaction.response.send_message(
            prompt,
            view=MainSelectView(interaction.user),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Manage my alts",
        style=discord.ButtonStyle.secondary,
        emoji="\U0001F3AD",
        custom_id="wowforever:alts",
    )
    async def manage_alts(self, interaction: discord.Interaction, _: discord.ui.Button):
        main = current_main(interaction.user)
        if main is None:
            return await fail(
                interaction, "Set your main class first, then come back for alts."
            )
        await interaction.response.send_message(
            f"Your main is **{main}**. Tick every *other* class you play — "
            "unticking one removes that tag.",
            view=AltSelectView(interaction.user, main),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Group finder role",
        style=discord.ButtonStyle.secondary,
        emoji="\U0001F6E1\uFE0F",
        custom_id="wowforever:group",
    )
    async def group_finder(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "\U0001F3AF What can you bring to a group? Pick everything you're willing to fill.",
            view=GroupSelectView(interaction.user),
            ephemeral=True,
        )

    @discord.ui.button(
        label="What do I have?",
        style=discord.ButtonStyle.secondary,
        emoji="\U0001F4DC",
        custom_id="wowforever:whoami",
    )
    async def whoami(self, interaction: discord.Interaction, _: discord.ui.Button):
        member: discord.Member = interaction.user  # type: ignore[assignment]
        held = {r.name for r in member.roles}
        main = current_main(member)
        alts = [c for c in CLASSES if c in held and c != main]
        groups = [g for g in GROUP_ROLES if g in held]

        lines = [f"**Main:** {CLASS_EMOJI.get(main, '')} {main}" if main else "**Main:** not set"]
        lines.append("**Alts:** " + (", ".join(alts) if alts else "none"))
        lines.append("**Group finder:** " + (", ".join(groups) if groups else "none"))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


def panel_embed() -> discord.Embed:
    e = discord.Embed(
        title="\u2694\uFE0F  Declare your class",
        description=(
            "\U0001F3F0 **Welcome to the Bureaucracy Guild Hall!**\n"
            "Start by selecting your main. Whichever class you choose will be "
            "represented as your color across the server.\n"
            "Then pick your alts so you can be notified of class updates "
            "as well as keeping the guild updated on your characters.\n\n"
            "\U0001F4DC Pinging `@Druid` notifies **every** druid in the guild, "
            "main or alt. Use class pings sparingly so you're not spamming people."
        ),
        color=EMBED_COLOR,
    )
    e.add_field(
        name="\u2B50  Set my main",
        value="Pick your main class. Grants `<Class> Main` and `<Class>` roles.",
        inline=False,
    )
    e.add_field(
        name="\U0001F3AD  Manage my alts",
        value="Select any alts you're serious about and want to be notified on. "
              "Deselect any you'd like to remove.",
        inline=False,
    )
    e.add_field(
        name="\U0001F6E1\uFE0F  Group finder role",
        value=f"{GROUP_EMOJI['Tank']} Tank  \u2022  {GROUP_EMOJI['Healer']} Healer  \u2022  "
              f"{GROUP_EMOJI['DPS']} DPS \u2014 for the Discord group finder.",
        inline=False,
    )
    e.set_footer(text="Only you can see your choices. Respec as often as you like \u2014 no gold cost here.")
    return e


# --------------------------------------------------------------------------
# NEW MEMBER TRIAGE
# --------------------------------------------------------------------------

TRIAGE_LABELS = {"member": MEMBER_ROLE, "guest": GUEST_ROLE, "back": "Back"} | {
    k: label for k, (label, _) in TRIAGE_RANKS.items()
}
TRIAGE_STYLES = {
    "member": discord.ButtonStyle.success,
    "raider": discord.ButtonStyle.success,
    "trial": discord.ButtonStyle.primary,
}


def triage_prompt(uid: int) -> str:
    return f"The user <@{uid}> has joined the server. Are they a guild member or just a guest?"


def triage_rank_prompt(uid: int) -> str:
    return f"<@{uid}> is joining as a guild member. Are they Social, a Raider or a Raider Trial?"


class TriageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"wf:triage:(?P<action>member|guest|social|raider|trial|back):(?P<uid>\d+)",
):
    """Button whose target user is encoded in the custom_id, so it survives restarts."""

    def __init__(self, action: str, uid: int):
        self.action = action
        self.uid = uid
        super().__init__(
            discord.ui.Button(
                label=TRIAGE_LABELS[action],
                style=TRIAGE_STYLES.get(action, discord.ButtonStyle.secondary),
                custom_id=f"wf:triage:{action}:{uid}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["uid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "Only officers and admins can answer this.", ephemeral=True
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        # Guild Member is a two-step answer: first the kind of member, then the roles.
        if self.action == "member":
            return await interaction.response.edit_message(
                content=triage_rank_prompt(self.uid), view=triage_rank_view(self.uid)
            )
        if self.action == "back":
            return await interaction.response.edit_message(
                content=triage_prompt(self.uid), view=triage_view(self.uid)
            )

        guild = interaction.guild
        member = guild.get_member(self.uid)

        if member is None:
            return await interaction.response.edit_message(
                content=f"<@{self.uid}> is no longer in the server \u2014 nothing to do.",
                view=None,
            )

        guest = find_role(guild, GUEST_ROLE)
        gmember = find_role(guild, MEMBER_ROLE)
        if guest is None or gmember is None:
            return await interaction.response.send_message(
                f"Missing the `{GUEST_ROLE}` or `{MEMBER_ROLE}` role.", ephemeral=True
            )

        try:
            if self.action == "guest":
                if guest not in member.roles:
                    await member.add_roles(guest, reason=f"Guest, set by {interaction.user}")
                assigned = [guest]
            else:
                grant, drop = triage_rank_roles(self.action)
                unset = [RANK_ROLE_ENV[k] for k in grant + drop if not guild.get_role(RANK_ROLE_IDS[k])]
                if unset:
                    return await interaction.response.send_message(
                        "These role settings are missing or wrong in the bot's config: "
                        + ", ".join(f"`{n}`" for n in unset),
                        ephemeral=True,
                    )
                ranks = [guild.get_role(RANK_ROLE_IDS[k]) for k in grant]
                remove = [guest] + [guild.get_role(RANK_ROLE_IDS[k]) for k in drop]
                assigned = [gmember, *ranks]
                # Imported here so this file still runs without the sync package's dependencies.
                from sync.members import roles_after_change

                # One edit, so the site's roster sees one change rather than two halves.
                await member.edit(
                    roles=roles_after_change(member, assigned, remove),
                    reason=f"{TRIAGE_LABELS[self.action]}, set by {interaction.user}",
                )
        except discord.Forbidden:
            return await interaction.response.send_message(HIERARCHY_HELP, ephemeral=True)

        names = [r.mention for r in assigned]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        await interaction.response.edit_message(
            content=f"New user {member.mention} was set to {joined} by {interaction.user.mention}.",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def triage_view(uid: int) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(TriageButton("member", uid))
    v.add_item(TriageButton("guest", uid))
    return v


def triage_rank_view(uid: int) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for action in (*TRIAGE_RANKS, "back"):
        v.add_item(TriageButton(action, uid))
    return v


# --------------------------------------------------------------------------
# BOT
# --------------------------------------------------------------------------

class RoleBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # required for on_member_join (privileged)
        # Officer messages in #applications threads become notes on the web (SYNC-SPEC §8).
        # Privileged: enable "Message Content Intent" on the bot in the developer portal.
        intents.message_content = True
        super().__init__(command_prefix="!unused!", intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(RolePanel())  # re-register the panel after restarts
        self.add_dynamic_items(TriageButton)  # re-bind triage buttons after restarts
        await self.load_extension("lfg")  # /lfg command + signup buttons
        await self.load_extension("recruit")  # recruitment applications
        await self.load_extension("sync")  # Discord ↔ web sync (SYNC-SPEC.md); no-op until configured
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("Commands synced.")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        guest = find_role(member.guild, GUEST_ROLE)
        if guest is not None:
            try:
                await member.add_roles(guest, reason="Auto-assigned on join")
            except discord.Forbidden:
                log.warning("Can't assign %s \u2014 check my role position.", GUEST_ROLE)
        else:
            log.warning("Role %s not found.", GUEST_ROLE)

        if not NEW_USER_CHANNEL_ID:
            return
        channel = member.guild.get_channel(NEW_USER_CHANNEL_ID)
        if channel is None:
            log.warning("Channel %s not found.", NEW_USER_CHANNEL_ID)
            return

        try:
            await channel.send(
                triage_prompt(member.id),
                view=triage_view(member.id),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.Forbidden:
            log.warning("Can't post in #%s \u2014 need Send Messages there.", channel)


bot = RoleBot()


@bot.tree.command(name="rolepanel", description="Post the class role selection panel here.")
@app_commands.checks.has_permissions(manage_guild=True)
async def rolepanel(interaction: discord.Interaction):
    gone = missing_roles(interaction.guild)
    if gone:
        return await interaction.response.send_message(
            "These roles don't exist yet: " + ", ".join(f"`{g}`" for g in gone),
            ephemeral=True,
        )
    perms = interaction.channel.permissions_for(interaction.guild.me)
    if not (perms.send_messages and perms.embed_links):
        return await interaction.response.send_message(
            "I can't post in this channel. Give my role **Send Messages** and "
            "**Embed Links** here \u2014 a deny on @everyone applies to me too.",
            ephemeral=True,
        )

    await interaction.response.send_message("Posting...", ephemeral=True)
    try:
        await interaction.channel.send(embed=panel_embed(), view=RolePanel())
    except discord.HTTPException as exc:
        log.exception("Failed to post panel")
        return await interaction.edit_original_response(
            content=f"Couldn't post the panel: `{exc.text or exc}`"
        )
    await interaction.edit_original_response(content="Panel posted.")


@bot.tree.command(name="rolecheck", description="Verify the bot can manage every class role.")
@app_commands.checks.has_permissions(manage_guild=True)
async def rolecheck(interaction: discord.Interaction):
    guild = interaction.guild
    me = guild.me
    problems: list[str] = []

    if not me.guild_permissions.manage_roles:
        problems.append("I don't have **Manage Roles**.")

    here = interaction.channel.permissions_for(me)
    if not (here.send_messages and here.embed_links):
        problems.append(
            "I can't post in this channel (need **Send Messages** + **Embed Links**). "
            "Channel denies on @everyone apply to me too."
        )

    gone = missing_roles(guild)
    if gone:
        problems.append("Missing roles: " + ", ".join(f"`{g}`" for g in gone))

    too_high = [
        n for n in (CLASSES + main_role_names() + GROUP_ROLES + [GUEST_ROLE, MEMBER_ROLE])
        if (r := find_role(guild, n)) and r >= me.top_role
    ]
    if too_high:
        problems.append(
            "These roles sit at or above my own role, so I can't assign them: "
            + ", ".join(f"`{n}`" for n in too_high)
            + ". Drag my role higher."
        )

    await interaction.response.send_message(
        "\n".join(f"- {p}" for p in problems) if problems else "All good. Everything checks out.",
        ephemeral=True,
    )


@bot.tree.command(name="emojidump", description="List custom emoji as paste-ready code strings.")
@app_commands.checks.has_permissions(manage_guild=True)
async def emojidump(interaction: discord.Interaction):
    lines: list[str] = []

    app_emojis = await bot.fetch_application_emojis()
    if app_emojis:
        lines.append("**Application emoji**")
        lines += [f"{e} `\"{e}\"` -> {e.name}" for e in app_emojis]

    if interaction.guild.emojis:
        lines.append("\n**Server emoji**")
        lines += [f"{e} `\"{e}\"` -> {e.name}" for e in interaction.guild.emojis]

    if not lines:
        lines = ["No custom emoji found. Upload some first."]

    out = "\n".join(lines)
    if len(out) > 1900:
        out = out[:1900] + "\n... (truncated)"
    await interaction.response.send_message(out, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_TOKEN environment variable.")
    bot.run(TOKEN)
