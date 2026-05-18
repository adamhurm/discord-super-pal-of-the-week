"""Tests for superpal.static module."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from superpal import static as superpal_static


def test_commands_message_exists():
    """Test that COMMANDS_MSG contains command documentation."""
    assert superpal_static.COMMANDS_MSG is not None
    assert len(superpal_static.COMMANDS_MSG) > 0
    assert "!spotw" in superpal_static.COMMANDS_MSG
    assert "!spinthewheel" in superpal_static.COMMANDS_MSG
    assert "!cacaw" in superpal_static.COMMANDS_MSG
    assert "!meow" in superpal_static.COMMANDS_MSG
    assert "!karatechop" in superpal_static.COMMANDS_MSG


def test_welcome_message_exists():
    """Test that WELCOME_MSG exists and contains welcome information."""
    assert superpal_static.WELCOME_MSG is not None
    assert len(superpal_static.WELCOME_MSG) > 0
    assert "!commands" in superpal_static.WELCOME_MSG


def test_runtime_warn_message_exists():
    """Test that RUNTIME_WARN_MSG exists."""
    assert superpal_static.RUNTIME_WARN_MSG is not None
    assert len(superpal_static.RUNTIME_WARN_MSG) > 0
