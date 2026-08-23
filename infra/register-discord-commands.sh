#!/usr/bin/env bash
# Register the global slash commands handled by lambda/wake_sleep.py.
#
# These must exist as-is separately from anything bot/main.py registers via
# discord.py's CommandTree.sync() -- that sync only runs while the bot
# process (and therefore the EC2 instance) is up, but /wake's whole job is
# to work while the instance is stopped. So these are registered once,
# directly against the Discord API, and never touch the bot process.
#
# Idempotent: Discord's PUT global-commands endpoint replaces the full set
# each time, so re-running with the same body is a no-op.
#
# Required env vars:
#   DISCORD_APPLICATION_ID   Discord developer portal -> General Information -> Application ID
#   DISCORD_BOT_TOKEN         Discord developer portal -> Bot -> Token
set -euo pipefail

DISCORD_APPLICATION_ID="${DISCORD_APPLICATION_ID:?set DISCORD_APPLICATION_ID}"
DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN:?set DISCORD_BOT_TOKEN}"

RESPONSE="$(curl -sS -w '\n%{http_code}' \
  -X PUT "https://discord.com/api/v10/applications/${DISCORD_APPLICATION_ID}/commands" \
  -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '[
    {"name": "wake", "description": "Start the music bot instance (takes ~30s)", "type": 1},
    {"name": "sleep", "description": "Stop the music bot instance", "type": 1}
  ]')"

HTTP_CODE="$(tail -n1 <<<"$RESPONSE")"
BODY="$(sed '$d' <<<"$RESPONSE")"

if [ "$HTTP_CODE" != "200" ]; then
  echo "Discord API error ($HTTP_CODE): $BODY" >&2
  exit 1
fi

echo "== registered global commands =="
echo "$BODY" | python3 -c "import json,sys; [print('/'+c['name'], '-', c['description']) for c in json.load(sys.stdin)]"
echo "-> global commands can take up to 1h to propagate to all servers (per-guild commands would be instant, but /wake needs to work everywhere the bot is installed)"
