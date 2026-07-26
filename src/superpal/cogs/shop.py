"""Pringle shop, wallet, currency exchange, and Boin casino games."""

import asyncio
import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

import superpal.env as superpal_env
import superpal.palymarket.service as palymarket_svc
import superpal.static as superpal_static
from superpal.cards.pringle_service import (
    ITEM_COSTS,
    ITEM_DESCRIPTIONS,
    ITEM_NAMES,
    add_pringles,
    buy_item,
    get_balance,
    get_player_items,
    reset_heal_potions_for_empty_players,
    spend_pringles,
)
from superpal.cards.service import draw_card
from superpal.cogs.helpers import _member_card_embed, get_non_bot_members
from superpal.economy import boin_service, exchange_service, game_service
from superpal.economy.boin_service import award_daily_to_all
from superpal.schedule import next_noon_utc, next_sunday_noon_utc

log = superpal_env.log


def _outcome_color(outcome: str) -> int:
    return {
        "win": 0x3BA55C,
        "tie": 0xFAA61A,
        "lose": 0xED4245,
    }.get(outcome, 0x5865F2)


def _net_str(net: int) -> str:
    return f"+{net}" if net >= 0 else str(net)


def _game_error(reason: str) -> str:
    if reason.startswith("minimum_bet_"):
        minimum = reason.split("_")[-1]
        return f"Minimum bet is {minimum} Boins."
    if reason == "insufficient_boins":
        return "Not enough Boins."
    return f"Error: {reason}"


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if not self.heal_potion_reset.is_running():
            self.heal_potion_reset.start()
        if not self.daily_boin_grant.is_running():
            self.daily_boin_grant.start()

    async def cog_unload(self) -> None:
        self.heal_potion_reset.cancel()
        self.daily_boin_grant.cancel()

    @tasks.loop(hours=24 * 7)
    async def heal_potion_reset(self) -> None:
        """Reset Heal Potions to 2 for players with 0 every Sunday at noon UTC."""
        try:
            count = await reset_heal_potions_for_empty_players()
            log.info("Heal potion reset: %d players topped up", count)
        except Exception as e:
            log.error("Error in heal_potion_reset: %s", e)

    @heal_potion_reset.before_loop
    async def before_heal_potion_reset(self) -> None:
        await self.bot.wait_until_ready()
        try:
            target = next_sunday_noon_utc()
            delta = target - datetime.datetime.now(datetime.timezone.utc)
            log.info("Heal potion reset: sleeping for %s. Will wake up Sunday at 12PM UTC.", delta)
            await asyncio.sleep(delta.total_seconds())
        except Exception as e:
            log.error("Error in before_heal_potion_reset: %s", e)

    @tasks.loop(hours=24)
    async def daily_boin_grant(self) -> None:
        """Award daily boins to all guild members at noon UTC."""
        try:
            guild = self.bot.get_guild(superpal_env.GUILD_ID or 0)
            if not guild:
                log.error("daily_boin_grant: could not find guild")
                return
            member_ids = [str(m.id) for m in get_non_bot_members(guild)]
            results = await award_daily_to_all(member_ids)
            log.info("Daily boin grant: awarded to %d members", len(results))
        except Exception as e:
            log.error("Error in daily_boin_grant: %s", e)

    @daily_boin_grant.before_loop
    async def before_daily_boin_grant(self) -> None:
        await self.bot.wait_until_ready()
        try:
            delta = next_noon_utc() - datetime.datetime.now(datetime.timezone.utc)
            log.info("Daily boin grant: sleeping %s until noon UTC", delta)
            await asyncio.sleep(delta.total_seconds())
        except Exception as e:
            log.error("Error in before_daily_boin_grant: %s", e)

    @app_commands.command(
        name="card-shop", description="Browse or buy items from the Pringle shop"
    )
    @app_commands.describe(action="list: show items, buy: purchase an item")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="list", value="list"),
        ]
    )
    async def card_shop_command(
        self, interaction: discord.Interaction, action: str = "list"
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        player_id = str(interaction.user.id)
        balance = await get_balance(player_id)
        items_owned = await get_player_items(player_id)

        lines = [f"**Pringle Balance:** {balance} 🟣\n", "**Item Shop:**"]
        for item_type, cost in ITEM_COSTS.items():
            owned_qty = items_owned.get(item_type, 0)
            lines.append(
                f"• **{ITEM_NAMES[item_type]}** — {cost} Pringles — "
                f"{ITEM_DESCRIPTIONS[item_type]}  *(you have {owned_qty})*"
            )
        lines.append("\nUse `/card-shop-buy <item>` to purchase.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="card-shop-buy", description="Buy an item from the Pringle shop")
    @app_commands.describe(item="Item to purchase")
    @app_commands.choices(
        item=[
            app_commands.Choice(name="Heal Potion (50 🟣)", value="heal_potion"),
            app_commands.Choice(name="Super Potion (100 🟣)", value="super_potion"),
            app_commands.Choice(name="Bringus Boost (75 🟣)", value="bringus_boost"),
            app_commands.Choice(name="Smoke Screen (60 🟣)", value="smoke_screen"),
        ]
    )
    async def card_shop_buy_command(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer(ephemeral=True)
        player_id = str(interaction.user.id)
        success, reason = await buy_item(player_id, item)
        if success:
            balance = await get_balance(player_id)
            await interaction.followup.send(
                f"Purchased **{ITEM_NAMES[item]}**! Remaining balance: {balance} Pringles 🟣",
                ephemeral=True,
            )
        else:
            msg = {
                "unknown_item": "Unknown item.",
                "insufficient_pringles": "Not enough Pringles.",
            }.get(reason, "Purchase failed.")
            await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(
        name="card-pringles",
        description="Check your Pringle balance or trade in for a card draw",
    )
    @app_commands.describe(
        action="balance: show balance, trade-in: spend 100 Pringles for a card draw",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="balance", value="balance"),
            app_commands.Choice(name="trade-in (100 Pringles → 1 draw)", value="trade-in"),
        ]
    )
    async def card_pringles_command(
        self, interaction: discord.Interaction, action: str = "balance"
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        player_id = str(interaction.user.id)

        if action == "balance":
            balance = await get_balance(player_id)
            items = await get_player_items(player_id)
            item_lines = [f"• {ITEM_NAMES[k]}: {v}" for k, v in items.items()] or ["• No items"]
            await interaction.followup.send(
                f"**Pringle Balance:** {balance} 🟣\n\n**Items:**\n" + "\n".join(item_lines),
                ephemeral=True,
            )
        elif action == "trade-in":
            if not await spend_pringles(player_id, 100):
                balance = await get_balance(player_id)
                await interaction.followup.send(
                    f"You need 100 Pringles to trade in for a card draw. You have {balance}.",
                    ephemeral=True,
                )
                return

            is_super_pal = any(
                r.name == superpal_static.SUPER_PAL_ROLE_NAME
                for r in getattr(interaction.user, "roles", [])
            )
            max_draws = 10 if is_super_pal else 5
            card = await draw_card(
                owner_id=player_id,
                max_draws=max_draws + 1,
                drawn_by_name=interaction.user.display_name,
            )
            if card is None:
                # Refund if draw fails (shouldn't happen but be safe)
                await add_pringles(player_id, 100)
                await interaction.followup.send(
                    "Could not draw a card (something went wrong). Pringles refunded.",
                    ephemeral=True,
                )
                return

            embed = await _member_card_embed(
                card.card_member_id,
                rarity=card.rarity,
                card_number=card.id,
                drawn_by=card.drawn_by_name or interaction.user.display_name,
            )
            new_balance = await get_balance(player_id)
            await interaction.followup.send(
                f"Spent 100 Pringles for a card draw! Remaining: {new_balance} 🟣",
                embed=embed,
                ephemeral=True,
            )

    @app_commands.command(
        name="pal-balance", description="Show your Boins, Pringles, and Palycoins"
    )
    async def pal_balance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        player_id = str(interaction.user.id)
        boins = await boin_service.get_balance(player_id)
        pringles = await get_balance(player_id)
        palycoins = await palymarket_svc.get_palycoin_balance(player_id)
        embed = discord.Embed(title="Your Wallet", color=0x5865F2)
        embed.add_field(name="🪙 Boins", value=str(boins), inline=True)
        embed.add_field(name="🥫 Pringles", value=str(pringles), inline=True)
        embed.add_field(name="📈 Palycoins", value=str(palycoins), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="pal-exchange", description="Exchange between Boins, Pringles, and Palycoins"
    )
    @app_commands.describe(
        from_currency="Currency to spend",
        to_currency="Currency to receive",
        amount="Amount to exchange",
    )
    @app_commands.choices(
        from_currency=[
            app_commands.Choice(name="Boins", value="boins"),
            app_commands.Choice(name="Pringles", value="pringles"),
            app_commands.Choice(name="Palycoins", value="palycoins"),
        ],
        to_currency=[
            app_commands.Choice(name="Boins", value="boins"),
            app_commands.Choice(name="Pringles", value="pringles"),
            app_commands.Choice(name="Palycoins", value="palycoins"),
        ],
    )
    async def pal_exchange(
        self,
        interaction: discord.Interaction,
        from_currency: str,
        to_currency: str,
        amount: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if from_currency == to_currency:
            await interaction.followup.send(
                "Cannot exchange a currency for itself.", ephemeral=True
            )
            return
        success, reason, received = await exchange_service.exchange(
            str(interaction.user.id), from_currency, to_currency, amount
        )
        if success:
            await interaction.followup.send(
                f"Exchanged {amount} {from_currency.capitalize()} "
                f"for {received} {to_currency.capitalize()}!",
                ephemeral=True,
            )
        elif reason == "amount_too_small":
            await interaction.followup.send(
                f"Amount too small — you would receive 0 {to_currency}.", ephemeral=True
            )
        elif reason == "insufficient_balance":
            await interaction.followup.send(f"Not enough {from_currency}.", ephemeral=True)
        else:
            await interaction.followup.send(f"Exchange failed: {reason}.", ephemeral=True)

    @app_commands.command(name="pal-dice", description="Roll dice against the bot and bet Boins")
    @app_commands.describe(bet="Boins to wager (minimum 10)")
    async def pal_dice(self, interaction: discord.Interaction, bet: int) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await game_service.play_dice(str(interaction.user.id), bet)
        if "error" in result:
            await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
            return
        outcome_str = {"win": "You win!", "tie": "Tie — bet returned.", "lose": "You lose."}[
            result["outcome"]
        ]
        embed = discord.Embed(title="🎲 Dice Roll", color=_outcome_color(result["outcome"]))
        embed.add_field(name="Your Roll", value=str(result["player_roll"]), inline=True)
        embed.add_field(name="Bot Roll", value=str(result["bot_roll"]), inline=True)
        embed.add_field(
            name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="pal-rps", description="Rock, paper, scissors against the bot for Boins"
    )
    @app_commands.describe(choice="Your pick", bet="Boins to wager (minimum 10)")
    @app_commands.choices(
        choice=[
            app_commands.Choice(name="Rock", value="rock"),
            app_commands.Choice(name="Paper", value="paper"),
            app_commands.Choice(name="Scissors", value="scissors"),
        ]
    )
    async def pal_rps(self, interaction: discord.Interaction, choice: str, bet: int) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await game_service.play_rps(str(interaction.user.id), choice, bet)
        if "error" in result:
            await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
            return
        outcome_str = {"win": "You win!", "tie": "Tie — bet returned.", "lose": "You lose."}[
            result["outcome"]
        ]
        embed = discord.Embed(
            title="✂️ Rock Paper Scissors", color=_outcome_color(result["outcome"])
        )
        embed.add_field(name="Your Pick", value=choice.capitalize(), inline=True)
        embed.add_field(name="Bot Pick", value=result["bot_choice"].capitalize(), inline=True)
        embed.add_field(
            name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="pal-roulette", description="Spin the roulette wheel and bet Boins")
    @app_commands.describe(bet_type="What to bet on", bet="Boins to wager (minimum 10)")
    @app_commands.choices(
        bet_type=[
            app_commands.Choice(name="Red (2×)", value="red"),  # noqa: RUF001
            app_commands.Choice(name="Black (2×)", value="black"),  # noqa: RUF001
            app_commands.Choice(name="Green / 0 (14×)", value="green"),  # noqa: RUF001
            app_commands.Choice(name="1st Dozen 1–12 (3×)", value="1st dozen"),  # noqa: RUF001
            app_commands.Choice(name="2nd Dozen 13–24 (3×)", value="2nd dozen"),  # noqa: RUF001
            app_commands.Choice(name="3rd Dozen 25–36 (3×)", value="3rd dozen"),  # noqa: RUF001
        ]
    )
    async def pal_roulette(
        self, interaction: discord.Interaction, bet_type: str, bet: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await game_service.play_roulette(str(interaction.user.id), bet_type, bet)
        if "error" in result:
            await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
            return
        outcome_str = {"win": "You win!", "lose": "You lose."}[result["outcome"]]
        embed = discord.Embed(title="🎰 Roulette", color=_outcome_color(result["outcome"]))
        embed.add_field(name="Spin", value=str(result["spin"]), inline=True)
        embed.add_field(name="Bet", value=bet_type.capitalize(), inline=True)
        embed.add_field(
            name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="pal-guess", description="Guess a number 1–10 and bet Boins")  # noqa: RUF001
    @app_commands.describe(number="Your guess (1–10)", bet="Boins to wager (minimum 10)")  # noqa: RUF001
    async def pal_guess(
        self,
        interaction: discord.Interaction,
        number: app_commands.Range[int, 1, 10],
        bet: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await game_service.play_guess(str(interaction.user.id), number, bet)
        if "error" in result:
            await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
            return
        outcome_str = {"win": "Correct! You win!", "lose": "Wrong number. You lose."}[
            result["outcome"]
        ]
        embed = discord.Embed(title="🔢 Number Guess", color=_outcome_color(result["outcome"]))
        embed.add_field(name="Your Guess", value=str(number), inline=True)
        embed.add_field(name="Bot's Number", value=str(result["bot_number"]), inline=True)
        embed.add_field(
            name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
