"""Tests for superpal.ai module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import discord
from discord.ext import commands


@pytest.mark.asyncio
async def test_is_member_super_pal_true(mock_env):
    """Test checking if a member is super pal (positive case)."""
    from superpal import ai as superpal_ai

    # Create mocks
    mock_bot = Mock(spec=commands.Bot)
    mock_guild = Mock(spec=discord.Guild)
    mock_member = Mock(spec=discord.Member)
    mock_role = Mock(spec=discord.Role)
    mock_role.name = "Super Pal of the Week"

    mock_member.roles = [mock_role]
    mock_guild.members = [mock_member]
    mock_guild.roles = [mock_role]

    mock_bot.get_guild = Mock(return_value=mock_guild)
    discord.utils.get = Mock(side_effect=[mock_member, mock_role])

    with patch('superpal.ai.superpal_env') as mock_superpal_env:
        mock_superpal_env.GUILD_ID = 123456789
        result = await superpal_ai.is_member_super_pal(mock_bot, "TestUser")

    assert "Yes" in result
    assert "TestUser" in result


@pytest.mark.asyncio
async def test_is_member_super_pal_false(mock_env):
    """Test checking if a member is super pal (negative case)."""
    from superpal import ai as superpal_ai

    # Create mocks
    mock_bot = Mock(spec=commands.Bot)
    mock_guild = Mock(spec=discord.Guild)
    mock_member = Mock(spec=discord.Member)
    mock_role = Mock(spec=discord.Role)
    mock_role.name = "Super Pal of the Week"

    mock_member.roles = []  # Member doesn't have the role
    mock_guild.members = [mock_member]
    mock_guild.roles = [mock_role]

    mock_bot.get_guild = Mock(return_value=mock_guild)
    discord.utils.get = Mock(side_effect=[mock_member, mock_role])

    with patch('superpal.ai.superpal_env') as mock_superpal_env:
        mock_superpal_env.GUILD_ID = 123456789
        result = await superpal_ai.is_member_super_pal(mock_bot, "TestUser")

    assert "No" in result
    assert "TestUser" in result


@pytest.mark.asyncio
async def test_generate_surprise_image_success(mock_env, mock_channel):
    """Test successful image generation."""
    from superpal import ai as superpal_ai
    import base64

    # Create mock response
    mock_response = {
        'data': [
            {'b64_json': base64.b64encode(b'fake_image_1').decode()},
            {'b64_json': base64.b64encode(b'fake_image_2').decode()},
            {'b64_json': base64.b64encode(b'fake_image_3').decode()},
            {'b64_json': base64.b64encode(b'fake_image_4').decode()},
        ]
    }

    mock_client = AsyncMock()
    mock_client.images.generate = AsyncMock(return_value=mock_response)

    with patch('superpal.ai.AsyncOpenAI', return_value=mock_client):
        with patch('superpal.ai.superpal_env') as mock_superpal_env:
            mock_superpal_env.OPENAI_API_KEY = 'test_key'
            await superpal_ai.generate_surprise_image_and_send("a cat", mock_channel)

    # Verify client was called with correct params
    mock_client.images.generate.assert_called_once()
    call_kwargs = mock_client.images.generate.call_args.kwargs
    assert call_kwargs['prompt'] == "a cat"
    assert call_kwargs['n'] == 4
    assert call_kwargs['size'] == "1024x1024"

    # Verify channel.send was called
    mock_channel.send.assert_called_once()


@pytest.mark.asyncio
async def test_generate_surprise_image_safety_rejection(mock_env, mock_channel):
    """Test handling of safety system rejection."""
    from superpal import ai as superpal_ai
    import openai

    mock_client = AsyncMock()
    mock_client.images.generate = AsyncMock(
        side_effect=openai.APIError("Your request was rejected as a result of our safety system.")
    )

    with patch('superpal.ai.AsyncOpenAI', return_value=mock_client):
        with patch('superpal.ai.superpal_env') as mock_superpal_env:
            mock_superpal_env.OPENAI_API_KEY = 'test_key'
            mock_superpal_env.log = Mock()
            await superpal_ai.generate_surprise_image_and_send("inappropriate content", mock_channel)

    # Verify appropriate error message was sent
    mock_channel.send.assert_called_once()
    call_args = mock_channel.send.call_args[0][0]
    assert "Safety" in call_args or "nasty nelly" in call_args


@pytest.mark.asyncio
async def test_generate_surprise_image_billing_error(mock_env, mock_channel):
    """Test handling of billing limit error."""
    from superpal import ai as superpal_ai
    import openai

    mock_client = AsyncMock()
    mock_client.images.generate = AsyncMock(
        side_effect=openai.APIError("Billing hard limit has been reached")
    )

    with patch('superpal.ai.AsyncOpenAI', return_value=mock_client):
        with patch('superpal.ai.superpal_env') as mock_superpal_env:
            mock_superpal_env.OPENAI_API_KEY = 'test_key'
            mock_superpal_env.log = Mock()
            await superpal_ai.generate_surprise_image_and_send("a dog", mock_channel)

    # Verify appropriate error message was sent
    mock_channel.send.assert_called_once()
    call_args = mock_channel.send.call_args[0][0]
    assert "broke" in call_args or "afford" in call_args
