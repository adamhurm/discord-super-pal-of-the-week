# Super Pal of the Week
Discord bot that promotes users to Super Pal of the Week role. Go to the [wiki home page](https://github.com/adamhurm/discord-super-pal-of-the-week/wiki) to get started!

> Hey! Over here! Just a heads up: I (Adam) created this bot a while ago and stopped maintaining it, so I'm trying out Claude after this [last release (1.3.3)](https://github.com/adamhurm/discord-super-pal-of-the-week/tree/1.3.3). Please let this serve as a warning that the repo contains AI-generated content. 

<br />

Super Pal Bot currently supports these commands and the looped task:
- **Commands:**
  - `/superpal @user` : add another person to super pal of the week.
  - `/surprise your text here` : receive a surprise image in the channel based on the text you provide.
  - `!spinthewheel` : pick a new super pal of the week at random.
  - `!commands` : list all supported commands.
  - `!cacaw` : spam chat with partyparrot emojis.
  - `!meow` : spam chat with partycat emojis.
  - `!unsurprise` : receive a surprise image in the channel.
  - `!karatechop` : move a random user to AFK voice channel.
- **Looped Task:**
  - Pick new super pal every Sunday at noon (dependent on bot's timezone).

ℹ️ See the [supported features page](https://github.com/adamhurm/discord-super-pal-of-the-week/wiki/Features) for a full list of commands.

## Building Native Binaries

Native binaries for macOS and Windows are automatically built on each release using GitHub Actions. You can also build them manually:

### Automated Builds (GitHub Actions)

Binaries are automatically built when:
- A new release is published (attached to the release)
- Manually triggered via the "Build Native Binaries" workflow

### Manual Build Instructions

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   ```

2. Build using the spec file:
   ```bash
   pyinstaller discord-super-pal.spec
   ```

   Or build directly:
   ```bash
   cd src
   pyinstaller --onefile --name discord-super-pal bot.py
   ```

3. The binary will be created in `dist/` directory.

### Platform-Specific Notes

- **macOS**: The binary supports both Intel (x86_64) and Apple Silicon (arm64) architectures
- **Windows**: The binary is built for x64 architecture
- **Linux**: Use Docker for deployment (see Dockerfile.super-pal)
