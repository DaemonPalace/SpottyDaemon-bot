#!/usr/bin/env bash
# One-time interactive OAuth login for one Spotify account slot.
# librespot 0.8+ dropped password auth entirely; this caches a token to disk
# so the systemd-managed bot never needs interactive login again afterward.
#
# Usage (run ON THE EC2 INSTANCE):
#   infra/bootstrap-spotify-oauth.sh 1 5588
#   infra/bootstrap-spotify-oauth.sh 2 5589
#
# Before running, from YOUR OWN machine, open an SSH tunnel so your local
# browser can reach the OAuth redirect server running on the instance:
#   ssh -i ~/path/to/key.pem -L 5588:localhost:5588 ec2-user@<INSTANCE_IP>
# (match the port to whatever you pass as the 2nd argument here)
set -euo pipefail

SLOT_INDEX="${1:?usage: bootstrap-spotify-oauth.sh <slot_index> <oauth_port>}"
OAUTH_PORT="${2:?usage: bootstrap-spotify-oauth.sh <slot_index> <oauth_port>}"

CACHE_DIR="/opt/discord-bot/librespot-cache/slot${SLOT_INDEX}"
LIBRESPOT_BIN="${LIBRESPOT_BIN:-/usr/local/bin/librespot}"

sudo mkdir -p "$CACHE_DIR"
sudo chown discordbot:discordbot "$CACHE_DIR"

echo "== starting interactive OAuth for slot ${SLOT_INDEX} =="
echo "== open the printed URL in YOUR browser (via the SSH tunnel on port ${OAUTH_PORT}) =="
echo "== log in with the Spotify account for this slot, then Ctrl+C once you see credentials saved =="

sudo -u discordbot "$LIBRESPOT_BIN" \
  --name "discord-bot-${SLOT_INDEX}" \
  --enable-oauth \
  --oauth-port "$OAUTH_PORT" \
  --system-cache "$CACHE_DIR" \
  --backend pipe \
  --device "/tmp/librespot-oauth-bootstrap-slot${SLOT_INDEX}.pcm"

echo "== done -- verify: ls $CACHE_DIR/credentials.json =="
