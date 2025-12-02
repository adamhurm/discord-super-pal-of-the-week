# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Discord Super Pal of the Week Bot.

This spec file can be used to build standalone executables for the bot.
Usage: pyinstaller discord-super-pal.spec
"""

import sys
from PyInstaller.utils.hooks import collect_all

# Determine platform-specific settings
if sys.platform == 'darwin':
    platform_name = 'macos'
elif sys.platform == 'win32':
    platform_name = 'windows'
else:
    platform_name = 'linux'

# Collect all discord and aiohttp dependencies
discord_datas, discord_binaries, discord_hiddenimports = collect_all('discord')
aiohttp_datas, aiohttp_binaries, aiohttp_hiddenimports = collect_all('aiohttp')

a = Analysis(
    ['src/bot.py'],
    pathex=['src'],
    binaries=discord_binaries + aiohttp_binaries,
    datas=discord_datas + aiohttp_datas,
    hiddenimports=[
        'discord',
        'discord.ext.commands',
        'discord.ext.tasks',
        'discord.app_commands',
        'openai',
        'superpal.ai',
        'superpal.env',
        'superpal.static',
        'aiohttp',
        'async_timeout',
        'multidict',
        'yarl',
        'typing_extensions',
    ] + discord_hiddenimports + aiohttp_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f'discord-super-pal-{platform_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
