#!/usr/bin/env bash
# One-command install for a fresh Linux box: system deps, librespot, the
# bot + cockpit UI, systemd services, and the `spottydaemon` CLI. Does NOT
# touch Discord/Spotify credentials -- set those after install, via the
# cockpit UI's setup wizard or `spottydaemon setup` (see the printed next
# steps at the end).
#
# Supports any apt- or dnf-based distro. Safe to re-run -- it skips work
# already done and never touches .env once it exists.
#
# Usage: git clone this repo, then from its root: ./install.sh
#
# AWS/EC2 users: this script has no AWS dependency at all (that's the
# point). If you specifically want the legacy EC2 auto-sleep/wake dev
# deployment, use infra/setup-instance.sh instead -- see README.
set -euo pipefail

REPO_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/discord-bot"
APP_USER="discordbot"
LIBRESPOT_SRC="${LIBRESPOT_SRC:-$HOME/librespot}"

echo "=============================================================="
echo " Discord Spotify Connect bot -- install"
echo "=============================================================="

if command -v apt-get >/dev/null 2>&1; then
  PKG_FAMILY=apt
elif command -v dnf >/dev/null 2>&1; then
  PKG_FAMILY=dnf
else
  echo "no apt-get or dnf found -- unsupported distro." >&2
  echo "Install by hand: python3 (>=3.10) + venv/pip, git, gcc, pkg-config," >&2
  echo "openssl headers, make, rsync, curl -- then re-run with SKIP_SYSTEM_PACKAGES=1." >&2
  exit 1
fi

echo "== system packages ($PKG_FAMILY) =="
if [ -z "${SKIP_SYSTEM_PACKAGES:-}" ]; then
  case "$PKG_FAMILY" in
    apt)
      sudo apt-get update -y
      sudo apt-get install -y python3 python3-venv python3-pip git gcc pkg-config libssl-dev make rsync curl
      sudo apt-get clean
      ;;
    dnf)
      sudo dnf install -y python3 python3-pip git gcc pkgconfig openssl-devel make rsync curl
      sudo dnf clean packages
      ;;
  esac
fi

PYTHON_BIN="$(command -v python3)"
PY_MINOR="$("$PYTHON_BIN" -c 'import sys; print(sys.version_info[1])')"
if [ "$PY_MINOR" -lt 10 ]; then
  echo "python3 is too old ($("$PYTHON_BIN" --version)) -- need >=3.10" >&2
  exit 1
fi

echo "== node.js (need >=20; distro packages are often older) =="
if ! command -v node >/dev/null 2>&1 || [ "$(node -e 'console.log(process.versions.node.split(".")[0])')" -lt 20 ]; then
  case "$PKG_FAMILY" in
    apt) curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt-get install -y nodejs && sudo apt-get clean ;;
    dnf) curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - && sudo dnf install -y nodejs && sudo dnf clean packages ;;
  esac
fi
node -v

echo "== ffmpeg (static build -- same binary on every distro) =="
if ! command -v ffmpeg >/dev/null 2>&1; then
  cd /tmp
  curl -L -O https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
  tar xf ffmpeg-release-amd64-static.tar.xz
  sudo cp ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/
  sudo cp ffmpeg-*-amd64-static/ffprobe /usr/local/bin/
fi
ffmpeg -version

echo "== swap (low-RAM boxes OOM-kill rustup/cargo without it) =="
# Best-effort: sized to whatever's actually free (capped at 2GiB), and never
# fatal to the rest of the install -- a box with no room for swap either
# still might have enough RAM, or the user's already been warned below and
# can go fix disk space and re-run.
MEM_KB="$(awk '/MemTotal/{print $2}' /proc/meminfo)"
SWAP_KB="$(awk '/SwapTotal/{print $2}' /proc/meminfo)"
if [ "$MEM_KB" -lt 2097152 ] && [ "$SWAP_KB" -lt 1048576 ] && [ ! -f /swapfile ]; then
  AVAIL_KB="$(df -k --output=avail / | tail -1)"
  SWAP_MB=$(( AVAIL_KB / 1024 - 512 ))
  [ "$SWAP_MB" -gt 2048 ] && SWAP_MB=2048
  if [ "$SWAP_MB" -lt 256 ]; then
    echo "-> only ${AVAIL_KB}KiB free on / -- skipping swap, not enough room."
    echo "   rustup/cargo below may OOM-kill without it. Free up disk and re-run,"
    echo "   or add swap by hand once there's space."
  else
    echo "-> ${MEM_KB}KiB RAM, ${SWAP_KB}KiB swap, ${AVAIL_KB}KiB free on / -- adding a ${SWAP_MB}MiB swapfile"
    if sudo fallocate -l "${SWAP_MB}M" /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_MB"; then
      sudo chmod 600 /swapfile
      sudo mkswap /swapfile
      sudo swapon /swapfile
      grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    else
      echo "-> swapfile creation failed, continuing without it" >&2
      sudo rm -f /swapfile
    fi
  fi
fi

echo "== rust toolchain =="
if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source "$HOME/.cargo/env"
# rustup being present doesn't mean a working toolchain is: an earlier run
# that got OOM-killed mid-unpack (see the swap step above) can leave the
# stable toolchain partial -- missing cargo or rustc, or no default
# configured at all. `--force` is NOT enough to repair this: it compares
# manifest versions and reports "unchanged" without checking whether the
# component files actually exist, so a partial toolchain stays partial.
# Actually verify it works, and nuke + reinstall from scratch if not.
if ! rustc --version >/dev/null 2>&1 || ! cargo --version >/dev/null 2>&1; then
  rustup toolchain uninstall stable
  rustup toolchain install stable
fi
rustup default stable

echo "== build librespot (in \$HOME, not /tmp -- /tmp may be tmpfs) =="
if [ -d "$LIBRESPOT_SRC" ]; then
  cd "$LIBRESPOT_SRC"
  git fetch --tags
else
  git clone https://github.com/librespot-org/librespot.git "$LIBRESPOT_SRC"
  cd "$LIBRESPOT_SRC"
fi
git checkout v0.8.0
# ponytail: -j left to cargo's default (parallel). Low-RAM boxes (~1-2GiB)
# can OOM under librespot-protocol's codegen at full parallelism -- set
# LIBRESPOT_BUILD_JOBS=1 to match what the old EC2 t3.small script hardcoded.
cargo build --release --no-default-features --features "native-tls" ${LIBRESPOT_BUILD_JOBS:+-j "$LIBRESPOT_BUILD_JOBS"}
sudo cp target/release/librespot /usr/local/bin/
librespot --version

echo "== build dashboard frontend =="
cd "$REPO_SRC/frontend"
npm ci
npm run build
# Only dist/ is served at runtime (supervisor/main.py) -- node_modules is
# pure build-time weight (150-300MB) with no reason to reach APP_DIR.
rm -rf node_modules
cd "$REPO_SRC"

echo "== deploy app to $APP_DIR =="
sudo mkdir -p "$APP_DIR"
# .env and librespot-cache/ are live deploy state, never sourced from the
# git checkout -- excluded so a re-run can't clobber them.
sudo rsync -a --exclude='.env' --exclude='librespot-cache' "$REPO_SRC"/ "$APP_DIR"/
sudo "$PYTHON_BIN" -m venv "$APP_DIR/venv"
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi

echo "== spottydaemon CLI =="
sudo chmod +x "$APP_DIR/bin/spottydaemon"
sudo ln -sf "$APP_DIR/bin/spottydaemon" /usr/local/bin/spottydaemon

echo "== systemd services (bot + dashboard) =="
sudo useradd -r -s /sbin/nologin "$APP_USER" 2>/dev/null || true
sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo cp "$APP_DIR/systemd/discord-music-bot.service" "$APP_DIR/systemd/discord-dashboard.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-music-bot discord-dashboard

echo "== Caddy (optional reverse proxy + auto-TLS for the dashboard) =="
if ! command -v caddy >/dev/null 2>&1; then
  curl -L -o /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/latest/download/caddy_$(curl -s https://api.github.com/repos/caddyserver/caddy/releases/latest | grep -oP '"tag_name": "v\K[^"]+')_linux_amd64.tar.gz"
  tar xzf /tmp/caddy.tar.gz -C /tmp caddy
  sudo mv /tmp/caddy /usr/local/bin/caddy
fi
caddy version

read -r -p "Domain for the dashboard [Enter to skip, e.g. music.example.com]: " DASHBOARD_DOMAIN_INPUT
if [ -n "$DASHBOARD_DOMAIN_INPUT" ]; then
  sudo mkdir -p /etc/caddy
  sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DASHBOARD_DOMAIN_INPUT {
    reverse_proxy 127.0.0.1:8080
}
EOF
  sudo cp "$APP_DIR/systemd/caddy.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now caddy
  echo "-> Caddyfile written for $DASHBOARD_DOMAIN_INPUT. Point its DNS A record at"
  echo "   this box and open inbound 80/443 before it can get a cert."
else
  echo "-> skipped -- point a domain at this box and re-run this script, or write"
  echo "   /etc/caddy/Caddyfile by hand and enable systemd/caddy.service."
fi

echo
echo "=============================================================="
echo " done -- next steps"
echo "=============================================================="
echo "Set your Discord bot token + an admin password, either:"
echo
echo "  - via the cockpit UI's setup wizard:"
echo "      sudo systemctl start discord-dashboard"
echo "      open http://<this-machine>:8080"
echo
echo "  - or headless, via the CLI:"
echo "      spottydaemon setup --bot-token <token> --admin-password <password>"
echo "      sudo systemctl start discord-music-bot discord-dashboard"
echo
echo "spottydaemon --help lists every other command."
echo "=============================================================="
