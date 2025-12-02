"""Pytest configuration and shared fixtures for test suite."""
import os
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock
import discord
from discord.ext import commands


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables."""
    env_vars = {
        'SUPERPAL_TOKEN': 'test_token_12345',
        'GUILD_ID': '123456789',
        'EMOJI_GUILD_ID': '123456789',
        'CHANNEL_ID': '987654321',
        'ART_CHANNEL_ID': '987654322',
        'GPT_ASSISTANT_ID': 'asst_test123',
        'GPT_ASSISTANT_THREAD_ID': 'thread_test123',
        'OPENAI_API_KEY': 'sk-test-key-12345'
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def mock_guild():
    """Create a mock Discord guild."""
    guild = Mock(spec=discord.Guild)
    guild.id = 123456789
    guild.name = "Test Guild"
    guild.roles = []
    guild.members = []
    guild.voice_channels = []
    return guild


@pytest.fixture
def mock_super_pal_role():
    """Create a mock Super Pal of the Week role."""
    role = Mock(spec=discord.Role)
    role.id = 111111111
    role.name = "Super Pal of the Week"
    return role


@pytest.fixture
def mock_member():
    """Create a mock Discord member."""
    member = Mock(spec=discord.Member)
    member.id = 222222222
    member.name = "TestUser"
    member.mention = "<@222222222>"
    member.bot = False
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


@pytest.fixture
def mock_bot_member():
    """Create a mock bot member."""
    bot = Mock(spec=discord.Member)
    bot.id = 333333333
    bot.name = "BotUser"
    bot.mention = "<@333333333>"
    bot.bot = True
    bot.roles = []
    return bot


@pytest.fixture
def mock_channel():
    """Create a mock Discord text channel."""
    channel = Mock(spec=discord.TextChannel)
    channel.id = 987654321
    channel.name = "super-pal-channel"
    channel.send = AsyncMock()
    return channel


@pytest.fixture
def mock_interaction(mock_member, mock_guild):
    """Create a mock Discord interaction."""
    interaction = Mock(spec=discord.Interaction)
    interaction.user = mock_member
    interaction.guild = mock_guild
    interaction.response = Mock()
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.fixture
def mock_message(mock_member, mock_channel):
    """Create a mock Discord message."""
    message = Mock(spec=discord.Message)
    message.id = 444444444
    message.author = mock_member
    message.channel = mock_channel
    message.content = "Test message"
    message.embeds = []
    return message


@pytest.fixture
def mock_voice_channel():
    """Create a mock Discord voice channel."""
    voice_channel = Mock(spec=discord.VoiceChannel)
    voice_channel.id = 555555555
    voice_channel.name = "General Voice"
    voice_channel.members = []
    return voice_channel


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot."""
    intents = discord.Intents.default()
    bot = Mock(spec=commands.Bot)
    bot.command_prefix = '!'
    bot.intents = intents
    bot.get_guild = Mock(return_value=None)
    bot.get_channel = Mock(return_value=None)
    bot.tree = Mock()
    bot.tree.sync = AsyncMock()
    return bot


@pytest.fixture
def mock_openai_client():
    """Create a mock OpenAI client."""
    client = AsyncMock()
    client.images = AsyncMock()
    client.images.generate = AsyncMock()
    client.beta = AsyncMock()
    client.beta.assistants = AsyncMock()
    client.beta.threads = AsyncMock()
    return client
