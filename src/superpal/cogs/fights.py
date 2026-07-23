"""Card fight commands: challenges, fight leaderboard, and fight expiry."""

import discord
from discord import app_commands
from discord.ext import commands, tasks

import superpal.env as superpal_env
from superpal.cards.fight_service import (
    FIGHT_TOKEN_EXPIRY_MINUTES,
    accept_fight,
    create_fight,
    create_fight_token,
    expire_inactive_fights,
    expire_pending_challenges,
    get_fight_leaderboard,
)
from superpal.env import WEBAPP_BASE_URL

log = superpal_env.log

FIGHT_CHALLENGE_TIMEOUT = FIGHT_TOKEN_EXPIRY_MINUTES * 60


class FightChallengeView(discord.ui.View):
    def __init__(
        self,
        fight_id: int,
        challenger_id: str,
        opponent_id: str,
        challenger_name: str,
        mode: str,
    ):
        super().__init__(timeout=FIGHT_CHALLENGE_TIMEOUT)
        self.fight_id = fight_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.challenger_name = challenger_name
        self.mode = mode
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.opponent_id:
            await interaction.response.send_message(
                "Only the challenged player can accept.", ephemeral=True
            )
            return
        self.stop()
        fight = await accept_fight(self.fight_id)
        if fight is None:
            await interaction.response.edit_message(
                content="This challenge has already expired or been resolved.", view=None
            )
            return

        await interaction.response.edit_message(
            content="Challenge accepted! DMs are on their way.", view=None
        )

        # DM both players their lobby magic links
        challenger_url = await create_fight_token(
            self.fight_id, self.challenger_id, WEBAPP_BASE_URL
        )
        opponent_url = await create_fight_token(self.fight_id, self.opponent_id, WEBAPP_BASE_URL)

        for uid, url in ((self.challenger_id, challenger_url), (self.opponent_id, opponent_url)):
            user = interaction.client.get_user(int(uid))
            if user:
                try:
                    other_name = (
                        self.challenger_name
                        if uid == self.opponent_id
                        else interaction.user.display_name
                    )
                    await user.send(
                        f"Your **{self.mode}** battle vs. **{other_name}** "
                        f"is ready!\n\nOpen the fight lobby: <{url}>",
                        suppress_embeds=True,
                    )
                except discord.Forbidden:
                    pass

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.opponent_id:
            await interaction.response.send_message(
                "Only the challenged player can decline.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(content="Challenge declined.", view=None)

    async def on_timeout(self) -> None:
        await expire_pending_challenges()
        if self.message:
            try:
                await self.message.edit(content="Fight challenge expired.", view=None)
            except discord.NotFound:
                pass


class FightsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if not self.fight_expiry.is_running():
            self.fight_expiry.start()

    async def cog_unload(self) -> None:
        self.fight_expiry.cancel()

    @tasks.loop(minutes=5)
    async def fight_expiry(self) -> None:
        """Expire stale challenges and inactive fights.

        The challenge View also expires on its timeout, but that timer is
        lost on process restart — this loop is the durable fallback.
        """
        try:
            await expire_pending_challenges()
            await expire_inactive_fights()
        except Exception as e:
            log.error("Error in fight_expiry task: %s", e)

    @fight_expiry.before_loop
    async def before_fight_expiry(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="card-fight", description="Challenge another player to a card battle"
    )
    @app_commands.describe(
        opponent="The player to challenge",
        mode="Battle mode: quick (1v1) or extended (3v3)",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Quick (1v1)", value="quick"),
            app_commands.Choice(name="Extended (3v3)", value="extended"),
        ]
    )
    async def card_fight_command(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        mode: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        challenger_id = str(interaction.user.id)
        opponent_id = str(opponent.id)

        if interaction.user.id == opponent.id:
            await interaction.followup.send("You can't fight yourself.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.followup.send("You can't challenge a bot.", ephemeral=True)
            return
        if not interaction.channel:
            await interaction.followup.send(
                "This command must be used in a server channel.", ephemeral=True
            )
            return

        fight = await create_fight(
            challenger_id=challenger_id,
            opponent_id=opponent_id,
            mode=mode,
            channel_id=str(interaction.channel_id),
        )

        view = FightChallengeView(
            fight_id=fight.id,
            challenger_id=challenger_id,
            opponent_id=opponent_id,
            challenger_name=interaction.user.display_name,
            mode=mode,
        )
        if not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.followup.send(
                "This command must be used in a server channel.", ephemeral=True
            )
            return
        channel_msg = await interaction.channel.send(
            content=(
                f"{opponent.mention}, **{interaction.user.display_name}** challenges you to a "
                f"**{mode.upper()} Battle**!\n\n"
                f"You have {FIGHT_TOKEN_EXPIRY_MINUTES} minutes to respond."
            ),
            view=view,
        )
        view.message = channel_msg
        await interaction.followup.send("Challenge sent!", ephemeral=True)

    @app_commands.command(
        name="card-fight-leaderboard", description="Show the top 10 fight stats"
    )
    @app_commands.describe(sort_by="What to rank players by")
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="Most Wins", value="wins"),
            app_commands.Choice(name="Best Win Rate", value="win_rate"),
            app_commands.Choice(name="Most Fights Played", value="fights_played"),
            app_commands.Choice(name="Pringle Balance", value="pringle_balance"),
            app_commands.Choice(name="Most Escapes", value="escapes"),
        ]
    )
    async def card_fight_leaderboard_command(
        self,
        interaction: discord.Interaction,
        sort_by: str = "wins",
    ) -> None:
        await interaction.response.defer()
        rows = await get_fight_leaderboard(sort_by)

        title_map = {
            "wins": "Most Wins",
            "win_rate": "Best Win Rate",
            "fights_played": "Most Fights Played",
            "pringle_balance": "Pringle Balance",
            "escapes": "Most Escapes",
        }
        unit_map = {
            "wins": "wins",
            "fights_played": "fights played",
            "pringle_balance": "Pringles",
            "escapes": "escapes",
        }
        title = f"Fight Leaderboard — {title_map.get(sort_by, 'Most Wins')}"

        if not rows:
            embed = discord.Embed(
                title=title,
                description="No data yet!",
                color=discord.Color(0x5865F2),
            )
        elif sort_by == "win_rate":
            lines = [
                f"{rank}. {row['display_name']} — "
                f"{round(row['total'] * 100)}% ({row['total_fights']} fights)"
                for rank, row in enumerate(rows, start=1)
            ]
            embed = discord.Embed(
                title=title, description="\n".join(lines), color=discord.Color(0x5865F2)
            )
        else:
            unit = unit_map.get(sort_by, "")
            lines = [
                f"{rank}. {row['display_name']} — {row['total']} {unit}"
                for rank, row in enumerate(rows, start=1)
            ]
            embed = discord.Embed(
                title=title, description="\n".join(lines), color=discord.Color(0x5865F2)
            )

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FightsCog(bot))
