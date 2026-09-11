#!/usr/bin/env bash
# One-command setup for a brand new EC2 instance: installs everything the
# bot needs (ffmpeg, Rust, librespot), deploys the app to /opt/discord-bot,
# and walks you through entering your Discord app's credentials.
#
# Targets Amazon Linux/Fedora (uses `dnf`). On another distro, install the
# equivalent packages by hand (see the `dnf install` line below for the
# list) and skip straight to the librespot build / systemd steps -- nothing
# else here is AWS- or distro-specific.
#
# Run this ON THE INSTANCE, after: launching it (see README's "EC2 setup"
# section for the launch/IAM-role steps) and `git clone`-ing this repo to
# ~/discord-bot. Safe to re-run -- it skips work already done and won't
# re-prompt for credentials once .env has a token in it.
set -euo pipefail

AWS_REGION="us-east-2"
REPO_SRC="$HOME/discord-bot"
LIBRESPOT_SRC="$HOME/librespot"
APP_DIR="/opt/discord-bot"

echo "=============================================================="
echo " Discord Spotify Connect bot -- guided instance setup"
echo "=============================================================="
echo "This installs system deps, builds librespot, deploys the bot, and"
echo "then asks for your Discord app's credentials to finish configuring it."
echo "Grab these now from https://discord.com/developers/applications, if"
echo "you haven't already: open your app -> Bot page (token) and General"
echo "Information page (application ID + public key)."
echo

echo "== system packages =="
sudo dnf install -y python3.12 python3.12-pip git gcc pkgconfig openssl-devel make

echo "== ffmpeg (static build, not in AL2023 repos) =="
if ! command -v ffmpeg >/dev/null 2>&1; then
  cd /tmp
  curl -L -O https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
  tar xf ffmpeg-release-amd64-static.tar.xz
  sudo cp ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/
  sudo cp ffmpeg-*-amd64-static/ffprobe /usr/local/bin/
fi
ffmpeg -version

echo "== rust toolchain =="
if ! command -v cargo >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source "$HOME/.cargo/env"

echo "== build librespot (in \$HOME, not /tmp -- /tmp is tmpfs: small + wiped on reboot) =="
if [ -d "$LIBRESPOT_SRC" ]; then
  cd "$LIBRESPOT_SRC"
  git fetch --tags
else
  git clone https://github.com/librespot-org/librespot.git "$LIBRESPOT_SRC"
  cd "$LIBRESPOT_SRC"
fi
git checkout v0.8.0
# -j 1: t3.small (2GiB RAM) OOM-kills rustc under librespot-protocol's codegen
# with the default parallel job count. Single-job build is slower but stable.
cargo build --release --no-default-features --features "native-tls" -j 1
sudo cp target/release/librespot /usr/local/bin/
librespot --version

echo "== deploy bot app =="
sudo mkdir -p "$APP_DIR"
sudo cp -r "$REPO_SRC"/. "$APP_DIR"/
sudo python3.12 -m venv "$APP_DIR/venv"
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "== .env =="
if [ -f "$APP_DIR/.env" ] && grep -q '^DISCORD_TOKEN=.\+' "$APP_DIR/.env" 2>/dev/null; then
  echo "-> $APP_DIR/.env already has a Discord token configured, leaving it alone"
  echo "   (delete the DISCORD_TOKEN line and re-run this script to redo credentials)"
else
  if [ ! -f "$APP_DIR/.env" ]; then
    sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    sudo tee -a "$APP_DIR/.env" >/dev/null <<EOF
AWS_DEFAULT_REGION=$AWS_REGION
EOF
  fi

  echo
  echo "== Discord app credentials =="
  echo "https://discord.com/developers/applications -> your app:"
  echo "  - Bot token:       Bot page -> Reset Token (only shown once -- save it somewhere safe too)"
  echo "  - Application ID:  General Information page"
  echo "  - Public Key:      General Information page"
  echo

  DISCORD_TOKEN_INPUT=""
  while [ -z "$DISCORD_TOKEN_INPUT" ]; do
    read -r -s -p "Discord bot token (required, input hidden): " DISCORD_TOKEN_INPUT
    echo
  done

  read -r -p "Discord application (client) ID [optional, Enter to skip]: " DISCORD_APPLICATION_ID_INPUT
  read -r -p "Discord public key [needed later for the Lambda -- Enter to skip for now]: " DISCORD_PUBLIC_KEY_INPUT

  echo
  echo "One more thing the bot needs to actually receive /connect, /link etc:"
  echo "an SQS queue URL from infra/create-interaction-queue.sh, run once per AWS"
  echo "account from YOUR OWN machine (not this instance -- it needs broader AWS"
  echo "permissions than this instance's role has). If you've already run it,"
  echo "paste the queue URL it printed; otherwise leave blank and fill in"
  echo "INTERACTIONS_QUEUE_URL in $APP_DIR/.env once you have."
  echo
  read -r -p "SQS interactions queue URL [optional, Enter to skip]: " INTERACTIONS_QUEUE_URL_INPUT

  sudo sed -i "s|^DISCORD_TOKEN=.*|DISCORD_TOKEN=$DISCORD_TOKEN_INPUT|" "$APP_DIR/.env"
  if [ -n "$INTERACTIONS_QUEUE_URL_INPUT" ]; then
    sudo sed -i "s|^INTERACTIONS_QUEUE_URL=.*|INTERACTIONS_QUEUE_URL=$INTERACTIONS_QUEUE_URL_INPUT|" "$APP_DIR/.env"
  fi
  # Not read by the bot itself (application_id comes from each interaction's
  # own payload at runtime, and DISCORD_PUBLIC_KEY is only used by the
  # separately-deployed Lambda) -- kept here so they're not lost, and so the
  # "next steps" block below can hand back a ready-to-run deploy-lambda.sh
  # command. Strip any stale copies first so re-running this block doesn't
  # duplicate them.
  sudo sed -i '/^DISCORD_APPLICATION_ID=/d; /^DISCORD_PUBLIC_KEY=/d' "$APP_DIR/.env"
  sudo tee -a "$APP_DIR/.env" >/dev/null <<EOF
DISCORD_APPLICATION_ID=$DISCORD_APPLICATION_ID_INPUT
DISCORD_PUBLIC_KEY=$DISCORD_PUBLIC_KEY_INPUT
EOF
  echo "-> saved to $APP_DIR/.env"
fi

echo "== systemd service =="
sudo useradd -r -s /sbin/nologin discordbot || true
sudo chown -R discordbot:discordbot "$APP_DIR"
sudo cp "$APP_DIR/systemd/discord-music-bot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-music-bot

DISCORD_PUBLIC_KEY_SAVED="$(sudo grep -oP '^DISCORD_PUBLIC_KEY=\K.*' "$APP_DIR/.env" 2>/dev/null || true)"
INTERACTIONS_QUEUE_URL_SAVED="$(sudo grep -oP '^INTERACTIONS_QUEUE_URL=\K.*' "$APP_DIR/.env" 2>/dev/null || true)"

echo
echo "=============================================================="
echo " done -- next steps"
echo "=============================================================="
echo "1. sudo systemctl start discord-music-bot"
echo "   sudo journalctl -u discord-music-bot -f     # watch it come up"
echo
echo "2. Link a Spotify account to a slot: once the bot's running and in"
echo "   your server, run /link in Discord (see README's \"Self-serve"
echo "   Spotify linking\" section) -- no further SSH access needed."
echo
if [ -z "$INTERACTIONS_QUEUE_URL_SAVED" ]; then
  echo "3. STILL NEEDED -- from YOUR OWN machine (not this instance):"
  echo "   infra/create-interaction-queue.sh"
  echo "   then set INTERACTIONS_QUEUE_URL in $APP_DIR/.env to the URL it prints"
  echo "   and restart: sudo systemctl restart discord-music-bot"
  echo
fi
echo "4. Deploy the Lambda that handles /wake, /sleep, and routes everything"
echo "   else to this bot -- from YOUR OWN machine:"
echo "   DISCORD_PUBLIC_KEY=${DISCORD_PUBLIC_KEY_SAVED:-<from Discord developer portal>} \\"
echo "     INTERACTIONS_QUEUE_URL=${INTERACTIONS_QUEUE_URL_SAVED:-<from step 3>} \\"
echo "     infra/deploy-lambda.sh"
echo "   Then paste the Function URL it prints into the Discord app's"
echo "   \"Interactions Endpoint URL\" field."
echo "=============================================================="
