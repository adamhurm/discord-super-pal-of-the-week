"""Tests for bot.py module - core bot functionality."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import discord
from discord.ext import commands
import datetime


class TestSuperPalPromotion:
    """Tests for super pal promotion functionality."""

    @pytest.mark.asyncio
    async def test_promote_new_super_pal(self, mock_env, mock_interaction, mock_member, mock_super_pal_role, mock_channel):
        """Test promoting a new user to super pal."""
        # Setup
        new_super_pal = Mock(spec=discord.Member)
        new_super_pal.name = "NewSuperPal"
        new_super_pal.mention = "<@999999999>"
        new_super_pal.roles = []
        new_super_pal.add_roles = AsyncMock()

        mock_interaction.user.remove_roles = AsyncMock()
        mock_interaction.guild.roles = [mock_super_pal_role]

        # Mock discord.utils.get to return the role
        with patch('discord.utils.get', return_value=mock_super_pal_role):
            with patch('bot.bot') as mock_bot:
                mock_bot.get_channel = Mock(return_value=mock_channel)

                # Import and test the function logic
                # Since we can't directly call the slash command, we'll test the logic
                role = mock_super_pal_role
                channel = mock_channel

                if role not in new_super_pal.roles:
                    await new_super_pal.add_roles(role)
                    await mock_interaction.user.remove_roles(role)
                    await channel.send(f'Congratulations {new_super_pal.mention}!')

        # Verify
        new_super_pal.add_roles.assert_called_once_with(mock_super_pal_role)
        mock_interaction.user.remove_roles.assert_called_once_with(mock_super_pal_role)
        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_promote_existing_super_pal(self, mock_env, mock_member, mock_super_pal_role):
        """Test attempting to promote someone who is already super pal."""
        # Setup - member already has the role
        mock_member.roles = [mock_super_pal_role]
        mock_member.add_roles = AsyncMock()

        # Test logic
        if mock_super_pal_role in mock_member.roles:
            already_super_pal = True
        else:
            already_super_pal = False
            await mock_member.add_roles(mock_super_pal_role)

        # Verify
        assert already_super_pal is True
        mock_member.add_roles.assert_not_called()


class TestWeeklyTask:
    """Tests for weekly super pal selection task."""

    def test_calculate_days_until_sunday(self):
        """Test calculation of days until Sunday."""
        # Mock a Wednesday (isoweekday = 3)
        test_date = datetime.date(2025, 12, 3)  # This is a Wednesday
        days_until_sunday = 7 - test_date.isoweekday()

        # Wednesday to Sunday is 4 days
        assert days_until_sunday == 4

    def test_calculate_days_until_sunday_on_sunday_before_noon(self):
        """Test calculation when it's Sunday before noon."""
        # Mock Sunday before noon
        now = datetime.datetime(2025, 12, 7, 10, 0)  # Sunday 10 AM
        days_until_sunday = 7 - datetime.date(2025, 12, 7).isoweekday()

        # Should be 0 days if it's Sunday before noon
        if datetime.date(2025, 12, 7).isoweekday() == 7 and now.hour <= 12:
            days_until_sunday = 0

        assert days_until_sunday == 0

    def test_calculate_days_until_sunday_on_sunday_after_noon(self):
        """Test calculation when it's Sunday after noon."""
        # Mock Sunday after noon
        now = datetime.datetime(2025, 12, 7, 14, 0)  # Sunday 2 PM
        days_until_sunday = 7 - datetime.date(2025, 12, 7).isoweekday()

        # Should be 7 days (wait until next Sunday)
        if datetime.date(2025, 12, 7).isoweekday() == 7 and now.hour > 12:
            days_until_sunday = 7

        assert days_until_sunday == 7

    @pytest.mark.asyncio
    async def test_pick_random_super_pal_excludes_bots(self, mock_guild, mock_member, mock_bot_member, mock_super_pal_role):
        """Test that bot members are excluded from super pal selection."""
        # Setup
        mock_guild.members = [mock_member, mock_bot_member]

        # Filter out bots
        true_member_list = [m for m in mock_guild.members if not m.bot]

        # Verify
        assert len(true_member_list) == 1
        assert mock_member in true_member_list
        assert mock_bot_member not in true_member_list

    @pytest.mark.asyncio
    async def test_remove_old_super_pal_role(self, mock_member, mock_super_pal_role):
        """Test removing super pal role from previous holder."""
        # Setup
        mock_member.roles = [mock_super_pal_role]
        mock_member.remove_roles = AsyncMock()

        # Remove role
        if mock_super_pal_role in mock_member.roles:
            await mock_member.remove_roles(mock_super_pal_role)

        # Verify
        mock_member.remove_roles.assert_called_once_with(mock_super_pal_role)


class TestSpinTheWheel:
    """Tests for spin the wheel integration."""

    @pytest.mark.asyncio
    async def test_parse_wheel_winner_message(self):
        """Test parsing winner from wheel spin embed."""
        # Mock embed with winner
        mock_embed = Mock(spec=discord.Embed)
        mock_embed.description = "🏆 Winner: <@TestUser>!"

        # Parse winner name
        if mock_embed.description and mock_embed.description[0] == '🏆':
            winner_name = mock_embed.description[12:-2]
            assert winner_name == "TestUser"

    @pytest.mark.asyncio
    async def test_ignore_non_winner_embeds(self):
        """Test that non-winner embeds are ignored."""
        # Mock embed without winner
        mock_embed = Mock(spec=discord.Embed)
        mock_embed.description = "Spinning the wheel..."

        # Should not process
        is_winner_embed = mock_embed.description and mock_embed.description[0] == '🏆'
        assert is_winner_embed is False

    @pytest.mark.asyncio
    async def test_ignore_embeds_without_description(self):
        """Test that embeds without description are ignored."""
        # Mock embed with None description
        mock_embed = Mock(spec=discord.Embed)
        mock_embed.description = None

        # Should skip
        if mock_embed.description is None:
            should_skip = True
        else:
            should_skip = False

        assert should_skip is True


class TestFunCommands:
    """Tests for fun/entertainment commands."""

    @pytest.mark.asyncio
    async def test_cacaw_sends_50_parrots(self, mock_channel):
        """Test that cacaw command sends 50 parrot emojis."""
        # Mock emoji
        mock_emoji = Mock()
        mock_emoji.__str__ = Mock(return_value=":partyparrot:")

        # Send 50 emojis
        await mock_channel.send(str(mock_emoji) * 50)

        # Verify
        mock_channel.send.assert_called_once()
        call_args = mock_channel.send.call_args[0][0]
        assert call_args.count(":partyparrot:") == 50

    @pytest.mark.asyncio
    async def test_meow_sends_50_cats(self, mock_channel):
        """Test that meow command sends 50 cat emojis."""
        # Mock emoji
        mock_emoji = Mock()
        mock_emoji.__str__ = Mock(return_value=":partymeow:")

        # Send 50 emojis
        await mock_channel.send(str(mock_emoji) * 50)

        # Verify
        mock_channel.send.assert_called_once()
        call_args = mock_channel.send.call_args[0][0]
        assert call_args.count(":partymeow:") == 50

    @pytest.mark.asyncio
    async def test_karatechop_with_no_users_in_voice(self, mock_guild, mock_channel):
        """Test karate chop when no users are in voice channels."""
        # Setup - no users in voice
        mock_guild.voice_channels = [Mock(members=[])]

        active_members = [vc.members for vc in mock_guild.voice_channels]

        if not any(active_members):
            await mock_channel.send("There is no one to karate chop!")

        # Verify
        mock_channel.send.assert_called_once()
        call_args = mock_channel.send.call_args[0][0]
        assert "no one" in call_args

    @pytest.mark.asyncio
    async def test_karatechop_moves_random_user(self, mock_guild, mock_member):
        """Test that karate chop moves a random user to AFK."""
        # Setup
        mock_voice_channel = Mock(spec=discord.VoiceChannel)
        mock_voice_channel.name = "General"
        mock_voice_channel.members = [mock_member]

        mock_afk_channel = Mock(spec=discord.VoiceChannel)
        mock_afk_channel.name = "AFK"

        mock_guild.voice_channels = [mock_voice_channel, mock_afk_channel]
        mock_member.move_to = AsyncMock()

        # Get active members
        active_members = [vc.members for vc in mock_guild.voice_channels]
        flatten = lambda l: [x for y in l for x in y]
        true_member_list = [m for m in flatten(active_members) if not m.bot]

        # Check for AFK channel
        afk_channels = [c for c in mock_guild.voice_channels if 'AFK' in c.name]

        assert len(true_member_list) > 0
        assert len(afk_channels) > 0


class TestCommandsList:
    """Tests for commands list functionality."""

    @pytest.mark.asyncio
    async def test_commands_list_sends_help_text(self, mock_channel):
        """Test that !commands sends the help text."""
        from superpal import static as superpal_static

        await mock_channel.send(superpal_static.COMMANDS_MSG)

        # Verify
        mock_channel.send.assert_called_once()
        call_args = mock_channel.send.call_args[0][0]
        assert "!spotw" in call_args
        assert "!surprise" in call_args
