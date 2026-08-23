#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="us-east-2"
REPO_SRC="$HOME/discord-bot"
LIBRESPOT_SRC="$HOME/librespot"
APP_DIR="/opt/discord-bot"

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
if [ -f "$APP_DIR/.env" ]; then
  echo "-> $APP_DIR/.env already exists, leaving it alone"
else
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  sudo tee -a "$APP_DIR/.env" >/dev/null <<EOF
AWS_DEFAULT_REGION=$AWS_REGION
EOF
  echo "-> edit $APP_DIR/.env now: confirm SECRETS_MANAGER_SECRET_ID is set, leave DISCORD_TOKEN/SPOTIFY_* blank"
fi

echo "== systemd service =="
sudo useradd -r -s /sbin/nologin discordbot || true
sudo chown -R discordbot:discordbot "$APP_DIR"
sudo cp "$APP_DIR/systemd/discord-music-bot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-music-bot

echo "== done =="
echo "Review/edit $APP_DIR/.env, then: sudo systemctl start discord-music-bot"
echo "Check logs with: sudo journalctl -u discord-music-bot -f"
