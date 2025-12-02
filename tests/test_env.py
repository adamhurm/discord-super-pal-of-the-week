"""Tests for superpal.env module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_env_variables_loaded(mock_env, monkeypatch):
    """Test that environment variables are loaded correctly."""
    # Need to reload module after setting env vars
    import importlib
    from superpal import env as superpal_env
    importlib.reload(superpal_env)

    assert superpal_env.TOKEN == 'test_token_12345'
    # GUILD_ID is now optional, but should still load if provided
    assert superpal_env.GUILD_ID == 123456789
    assert superpal_env.CHANNEL_ID == 987654321
    assert superpal_env.OPENAI_API_KEY == 'sk-test-key-12345'


def test_logger_exists(mock_env):
    """Test that logger is properly configured."""
    import importlib
    from superpal import env as superpal_env
    importlib.reload(superpal_env)

    assert superpal_env.log is not None
    assert superpal_env.log.name == 'super-pal'


def test_emoji_guild_id_fallback(monkeypatch):
    """Test that EMOJI_GUILD_ID falls back to GUILD_ID when not set."""
    # Set up minimal env
    monkeypatch.setenv('SUPERPAL_TOKEN', 'test_token')
    monkeypatch.setenv('GUILD_ID', '123456')
    monkeypatch.setenv('CHANNEL_ID', '789012')
    monkeypatch.setenv('EMOJI_GUILD_ID', '123456')
    monkeypatch.setenv('OPENAI_API_KEY', 'test_key')
    monkeypatch.setenv('GPT_ASSISTANT_ID', 'asst_test')
    monkeypatch.setenv('GPT_ASSISTANT_THREAD_ID', 'thread_test')

    import importlib
    from superpal import env as superpal_env
    importlib.reload(superpal_env)

    # Should use GUILD_ID value
    assert superpal_env.EMOJI_GUILD_ID == 123456


def test_art_channel_id_fallback(monkeypatch):
    """Test that ART_CHANNEL_ID falls back to CHANNEL_ID when not set."""
    monkeypatch.setenv('SUPERPAL_TOKEN', 'test_token')
    monkeypatch.setenv('GUILD_ID', '123456')
    monkeypatch.setenv('CHANNEL_ID', '789012')
    monkeypatch.setenv('EMOJI_GUILD_ID', '123456')
    monkeypatch.setenv('ART_CHANNEL_ID', '789012')
    monkeypatch.setenv('OPENAI_API_KEY', 'test_key')
    monkeypatch.setenv('GPT_ASSISTANT_ID', 'asst_test')
    monkeypatch.setenv('GPT_ASSISTANT_THREAD_ID', 'thread_test')

    import importlib
    from superpal import env as superpal_env
    importlib.reload(superpal_env)

    # Should use CHANNEL_ID value
    assert superpal_env.ART_CHANNEL_ID == 789012


def test_guild_id_optional(monkeypatch):
    """Test that GUILD_ID is optional and can be None for multi-guild mode."""
    monkeypatch.setenv('SUPERPAL_TOKEN', 'test_token')
    monkeypatch.setenv('CHANNEL_ID', '789012')
    # Don't set GUILD_ID
    monkeypatch.delenv('GUILD_ID', raising=False)

    import importlib
    from superpal import env as superpal_env
    importlib.reload(superpal_env)

    # GUILD_ID should be None when not set
    assert superpal_env.GUILD_ID is None
    # Other required variables should still work
    assert superpal_env.TOKEN == 'test_token'
    assert superpal_env.CHANNEL_ID == 789012
