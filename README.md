# super-pal-of-the-week
Discord mod that promotes users to Super Pal of the Week role.

This can just run in a tmux session on a raspberry pi. If you want a more portable build, however, you can use the docker image.

Currently this supports commands and the looped task.
- **Commands:**
  - `!spotw @user` : add another person to super pal of the week.
  - `!spinthewheel` : pick a new super pal of the week at random.
  - `!commands` : list all supported commands.
  - `!cacaw` : spam chat with partyparrot emojis.
  - `!meow` : spam chat with partycat emojis.
- **Looped Task:**
  - Pick new super pal every Sunday at noon (dependent on bot's timezone).

--------
## Local Installation

### Step 1: Clone this repository and install dependencies
First clone this repository: `git clone git@github.com:adamhurm/discord-super-pal-of-the-week.git`

Then after ensuring python3 is on your system, install dependencies: `pip install -U discord.py python-dotenv`

Follow the [spin-the-wheel](https://github.com/adamhurm/wheel-of-names-discord-bot/tree/main#how-to-use) installation instructions: `cd discord-spin-the-wheel && yarn install`

<br/>

### Step 2: Create a local file named `.env` to hold your tokens and IDs:

#### .env
```
SUPERPAL_TOKEN=
WHEEL_TOKEN=
GUILD_ID=
EMOJI_GUILD_ID=
CHANNEL_ID=
ANNOUNCEMENTS_CHANNEL_ID=
```

#### SUPERPAL\_TOKEN and WHEEL\_TOKEN

- You will need to create two bots in the [Discord developer portal](https://discord.com/developers/applications/): Super Pal Bot & Spin the Wheel Bot.

- Choose your application -> Go to Bot section -> Look under "Token" section \
(This token can only be copied once so you may have to reset your token if you do not know it)


#### GUILD\_ID, EMOJI\_GUILD\_ID, CHANNEL\_ID, and ANNOUNCEMENTS\_CHANNEL\_ID
[Web Application](https://discord.com/app) (in-browser)

- Click on the text channel in your server.

- Look at your URL. It will be in the form of `https://discord.com/channels/[GUILD_ID]/[CHANNEL_ID]`

[Desktop Application](https://discord.com/download)

- Turn on `Developer Mode` in Settings -> Advanced

- GUILD\_ID: Right-click on your server icon and select `Copy ID`

- EMOJI\_GUILD\_ID: Right-click on the server icon where party emojis are hosted and select `Copy ID`

- CHANNEL\_ID: Right-click on the text channel where you want to send Super Pal of the Week commands and select `Copy ID`

- ANNOUNCEMENTS\_CHANNEL\_ID: Right-click on the text channel where you want announcements from super-pal-of-the-week-manager and select `Copy ID`

<br/>

### Step 3: Configure Super Pal roles in your channel:

- Create a role named `super pal of the week` and add your desired elevated permissions (if any).

- Create a role that is one tier higher named `spotw-bot` and apply it to the Super Pal Bot.
  - This is required in order for the Super Pal Bot to apply the `super pal of the week` role.

<br/>

### Step 4: Create an invite link to add the Super Pal Bot to your channel.

Get your bot's CLIENT\_ID under OAuth2 > General in the [Discord developer portal](https://discord.com/developers/applications/).

Recommended settings for OAuth Invite Link:
**Super Pal Bot**
- guilds
- guilds.members.read
- bot
  - Manage Roles
  - Send Messages

**Spin the Wheel Bot**
- guilds
- guilds.members.read
- bot
  - Send Messages
  - Manage Messages
  - Attach Files
<br/>

The settings listed above would result in the following link, where [CLIENT\_ID] is substituted with your bot's CLIENT\_ID:
`https://discord.com/api/oauth2/authorize?client_id=[CLIENT_ID]&permissions=268437504&redirect_uri=https%3A%2F%2Fdiscord.com%2Fapi%2Foauth2%2Fauthorize&response_type=code&scope=guilds%20guilds.members.read%20bot`

<br/>

### Run the bots!
Now you'll need to run the bots:
 - Super Pal Bot: `python3 discord-super-pal-of-the-week.py`
 - Spin the Wheel Bot: `cd discord-spin-the-wheel && yarn start`

(I suggest keeping the script running in a tmux session so that you can easily attach if you want to view the bot status.)

<br/>

## Docker installation instructions:

First clone this repository: `git clone git@github.com:adamhurm/discord-super-pal-of-the-week.git`

Next, follow [Step 2 above](https://github.com/adamhurm/discord-super-pal-of-the-week#step-2-create-a-local-file-named-env-to-hold-your-tokens-and-ids) to create a local file named .env in the discord-super-pal-of-the-week directory. Add all your tokens to the file.

Once the .env file is in place, build the image: `docker build -t discord-super-pal-of-the-week .`

Now you can just deploy and run the image anywhere: `docker run -d discord-super-pal-of-the-week`

*WARNING: This iteration of the project does not use any docker secrets or secure storage for discord tokens. Your tokens will all be in plaintext, so -- Please do not publicly upload your container until this notice is removed.*
