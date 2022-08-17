FROM ubuntu:latest
LABEL MAINTAINER="Adam Hurm" INFO="Discord Super Pal of the Week"

COPY .env /home/.env
ENV WHEEL_TOKEN=$(grep WHEEL_TOKEN .env)

RUN apt update && apt install python3 python3-pip git curl 
&& nvm install --lts && nvm use --lts \
&& cd /home && git clone https://github.com/adamhurm/wheel-of-names-discord-bot \
&& cd wheel-of-names-discord-bot && git submodule update --init \
&& cd wheel-of-names-discord-bot && yarn install \
&& cp /home/.env /home/wheel-of-names-discord-bot \
&& yarn start & \
&& cd ../ && pip install -U discord.py python-dotenv \
&& python3 discord-super-pal-of-the-week

CMD ["/bin/bash"]
