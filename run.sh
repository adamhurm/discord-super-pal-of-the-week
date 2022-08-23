#!/bin/sh
tmux new-session -d -s octoSession -n octoWindow
tmux send-keys -t octoSession:octoWindow "cd /home/discord-super-pal-of-the-week && python3 discord-super-pal-of-the-week.py " Enter
tmux split-window -v
tmux send-keys -t octoSession:octoWindow "export NVM_DIR=\"$HOME/.nvm\" && [ -s \"$NVM_DIR/nvm.sh\" ] && \. \"$NVM_DIR/nvm.sh\" && cd /home/discord-super-pal-of-the-week/discord-spin-the-wheel && node dist/bot.js" Enter
tmux attach -t octoSession:octoWindow
