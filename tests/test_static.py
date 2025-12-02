"""Tests for superpal.static module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from superpal import static as superpal_static


def test_commands_message_exists():
    """Test that COMMANDS_MSG contains command documentation."""
    assert superpal_static.COMMANDS_MSG is not None
    assert len(superpal_static.COMMANDS_MSG) > 0
    assert "!spotw" in superpal_static.COMMANDS_MSG
    assert "!spinthewheel" in superpal_static.COMMANDS_MSG
    assert "!cacaw" in superpal_static.COMMANDS_MSG
    assert "!meow" in superpal_static.COMMANDS_MSG
    assert "!surprise" in superpal_static.COMMANDS_MSG
    assert "!karatechop" in superpal_static.COMMANDS_MSG


def test_gamble_message_exists():
    """Test that GAMBLE_MSG exists and contains gambling information."""
    assert superpal_static.GAMBLE_MSG is not None
    assert len(superpal_static.GAMBLE_MSG) > 0
    assert "1-800-522-4700" in superpal_static.GAMBLE_MSG


def test_welcome_message_exists():
    """Test that WELCOME_MSG exists and contains welcome information."""
    assert superpal_static.WELCOME_MSG is not None
    assert len(superpal_static.WELCOME_MSG) > 0
    assert "!commands" in superpal_static.WELCOME_MSG


def test_runtime_warn_message_exists():
    """Test that RUNTIME_WARN_MSG exists."""
    assert superpal_static.RUNTIME_WARN_MSG is not None
    assert len(superpal_static.RUNTIME_WARN_MSG) > 0


def test_gpt_prompt_message_exists():
    """Test that GPT_PROMPT_MSG exists and contains assistant prompt."""
    assert superpal_static.GPT_PROMPT_MSG is not None
    assert len(superpal_static.GPT_PROMPT_MSG) > 0
    assert "Super Pal Bot" in superpal_static.GPT_PROMPT_MSG


def test_gpt_assistant_tools_structure():
    """Test that GPT_ASSISTANT_TOOLS has correct structure."""
    assert superpal_static.GPT_ASSISTANT_TOOLS is not None
    assert isinstance(superpal_static.GPT_ASSISTANT_TOOLS, list)
    assert len(superpal_static.GPT_ASSISTANT_TOOLS) > 0

    # Check first tool structure
    tool = superpal_static.GPT_ASSISTANT_TOOLS[0]
    assert tool['type'] == 'function'
    assert 'function' in tool
    assert tool['function']['name'] == 'is_member_super_pal'
    assert 'parameters' in tool['function']
    assert 'member' in tool['function']['parameters']['properties']
