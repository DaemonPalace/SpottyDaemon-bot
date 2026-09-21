#!/usr/bin/env bash
# Run ON THE EC2 INSTANCE to pull the latest code and restart the bot +
# dashboard.
#   cd ~/discord-bot && infra/deploy-bot.sh
set -euo pipefail

cd ~/discord-bot
git pull
cd frontend && npm ci && npm run build && cd ..
sudo cp -r ~/discord-bot/. /opt/discord-bot/
sudo /opt/discord-bot/venv/bin/pip install -q -r /opt/discord-bot/requirements.txt
sudo chown -R discordbot:discordbot /opt/discord-bot
sudo systemctl restart discord-music-bot discord-dashboard
sleep 2
sudo journalctl -u discord-music-bot -n 30 --no-pager
sudo journalctl -u discord-dashboard -n 15 --no-pager
