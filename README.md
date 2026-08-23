# Discord Spotify Connect bot

Bot streams audio from up to 2 Spotify Premium accounts into Discord voice
channels. Each account runs as a real Spotify Connect device (via
`librespot`), so playback (play/pause/skip/queue) is controlled straight
from the Spotify app — the bot just relays decoded audio into the voice
channel.

Runs on a single on-demand EC2 instance that stops itself after 20 minutes
with no active voice connection, and is woken back up with a `/wake` Discord
slash command routed through a small Lambda.

## Layout

- `bot/` — the Discord bot (discord.py) + librespot process manager + idle-shutdown monitor
- `systemd/discord-music-bot.service` — runs the bot on boot
- `lambda/wake_sleep.py` — Discord Interactions Endpoint handler (`/wake`, `/sleep`)
- `infra/` — IAM policy JSON for the Lambda role and the EC2 instance role

## EC2 setup

1. Launch `t4g.micro`, Amazon Linux 2023 (arm64), 8 GiB gp3 root volume, tag `Name=discord-music-bot`.
2. Attach an instance profile using `infra/iam-ec2-instance-policy.json` (fill in `REGION`/`ACCOUNT_ID`) — lets the bot stop only this tagged instance.
3. Install deps:
   ```
   sudo dnf install -y python3.12 python3.12-pip ffmpeg
   # librespot: build from https://github.com/librespot-org/librespot or grab a prebuilt arm64 binary
   ```
4. `git clone` this repo to `/opt/discord-bot`, create a venv, `pip install -r requirements.txt`.
5. Copy `.env.example` to `/opt/discord-bot/.env`, fill in `DISCORD_TOKEN` and both Spotify slot credentials.
6. `sudo cp systemd/discord-music-bot.service /etc/systemd/system/`, then `sudo useradd -r discordbot`, `sudo systemctl enable --now discord-music-bot`.

## Lambda (wake/sleep) setup

1. Create the Discord app's slash commands `/wake` and `/sleep` (no options) via the Discord API.
2. Deploy `lambda/wake_sleep.py` (bundle with `lambda/requirements.txt`; `boto3` is already in the Lambda runtime).
3. Set env vars `DISCORD_PUBLIC_KEY` (from the Discord developer portal) and `INSTANCE_TAG_NAME=discord-music-bot`.
4. Attach a role using `infra/iam-lambda-policy.json` (fill in `REGION`/`ACCOUNT_ID`).
5. Enable a Lambda Function URL (auth type `NONE` — Discord itself can't sign AWS SigV4; the Ed25519 signature check in the handler is what actually authenticates requests).
6. Paste the Function URL into the Discord app's "Interactions Endpoint URL" field.

## Idle shutdown

`bot/idle_monitor.py` tracks how long `voice_clients` has been empty; after
`IDLE_SHUTDOWN_MINUTES` (default 20, set in `.env`) it calls
`ec2_control.stop_this_instance()`, which reads the instance's own ID via
IMDSv2 and calls `ec2:StopInstances` on itself. The bot also proactively
disconnects from a voice channel once it's the last non-bot member left, so
the idle clock starts as soon as everyone leaves rather than waiting for a
stale connection to time out on its own.

## Known gaps / next steps

- Discord slash commands `/wake` and `/sleep` must be registered once via the Discord API (not automated here).
- Spotify credentials are plain username/password env vars; consider `librespot`'s cached-credentials mode if rotating passwords is undesirable.
- No health check / alerting if librespot or the bot crash-loops beyond systemd's restart.
