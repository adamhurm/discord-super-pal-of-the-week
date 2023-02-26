FROM python:3.10-slim-buster
LABEL MAINTAINER="Adam Hurm" INFO="Discord Super Pal of the Week"

WORKDIR /super-pal

COPY super-pal.py super-pal.py
COPY .env .env
COPY assets assets

RUN pip install discord.py python-dotenv openai

CMD ["python", "/super-pal/super-pal.py"]
