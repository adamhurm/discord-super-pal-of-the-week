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
) -> discord.Embed:
    """Build a Discord embed for a drawn card."""
    color = discord.Color(RARITY_COLORS[rarity])
    label = RARITY_LABELS[rarity]

    embed = discord.Embed(
        description="*[ Stats & lore coming soon ]*",
        color=color,
    )
    embed.set_author(name=display_name, icon_url=avatar_url)
    embed.set_footer(text=f"{label} · #{card_number} · Bringus Card Game · drawn by {drawn_by}")
    embed.set_thumbnail(url=avatar_url)

    return embed
