#!/usr/bin/env bash
# Redeploy an already-installed instance: pulls latest code, rebuilds the
# frontend, redeploys to /opt/discord-bot, reinstalls Python deps, restarts
# the services, then actually checks the bot and dashboard came back up.
#
# For a fresh box, or after a distro-level dependency changes, use
# ./install.sh instead -- it's also safe to re-run any time, this script
# just skips its system-package/rust/librespot steps, which don't change on
# a normal code update.
#
# Usage: from the repo you originally ran install.sh from: ./update.sh
set -euo pipefail

REPO_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/discord-bot"
APP_USER="discordbot"

if [ ! -d "$APP_DIR" ]; then
  echo "$APP_DIR doesn't exist -- run ./install.sh first." >&2
  exit 1
fi

echo "== pull latest =="
git -C "$REPO_SRC" pull

echo "== rebuild dashboard frontend =="
cd "$REPO_SRC/frontend"
npm ci
npm run build
rm -rf node_modules
cd "$REPO_SRC"

echo "== redeploy to $APP_DIR =="
sudo rsync -a --exclude='.env' --exclude='librespot-cache' "$REPO_SRC"/ "$APP_DIR"/
sudo "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
# Only reinstall AWS support if it was opted into originally (install.sh's
# prompt, or a manual `pip install -r requirements-aws.txt`) -- never adds
# it on its own.
if sudo "$APP_DIR/venv/bin/pip" show boto3 >/dev/null 2>&1; then
  sudo "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements-aws.txt"
fi

# Voice encoding needs the system libopus (discord.py only bundles it on
# Windows). Older installs predate install.sh installing it -- without it
# the bot joins voice but plays nothing (OpusNotLoaded in the logs).
if ! ldconfig -p | grep -q libopus; then
  echo "== libopus missing -- installing =="
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y libopus0
  else
    sudo dnf install -y opus
  fi
fi
sudo chmod +x "$APP_DIR/bin/spottydaemon"
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "== restart services =="
sudo systemctl daemon-reload
sudo systemctl restart discord-music-bot discord-dashboard

echo "== verify =="
sleep 3
ok=1
if curl -sf http://127.0.0.1:8787/healthz >/dev/null; then
  echo "-> bot healthz: OK"
else
  echo "-> bot healthz: FAILED -- sudo journalctl -u discord-music-bot -n 50 --no-pager" >&2
  ok=0
fi
if curl -sf -o /dev/null http://127.0.0.1:8080/; then
  echo "-> dashboard: OK"
else
  echo "-> dashboard: FAILED -- sudo journalctl -u discord-dashboard -n 50 --no-pager" >&2
  ok=0
fi
echo
sudo systemctl status --no-pager -l discord-music-bot discord-dashboard
[ "$ok" -eq 1 ]
