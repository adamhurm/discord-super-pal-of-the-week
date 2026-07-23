"""Palymarket prediction-market commands."""

import discord
from discord import app_commands
from discord.ext import commands

import superpal.env as superpal_env
import superpal.palymarket.service as palymarket_svc
from superpal.cogs.helpers import _is_clippy

log = superpal_env.log


class PalymarketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="palymarket-propose", description="Propose a new prediction market"
    )
    @app_commands.describe(title="Short title for the market", description="Full description")
    async def palymarket_propose(
        self, interaction: discord.Interaction, title: str, description: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        market = await palymarket_svc.propose_market(title, description, str(interaction.user.id))
        await interaction.followup.send(
            f"Market proposed! Admins will review it shortly. ID: {market.id}", ephemeral=True
        )
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"📊 New market proposed by {interaction.user.mention}: "
                f"**{market.title}** (ID: {market.id}) — awaiting admin approval"
            )

    @app_commands.command(
        name="palymarket-bet", description="Place or update a bet on a market"
    )
    @app_commands.describe(market_id="Market ID to bet on", amount="Amount of Palycoins to bet")
    @app_commands.choices(
        side=[
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ]
    )
    async def palymarket_bet(
        self,
        interaction: discord.Interaction,
        market_id: int,
        side: app_commands.Choice[str],
        amount: app_commands.Range[int, 1],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await palymarket_svc.get_palycoin_balance(str(interaction.user.id))
        success, reason = await palymarket_svc.place_or_update_bet(
            market_id, str(interaction.user.id), side.value, amount
        )
        if success:
            await interaction.followup.send(
                f"Bet placed! {amount} Palycoins on {side.value.upper()} for market #{market_id}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(reason, ephemeral=True)

    @app_commands.command(
        name="palymarket-list", description="List all open prediction markets"
    )
    async def palymarket_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        markets = await palymarket_svc.list_markets(status="open")
        if not markets:
            await interaction.followup.send("No open markets right now.", ephemeral=True)
            return
        embed = discord.Embed(title="📊 Open Palymarkets")
        for m in markets:
            embed.add_field(
                name=f"#{m.id}: {m.title}",
                value=f"YES: {m.yes_pool} | NO: {m.no_pool}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="palymarket-balance", description="Check your Palycoin balance and bets"
    )
    async def palymarket_balance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        balance = await palymarket_svc.get_palycoin_balance(str(interaction.user.id))
        active_bets = await palymarket_svc.get_player_active_bets(str(interaction.user.id))
        embed = discord.Embed(title="📊 Palycoin Balance")
        embed.add_field(name="Balance", value=f"{balance} Palycoins", inline=False)
        if active_bets:
            bet_lines = [
                f"#{market.id} **{market.title}**: {bet.amount} on {bet.side.upper()}"
                for market, bet in active_bets
            ]
            embed.add_field(name="Active Bets", value="\n".join(bet_lines), inline=False)
        else:
            embed.add_field(name="Active Bets", value="None", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="palymarket-approve", description="[Admin] Approve a pending market"
    )
    @app_commands.describe(market_id="Market ID to approve")
    @app_commands.check(_is_clippy)
    async def palymarket_approve(
        self, interaction: discord.Interaction, market_id: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        success, reason = await palymarket_svc.approve_market(market_id, str(interaction.user.id))
        if success:
            await interaction.followup.send(f"Market #{market_id} approved.", ephemeral=True)
            if isinstance(interaction.channel, discord.abc.Messageable):
                await interaction.channel.send(
                    f"📊 Market #{market_id} is now OPEN for betting!"
                )
        else:
            await interaction.followup.send(
                f"Could not approve market #{market_id}: {reason}", ephemeral=True
            )

    @app_commands.command(
        name="palymarket-reject", description="[Admin] Reject a pending market"
    )
    @app_commands.describe(market_id="Market ID to reject", reason="Reason for rejection")
    @app_commands.check(_is_clippy)
    async def palymarket_reject(
        self, interaction: discord.Interaction, market_id: int, reason: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        success, _ = await palymarket_svc.reject_market(market_id, str(interaction.user.id))
        if success:
            await interaction.followup.send(f"Market #{market_id} rejected.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"Could not reject market #{market_id}.", ephemeral=True
            )

    @app_commands.command(
        name="palymarket-close", description="[Admin] Close a market to new bets"
    )
    @app_commands.describe(market_id="Market ID to close")
    @app_commands.check(_is_clippy)
    async def palymarket_close(self, interaction: discord.Interaction, market_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        success, reason = await palymarket_svc.close_market(market_id, str(interaction.user.id))
        if success:
            await interaction.followup.send(f"Market #{market_id} closed.", ephemeral=True)
            if isinstance(interaction.channel, discord.abc.Messageable):
                await interaction.channel.send(
                    f"📊 Market #{market_id} is now CLOSED. No more bets accepted."
                )
        else:
            await interaction.followup.send(
                f"Could not close market #{market_id}: {reason}", ephemeral=True
            )

    @app_commands.command(
        name="palymarket-resolve", description="[Admin] Resolve a market and pay winners"
    )
    @app_commands.describe(market_id="Market ID to resolve")
    @app_commands.choices(
        outcome=[
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ]
    )
    @app_commands.check(_is_clippy)
    async def palymarket_resolve(
        self,
        interaction: discord.Interaction,
        market_id: int,
        outcome: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await palymarket_svc.resolve_market(
            market_id, outcome.value, str(interaction.user.id)
        )
        if "error" in result:
            await interaction.followup.send(
                f"Could not resolve market #{market_id}: {result['error']}", ephemeral=True
            )
            return
        await interaction.followup.send(f"Market #{market_id} resolved.", ephemeral=True)
        if isinstance(interaction.channel, discord.abc.Messageable):
            embed = discord.Embed(title=f"📊 Market #{market_id} Resolved")
            embed.add_field(name="Outcome", value=result["outcome"].upper(), inline=True)
            embed.add_field(name="Total Pool", value=str(result["total_pool"]), inline=True)
            embed.add_field(name="Winners", value=str(result["winner_count"]), inline=True)
            top_payouts = sorted(result["payouts"], key=lambda p: p["payout"], reverse=True)[:5]
            if top_payouts:
                payout_lines = [
                    f"<@{p['player_id']}> → {p['payout']} Palycoins" for p in top_payouts
                ]
                embed.add_field(name="Top Payouts", value="\n".join(payout_lines), inline=False)
            await interaction.channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PalymarketCog(bot))
