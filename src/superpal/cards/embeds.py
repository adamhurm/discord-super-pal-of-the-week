from typing import Optional
import discord
from superpal.cards.models import RARITY_COLORS, RARITY_LABELS


def build_card_embed(
    *,
    display_name: str,
    avatar_url: Optional[str],
    rarity: str,
    card_number: int,
    drawn_by: str,
    bio: Optional[str] = None,
    stats_pairs: Optional[list[tuple[str, str]]] = None,
    action_label: str = "drawn by",
) -> discord.Embed:
    """Build a Discord embed for a drawn card."""
    color = discord.Color(RARITY_COLORS[rarity])
    label = RARITY_LABELS[rarity]

    embed = discord.Embed(description=bio if bio else None, color=color)
    embed.set_author(name=display_name, icon_url=avatar_url)
    embed.set_footer(text=f"{label} · #{card_number} · Bringus Card Game · {action_label} {drawn_by}")
    embed.set_thumbnail(url=avatar_url)

    if stats_pairs:
        value = "\n".join(f"**{k}** {v}" for k, v in stats_pairs)
        embed.add_field(name="Stats", value=value, inline=False)

    return embed
