#!/bin/sh
cd /home/discord-super-pal-of-the-week \
&& python3 discord-super-pal-of-the-week.py &
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" \
&& cd /home/discord-super-pal-of-the-week/discord-spin-the-wheel \
&& node dist/bot.js
