"""Tests for superpal.ai module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from unittest.mock import AsyncMock, Mock, patch
import discord
from discord.ext import commands


@pytest.mark.asyncio
async def test_is_member_super_pal_true(mock_env):
    """Test checking if a member is super pal (positive case)."""
    from superpal import ai as superpal_ai

    # Create mocks
    mock_guild = Mock(spec=discord.Guild)
    mock_member = Mock(spec=discord.Member)
    mock_role = Mock(spec=discord.Role)
    mock_role.name = "Super Pal of the Week"

    mock_member.roles = [mock_role]
    mock_guild.members = [mock_member]
    mock_guild.roles = [mock_role]

    discord.utils.get = Mock(side_effect=[mock_member, mock_role])

    result = await superpal_ai.is_member_super_pal(mock_guild, "TestUser")

    assert "Yes" in result
    assert "TestUser" in result


@pytest.mark.asyncio
async def test_is_member_super_pal_false(mock_env):
    """Test checking if a member is super pal (negative case)."""
    from superpal import ai as superpal_ai

    # Create mocks
    mock_guild = Mock(spec=discord.Guild)
    mock_member = Mock(spec=discord.Member)
    mock_role = Mock(spec=discord.Role)
    mock_role.name = "Super Pal of the Week"

    mock_member.roles = []  # Member doesn't have the role
    mock_guild.members = [mock_member]
    mock_guild.roles = [mock_role]

    discord.utils.get = Mock(side_effect=[mock_member, mock_role])

    result = await superpal_ai.is_member_super_pal(mock_guild, "TestUser")

    assert "No" in result
    assert "TestUser" in result
