"""Environment configuration and validation for Super Pal Bot.

This module handles loading environment variables, configuring logging,
and validating required configuration.
"""

import logging
import os
from typing import Optional
from dotenv import load_dotenv

from . import static as superpal_static

###########
# Logging #
###########
log = logging.getLogger('super-pal')
log.setLevel(logging.INFO)
log_handler = logging.FileHandler(filename='discord-super-pal.log', encoding='utf-8', mode='w')
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
log_handler.setFormatter(formatter)
log.addHandler(log_handler)

# Also log to console
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
log.addHandler(console_handler)

##################
# Env. variables #
##################
load_dotenv()


def get_env(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Get environment variable with optional default and required validation.

    Args:
        key: Environment variable name
        default: Default value if not found
        required: Whether this variable is required

    Returns:
        Environment variable value or default

    Raises:
        ValueError: If required=True and variable not found
    """
    value = os.environ.get(key, default)
    if required and value is None:
        raise ValueError(f"Required environment variable '{key}' not found")
    return value


def get_env_int(key: str, default: Optional[int] = None, required: bool = False) -> Optional[int]:
    """Get environment variable as integer.

    Args:
        key: Environment variable name
        default: Default value if not found
        required: Whether this variable is required

    Returns:
        Environment variable value as integer or default

    Raises:
        ValueError: If required=True and variable not found, or cannot convert to int
    """
    value = get_env(key, required=required)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        log.error(f"Environment variable '{key}' must be an integer, got '{value}'")
        raise


# Required variables
try:
    TOKEN = get_env('SUPERPAL_TOKEN', required=True)
    GUILD_ID = get_env_int('GUILD_ID', required=True)
    CHANNEL_ID = get_env_int('CHANNEL_ID', required=True)
except ValueError as e:
    log.error(f"Missing required environment variables: {e}")
    log.error(f"{superpal_static.RUNTIME_WARN_MSG}")
    TOKEN = None
    GUILD_ID = None
    CHANNEL_ID = None

# Optional variables with defaults
EMOJI_GUILD_ID = get_env_int('EMOJI_GUILD_ID', default=GUILD_ID)
ART_CHANNEL_ID = get_env_int('ART_CHANNEL_ID', default=CHANNEL_ID)
GPT_ASSISTANT_ID = get_env('GPT_ASSISTANT_ID')
GPT_ASSISTANT_THREAD_ID = get_env('GPT_ASSISTANT_THREAD_ID')
OPENAI_API_KEY = get_env('OPENAI_API_KEY')

# Validate AI requirements
if OPENAI_API_KEY is None:
    log.warning(
        'OpenAI requirements not fulfilled. AI features will not work. '
        f'Please provide OPENAI_API_KEY.\n{superpal_static.RUNTIME_WARN_MSG}'
    )

# Log configuration status
log.info("Environment configuration loaded successfully")
log.info(f"Guild ID: {GUILD_ID}")
log.info(f"Channel ID: {CHANNEL_ID}")
log.info(f"AI features enabled: {OPENAI_API_KEY is not None}")
