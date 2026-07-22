"""Card game commands: draws, collection, trade-in, upgrade, gifts, marketplace links."""

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import superpal.env as superpal_env
import superpal.static as superpal_static
from superpal.cards.db import DB_PATH
from superpal.cards.models import RARITY_LABELS
from superpal.cards.service import (
    accept_offer,
    decline_offer,
    draw_card,
    expire_offer,
    generate_magic_link,
    get_card_quantity,
    get_collection,
    get_leaderboard,
    get_member_display_name,
    get_owned_card_subjects,
    gift_card,
    trade_in,
    upgrade,
)
from superpal.cogs.helpers import _label_card_subjects, _member_card_embed
from superpal.env import WEBAPP_BASE_URL

log = superpal_env.log

TRADE_OFFER_EXPIRY_HOURS = 24

RARITY_CHOICES = [
    app_commands.Choice(name="Common", value="common"),
    app_commands.Choice(name="Uncommon", value="uncommon"),
    app_commands.Choice(name="Rare", value="rare"),
    app_commands.Choice(name="Legendary", value="legendary"),
]


class TradeOfferView(discord.ui.View):
    """Discord DM view sent when a marketplace offer arrives."""

    def __init__(self, offer_id: int, listing_owner_id: str):
        super().__init__(timeout=TRADE_OFFER_EXPIRY_HOURS * 3600)
        self.offer_id = offer_id
        self.listing_owner_id = listing_owner_id
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.listing_owner_id:
            await interaction.response.send_message(
                "Only the listing owner can accept.", ephemeral=True
            )
            return
        success, reason = await accept_offer(self.offer_id, self.listing_owner_id)
        self.stop()
        if success:
            await interaction.response.edit_message(
                content="Trade accepted! Cards have been exchanged.", view=None
            )
        else:
            msg = {
                "not_found": "This offer no longer exists.",
                "not_owner": "You are not the listing owner.",
                "listing_no_card": "Trade failed — you no longer have those listing cards.",
                "offer_no_card": "Trade failed — the proposer no longer has their offered cards.",
            }.get(reason or "", "Trade failed.")
            await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.listing_owner_id:
            await interaction.response.send_message(
                "Only the listing owner can decline.", ephemeral=True
            )
            return
        await decline_offer(self.offer_id, self.listing_owner_id)
        self.stop()
        await interaction.response.edit_message(content="Offer declined.", view=None)

    async def on_timeout(self) -> None:
        await expire_offer(self.offer_id)
        if self.message:
            try:
                await self.message.edit(content="Offer expired.", view=None)
            except discord.NotFound:
                pass


class GiftConfirmView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        gifter_id: str,
        recipient: discord.Member,
        card_member_id: str,
        rarity: str,
    ):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.gifter_id = gifter_id
        self.recipient = recipient
        self.card_member_id = card_member_id
        self.rarity = rarity

    async def on_timeout(self) -> None:
        try:
            await self.interaction.edit_original_response(
                content="Gift confirmation expired.", view=None
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can confirm this.", ephemeral=True
            )
            return

        self.stop()
        card, err = await gift_card(
            gifter_id=self.gifter_id,
            recipient_id=str(self.recipient.id),
            card_member_id=self.card_member_id,
            rarity=self.rarity,
            drawn_by_name=interaction.user.display_name,
        )

        if card is None:
            msg = {
                "no_card": "You no longer have that card.",
                "self_gift": "You can't gift a card to yourself.",
            }.get(err or "", "Gift failed.")
            await interaction.response.edit_message(content=msg, view=None)
            return

        embed = await _member_card_embed(
            self.card_member_id,
            rarity=self.rarity,
            card_number=card.id,
            drawn_by=self.recipient.display_name,
            action_label=f"gifted by {interaction.user.display_name} to",
        )
        await interaction.response.edit_message(content="Gift sent!", view=None)
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                content=(
                    f"{self.recipient.mention} just received "
                    f"a gift from {interaction.user.mention}!"
                ),
                embed=embed,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can cancel this.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(content="Gift cancelled.", view=None)


class CardsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="card-draw",
        description="Draw a card from the Bringus deck (up to 5 per week)",
    )
    async def draw_card_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        member = interaction.user
        is_super_pal = any(
            r.name == superpal_static.SUPER_PAL_ROLE_NAME for r in getattr(member, "roles", [])
        )
        max_draws = 10 if is_super_pal else 5

        card = await draw_card(
            owner_id=str(member.id), max_draws=max_draws, drawn_by_name=member.display_name
        )
        if card is None:
            limit_label = "10 draws" if is_super_pal else "5 draws"
            await interaction.followup.send(
                f"You've used your {limit_label} for this week. Come back Sunday!",
                ephemeral=True,
            )
            return

        embed = await _member_card_embed(
            card.card_member_id,
            rarity=card.rarity,
            card_number=card.id,
            drawn_by=card.drawn_by_name or member.display_name,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="card-display", description="Show a card you own in the channel")
    @app_commands.describe(
        card="The card you want to display",
        rarity="The rarity of the card to display",
    )
    @app_commands.choices(rarity=RARITY_CHOICES)
    async def display_card_command(
        self,
        interaction: discord.Interaction,
        card: str,
        rarity: str,
    ) -> None:
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT uc.id, uc.drawn_by_name FROM user_cards uc "
                "WHERE uc.owner_id = ? AND uc.card_member_id = ? AND uc.rarity = ? "
                "AND uc.quantity > 0",
                (str(interaction.user.id), card, rarity),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            display_name = await get_member_display_name(card) or "Unknown"
            await interaction.followup.send(
                f"You don't own a {rarity.upper()} {display_name} card.",
                ephemeral=True,
            )
            return

        card_id, drawn_by_name = row
        embed = await _member_card_embed(
            card,
            rarity=rarity,
            card_number=card_id,
            drawn_by=drawn_by_name or interaction.user.display_name,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="card-collection", description="Get a private link to your card collection"
    )
    async def my_collection_command(self, interaction: discord.Interaction) -> None:
        url = await generate_magic_link(
            user_id=str(interaction.user.id),
            link_type="collection",
            base_url=WEBAPP_BASE_URL,
        )
        try:
            await interaction.user.send(
                f"Here's your private collection link (valid for 24 hours after first click):"
                f"\n{url}"
            )
            await interaction.response.send_message(
                "Check your DMs for your collection link!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't send you a DM. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(
        name="card-trade-in",
        description="Trade 3 duplicate cards for a random card of the same rarity",
    )
    @app_commands.describe(
        card="The card you want to trade in",
        rarity="The rarity of the card to trade",
    )
    @app_commands.choices(rarity=RARITY_CHOICES)
    async def trade_in_command(
        self,
        interaction: discord.Interaction,
        card: str,
        rarity: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result_card = await trade_in(
            owner_id=str(interaction.user.id),
            card_member_id=card,
            rarity=rarity,
            drawn_by_name=interaction.user.display_name,
        )
        if result_card is None:
            display_name = await get_member_display_name(card) or "Unknown"
            await interaction.followup.send(
                f"You need at least 3× {rarity.upper()} {display_name} to trade in.",  # noqa: RUF001
                ephemeral=True,
            )
            return

        embed = await _member_card_embed(
            result_card.card_member_id,
            rarity=result_card.rarity,
            card_number=result_card.id,
            drawn_by=result_card.drawn_by_name or interaction.user.display_name,
        )
        await interaction.followup.send(
            "Trade complete! You received:", embed=embed, ephemeral=True
        )

    @app_commands.command(
        name="card-upgrade",
        description="Spend 5 duplicate cards to upgrade a member's card rarity",
    )
    @app_commands.describe(
        card="The card you want to upgrade",
        rarity="The current rarity of the card",
    )
    @app_commands.choices(
        rarity=[
            app_commands.Choice(name="Common", value="common"),
            app_commands.Choice(name="Uncommon", value="uncommon"),
            app_commands.Choice(name="Rare", value="rare"),
        ]
    )
    async def upgrade_command(
        self,
        interaction: discord.Interaction,
        card: str,
        rarity: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result_card = await upgrade(
            owner_id=str(interaction.user.id),
            card_member_id=card,
            rarity=rarity,
            drawn_by_name=interaction.user.display_name,
        )
        if result_card is None:
            display_name = await get_member_display_name(card) or "Unknown"
            await interaction.followup.send(
                f"You need at least 5× {rarity.upper()} {display_name} to upgrade.",  # noqa: RUF001
                ephemeral=True,
            )
            return

        display_name = await get_member_display_name(result_card.card_member_id) or "Unknown"
        embed = await _member_card_embed(
            result_card.card_member_id,
            rarity=result_card.rarity,
            card_number=result_card.id,
            drawn_by=result_card.drawn_by_name or interaction.user.display_name,
        )
        await interaction.followup.send(
            f"Upgrade complete! {display_name} is now {result_card.rarity.upper()}:",
            embed=embed,
            ephemeral=True,
        )

    @app_commands.command(
        name="card-trade",
        description="Open the trade marketplace to list cards and make offers",
    )
    async def propose_trade_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        url = await generate_magic_link(
            user_id=str(interaction.user.id),
            link_type="collection",
            base_url=WEBAPP_BASE_URL,
        )
        try:
            await interaction.user.send(
                f"Open this link to access the trade marketplace "
                f"(valid 24 hours after first click):\n{url}\n\n"
                "Once open, click **Marketplace** in the top nav to browse listings and make "
                "offers. Right-click any card in your collection to list it for trade."
            )
            await interaction.followup.send(
                "Check your DMs for your marketplace link!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Here's your marketplace link (enable DMs to receive these privately):\n{url}",
                ephemeral=True,
            )

    @app_commands.command(name="card-gift", description="Give one of your cards to another player")
    @app_commands.describe(
        recipient="The server member to receive the gift",
        card="The card you want to gift",
        rarity="The rarity of the card to gift",
    )
    @app_commands.choices(rarity=RARITY_CHOICES)
    async def gift_card_command(
        self,
        interaction: discord.Interaction,
        recipient: discord.Member,
        card: str,
        rarity: str,
    ) -> None:
        gifter_id = str(interaction.user.id)

        if interaction.user.id == recipient.id:
            await interaction.response.send_message(
                "You can't gift a card to yourself.", ephemeral=True
            )
            return

        display_name = await get_member_display_name(card) or "Unknown"
        qty = await get_card_quantity(gifter_id, card, rarity)
        if qty < 1:
            await interaction.response.send_message(
                f"You don't own a {RARITY_LABELS[rarity]} {display_name} card.",
                ephemeral=True,
            )
            return

        view = GiftConfirmView(
            interaction=interaction,
            gifter_id=gifter_id,
            recipient=recipient,
            card_member_id=card,
            rarity=rarity,
        )
        await interaction.response.send_message(
            (
                f"You're about to gift a **{RARITY_LABELS[rarity]} {display_name}**"
                f" to {recipient.mention} — confirm?"
            ),
            view=view,
            ephemeral=True,
        )

    @display_card_command.autocomplete("card")
    @trade_in_command.autocomplete("card")
    @upgrade_command.autocomplete("card")
    @gift_card_command.autocomplete("card")
    async def _card_subject_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        subjects = await get_owned_card_subjects(str(interaction.user.id))
        labeled = _label_card_subjects(subjects)
        matches = [
            (label, discord_id)
            for label, discord_id in labeled
            if current.lower() in label.lower()
        ]
        return [
            app_commands.Choice(name=label[:100], value=discord_id)
            for label, discord_id in matches[:25]
        ]

    @app_commands.command(
        name="card-collection-leaderboard", description="Show the top 10 card collectors"
    )
    @app_commands.describe(sort_by="What to rank players by")
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="Total Cards", value="total"),
            app_commands.Choice(name="Legendary Cards", value="legendary"),
            app_commands.Choice(name="Unique Members", value="unique"),
        ]
    )
    async def card_collection_leaderboard_command(
        self,
        interaction: discord.Interaction,
        sort_by: str = "total",
    ) -> None:
        await interaction.response.defer()
        rows = await get_leaderboard(sort_by)

        title_map = {
            "total": "Total Cards",
            "legendary": "Legendary Cards",
            "unique": "Unique Members",
        }
        unit_map = {"total": "cards", "legendary": "legendary cards", "unique": "unique members"}
        title = f"Top 10 — {title_map.get(sort_by, 'Total Cards')}"
        unit = unit_map.get(sort_by, "cards")

        if not rows:
            embed = discord.Embed(
                title=title,
                description="No cards have been collected yet!",
                color=discord.Color(0x5865F2),
            )
        else:
            lines = [
                f"{rank}. {row['display_name']} — {row['total']} {unit}"
                for rank, row in enumerate(rows, start=1)
            ]
            embed = discord.Embed(
                title=title, description="\n".join(lines), color=discord.Color(0x5865F2)
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="card-progress", description="Check your card collection progress")
    async def card_progress_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        data = await get_collection(str(interaction.user.id))
        owned: list[dict] = data["owned"]
        undiscovered: list[dict] = data["undiscovered"]

        unique_members_collected = len({c["member_id"] for c in owned})
        total_eligible = unique_members_collected + len(undiscovered)
        completion_pct = (
            round(unique_members_collected / total_eligible * 100) if total_eligible > 0 else 0
        )

        rarity_members: dict[str, set[str]] = {
            "common": set(),
            "uncommon": set(),
            "rare": set(),
            "legendary": set(),
        }
        member_rarities: dict[str, set[str]] = {}
        member_names: dict[str, str] = {}
        for card in owned:
            rarity_members[card["rarity"]].add(card["member_id"])
            member_rarities.setdefault(card["member_id"], set()).add(card["rarity"])
            member_names[card["member_id"]] = card["display_name"]

        per_rarity = {r: len(s) for r, s in rarity_members.items()}
        all_rarities = {"common", "uncommon", "rare", "legendary"}
        complete_sets = sorted(
            member_names[mid] for mid, r in member_rarities.items() if r >= all_rarities
        )

        embed = discord.Embed(title="Your Card Progress", color=discord.Color.blurple())
        embed.add_field(
            name="Collection",
            value=f"{unique_members_collected}/{total_eligible} members ({completion_pct}%)",
            inline=False,
        )
        embed.add_field(
            name="Members by Rarity",
            value=(
                f"Common: {per_rarity['common']} · "
                f"Uncommon: {per_rarity['uncommon']} · "
                f"Rare: {per_rarity['rare']} · "
                f"Legendary: {per_rarity['legendary']}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Complete Sets",
            value=", ".join(complete_sets) if complete_sets else "None yet",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CardsCog(bot))
