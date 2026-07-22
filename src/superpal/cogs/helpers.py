"""Shared helpers for the Discord cogs."""

import discord

import superpal.env as superpal_env
import superpal.static as superpal_static
from superpal.cards.embeds import build_card_embed
from superpal.cards.service import get_member_card_context
from superpal.env import WEBAPP_BASE_URL

log = superpal_env.log

CLIPPY_ROLE_ID = 1085646770006151259


def _is_clippy(interaction: discord.Interaction) -> bool:
    role_ids = [r.id for r in getattr(interaction.user, "roles", [])]
    return CLIPPY_ROLE_ID in role_ids


def _label_card_subjects(subjects: list[dict]) -> list[tuple[str, str]]:
    """Format (label, discord_id) pairs for card autocomplete, disambiguating collisions.

    Synthetic (non-Discord) subjects get a " (Custom)" tag. Any label that still
    collides with another entry after tagging gets the subject's last 4 ID chars appended.
    """
    labeled = [
        (
            f"{s['display_name']} (Custom)" if s["is_synthetic"] else s["display_name"],
            s["discord_id"],
        )
        for s in subjects
    ]
    label_counts: dict[str, int] = {}
    for label, _ in labeled:
        label_counts[label] = label_counts.get(label, 0) + 1
    return [
        (f"{label} ({discord_id[-4:]})" if label_counts[label] > 1 else label, discord_id)
        for label, discord_id in labeled
    ]


def _resolve_avatar_url(avatar_url: str | None) -> str | None:
    """Return an absolute URL for Discord embeds.

    Synthetic members store avatars as relative paths (/static/avatars/...).
    Discord's embed API requires HTTP(S) URLs, so prefix with WEBAPP_BASE_URL.
    """
    if not avatar_url or avatar_url.startswith("http"):
        return avatar_url
    return f"{WEBAPP_BASE_URL.rstrip('/')}{avatar_url}"


async def _member_card_embed(
    card_member_id: str,
    rarity: str,
    card_number: int,
    drawn_by: str,
    action_label: str = "drawn by",
) -> discord.Embed:
    """Build a card embed from the member's stored display fields."""
    ctx = await get_member_card_context(card_member_id)
    return build_card_embed(
        display_name=ctx.display_name if ctx else "Unknown",
        avatar_url=_resolve_avatar_url(ctx.avatar_url if ctx else None),
        rarity=rarity,
        card_number=card_number,
        drawn_by=drawn_by,
        bio=ctx.bio if ctx else None,
        stats_pairs=ctx.stats_pairs if ctx else [],
        action_label=action_label,
    )


def get_non_bot_members(guild: discord.Guild) -> list[discord.Member]:
    """Get list of non-bot members from a guild."""
    return [m for m in guild.members if not m.bot]


def get_super_pal_role(guild: discord.Guild) -> discord.Role | None:
    """Get the Super Pal of the Week role from a guild, or None if not found."""
    role = discord.utils.get(guild.roles, name=superpal_static.SUPER_PAL_ROLE_NAME)
    if not role:
        log.error(f"Super Pal role '{superpal_static.SUPER_PAL_ROLE_NAME}' not found in guild")
    return role
