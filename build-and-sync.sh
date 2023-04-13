#!/bin/bash

echo 'ℹ️  Starting ssh-agent and requesting password for key'
eval $(ssh-agent -s)
ssh-add /home/pi/.ssh/id_ed25519

docker build -t adamhurm/discord-super-pal . -f ./Dockerfile.super-pal
echo 'ℹ️  Built docker image'
docker save -o discord-super-pal.tar adamhurm/discord-super-pal:latest
echo 'ℹ️  Saved docker image to discord-super-pal.tar'

for HOST in {201..202}; do
    scp discord-super-pal.tar pi@10.0.0.$HOST:/home/pi/discord-super-pal.tar
    bash -c "echo 'ℹ️  Copied discord-super-pal.tar to 10.0.0.$HOST'"
    ssh pi@10.0.0.$HOST -t 'docker load -i /home/pi/discord-super-pal.tar'
    bash -c "echo 'ℹ️  Loaded image to docker repository on 10.0.0.$HOST'"
done
