# super-pal-of-the-week
Discord mod that promotes users to Super Pal of the Week role.

This just runs in a tmux session on a raspberry pi.

Currently this supports commands and the looped task.
- **Commands:**
  - `!spotw @user` : add another person to super pal of the week.
  - `!cacaw` : spam chat with partyparrot emojis.
  - `!meow` : spam chat with partycat emojis.
- **Looped Task:**
  - Pick new super pal every Sunday at noon (dependent on bot's timezone).

--------

## Step 1: Install dependencies
Ensure python3 is on your system and then install dependencies: `pip install -U discord.py python-dotenv`

<br/>

## Step 2: Create a local file named `.env` to hold your tokens and IDs:

### .env
```
DISCORD_TOKEN=
GUILD_ID=
CHANNEL_ID=
```

#### DISCORD\_TOKEN
- You can find or create a bot token in the [Discord developer portal](https://discord.com/developers/applications/).

- Choose your application -> Go to Bot section -> Look under "Token" section \
(This token can only be copied once so you may have to reset your token if you do not know it)

#### GUILD\_ID and CHANNEL\_ID
[Web Application](https://discord.com/app) (in-browser)

- Click on the text channel in your server.

- Look at your URL. It will be in the form of `https://discord.com/channels/[GUILD_ID]/[CHANNEL_ID]`

[Desktop Application](https://discord.com/download)

- Turn on `Developer Mode` in Settings -> Advanced

- GUILD\_ID: Right-click on your server icon and select `Copy ID`

- CHANNEL\_ID: Right-click on the text channel where you want posts from super-pal-of-the-week-manager and select `Copy ID`

<br/>

## Step 3: Configure Super Pal roles in your channel:

- Create a role named `super pal of the week` and add your desired elevated permissions (if any).

- Create a role that is one tier higher named `spotw-bot` and apply it to the Super Pal Bot.
  - This is required in order for the Super Pal Bot to apply the `super pal of the week` role.

<br/>

## Step 4: Create an invite link to add the Super Pal Bot to your channel.

Get your bot's CLIENT\_ID under OAuth2 > General in the [Discord developer portal](https://discord.com/developers/applications/).

Recommended settings for OAuth Invite Link:
- guilds
- guilds.members.read
- bot
  - Manage Roles
  - Send Messages

<br/>

The settings listed above would result in the following link, where [CLIENT\_ID] is substituted with your bot's CLIENT\_ID:
`https://discord.com/api/oauth2/authorize?client_id=[CLIENT_ID]&permissions=268437504&redirect_uri=https%3A%2F%2Fdiscord.com%2Fapi%2Foauth2%2Fauthorize&response_type=code&scope=guilds%20guilds.members.read%20bot`

<br/>

## Run the script!
Now just run the python script: `python3 discord-super-pal-of-the-week.py`

(I suggest keeping the script running in a tmux session so that you can easily attach if you want to view the bot status.)
