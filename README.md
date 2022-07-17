# super-pal-of-the-week
Discord mod that promotes users to Super Pal of the Week role.

This just runs in a tmux session on a raspberry pi.

Currently this supports commands and the looped task.
- **Command:**      Accepts `!spotw @user` commands to add another person to super pal of the week.
- **Looped Task:**  Runs task on 7-day basis.

--------

## Setup

1. Ensure python3 is on your system and then install dependencies: `pip install -U discord.py python-dotenv`


2. Create a local file named `.env` and fill it with the following content:
```
DISCORD_TOKEN=
GUILD_ID=
CHANNEL_ID=
```

3. Run the python script: `python3 discord-super-pal-of-the-week.py`

#### DISCORD_TOKEN
- You can find or create a bot token in the [Discord developer portal](https://discord.com/developers/applications/).

- Choose your application -> Go to Bot section -> Look under "Token" section \
(This token can only be copied once so you may have to reset your token if you do not know it)

#### GUILD_ID and CHANNEL_ID
[Web Application](https://discord.com/app) (in-browser)

- Click on the text channel in your server.

- Look at your URL. It will be in the form of `https://discord.com/channels/[GUILD_ID]/[CHANNEL_ID]`



[Desktop Application](https://discord.com/download)

- Turn on `Developer Mode` in Settings -> Advanced

- GUILD_ID: Right-click on your server icon and select `Copy ID`

- CHANNEL_ID: Right-click on the text channel where you want posts from super-pal-of-the-week-manager and select `Copy ID`

