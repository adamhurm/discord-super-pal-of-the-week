import discord
from superpal.cards.embeds import build_card_embed
from superpal.cards.models import RARITY_COLORS


def test_build_card_embed_common():
    embed = build_card_embed(
        display_name="Bingus McFlop",
        avatar_url="https://cdn.discordapp.com/avatars/123/abc.png",
        rarity="common",
        card_number=7,
        drawn_by="DiscordUser",
    )
    assert isinstance(embed, discord.Embed)
    assert embed.color.value == RARITY_COLORS["common"]
    assert "COMMON" in embed.footer.text
    assert "#7" in embed.footer.text
    assert embed.author.name == "Bingus McFlop"


def test_build_card_embed_legendary():
    embed = build_card_embed(
        display_name="Dingus Supreme",
        avatar_url=None,
        rarity="legendary",
        card_number=1,
        drawn_by="SomeUser",
    )
    assert embed.color.value == RARITY_COLORS["legendary"]
    assert "LEGENDARY" in embed.footer.text


def test_build_card_embed_bio_and_stats():
    embed = build_card_embed(
        display_name="Test",
        avatar_url=None,
        rarity="rare",
        card_number=3,
        drawn_by="User",
        bio="A mysterious figure.",
        stats_pairs=[("Power Level", "9000"), ("Vibe", "Immaculate")],
    )
    assert embed.description == "A mysterious figure."
    assert len(embed.fields) == 1
    assert embed.fields[0].name == "Stats"
    assert "Power Level" in embed.fields[0].value


def test_build_card_embed_no_bio_no_stats():
    embed = build_card_embed(
        display_name="Test",
        avatar_url=None,
        rarity="rare",
        card_number=3,
        drawn_by="User",
    )
    assert embed.description is None
    assert len(embed.fields) == 0


def test_build_card_embed_custom_action_label():
    embed = build_card_embed(
        display_name="Bingus",
        avatar_url=None,
        rarity="rare",
        card_number=5,
        drawn_by="Alice",
        action_label="gifted by Gifter to",
    )
    assert "gifted by Gifter to Alice" in embed.footer.text
    assert "drawn by" not in embed.footer.text


def test_build_card_embed_default_action_label():
    embed = build_card_embed(
        display_name="Bingus",
        avatar_url=None,
        rarity="common",
        card_number=1,
        drawn_by="Alice",
    )
    assert "drawn by Alice" in embed.footer.text
