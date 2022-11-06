FROM ubuntu:latest
LABEL MAINTAINER="Adam Hurm" INFO="Discord Super Pal of the Week"

COPY run.sh /home/run.sh
COPY .env /home/.env

RUN apt update && apt install python3 python3-pip git curl tmux \
libpixman-1-dev libcairo2-dev libpango1.0-dev libjpeg8-dev libgif-dev -y \
&& curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.1/install.sh | sh \
&& export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" \
&& nvm install --lts && nvm use --lts \
&& cd /home && git clone -b dalle_ai_images https://github.com/adamhurm/discord-super-pal-of-the-week \
&& cd discord-super-pal-of-the-week && pip install -U discord.py python-dotenv openai \
&& cd discord-spin-the-wheel && git submodule update --init \
&& npm install -g yarn dotenv && yarn install && yarn setup \
&& cp /home/.env /home/discord-super-pal-of-the-week \
&& chmod +x /home/run.sh && mkdir assets

COPY assets /home/discord-super-pal-of-the-week/assets
CMD ["/bin/bash", "/home/run.sh"]
