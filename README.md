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

1. Launch `t3.micro` (free-tier eligible; use `t3.small` if 1 GiB RAM feels tight), Amazon Linux 2023 (x86_64), 8 GiB gp3 root volume, tag `Name=discord-music-bot`.
2. Create secret `discord-music-bot/credentials` in Secrets Manager (Secret type: "Other", plaintext JSON) with keys `DISCORD_TOKEN`, `SPOTIFY_1_USERNAME`, `SPOTIFY_1_PASSWORD`, `SPOTIFY_2_USERNAME`, `SPOTIFY_2_PASSWORD`. ~$0.40/month total.
3. From your own machine: `infra/create-ec2-role.sh` creates the instance's IAM role + instance profile (lets the bot stop only this tagged instance and read only this one secret), then attach it per the command it prints.
4. `git clone` this repo to `~/discord-bot` on the instance, then run `infra/setup-instance.sh` (on the instance) — installs system deps, ffmpeg, Rust, builds librespot, deploys the bot to `/opt/discord-bot`, and installs the systemd service. Safe to re-run (e.g. after a `git pull`) — it skips work already done and never touches an existing `.env`.
5. Edit `/opt/discord-bot/.env`: confirm `SECRETS_MANAGER_SECRET_ID=discord-music-bot/credentials` is set, leave `DISCORD_TOKEN`/`SPOTIFY_*` blank.
6. `sudo systemctl start discord-music-bot`, then run `infra/bootstrap-spotify-oauth.sh` once per Spotify account slot (see that script's header for the SSH tunnel steps).

## Lambda (wake/sleep) setup

1. From your own machine: `DISCORD_PUBLIC_KEY=<from the Discord developer portal> infra/deploy-lambda.sh` — builds `lambda/wake_sleep.py` + deps, creates the Lambda's IAM role, deploys the function, and creates a public Function URL with both resource-policy permissions it needs (`lambda:InvokeFunctionUrl` *and* `lambda:InvokeFunction` — AWS requires both as of Oct 2025; missing either gives a 403 `AccessDeniedException` before your code ever runs). Re-run any time `wake_sleep.py` changes.
2. Paste the Function URL it prints into the Discord app's "Interactions Endpoint URL" field. Discord's auth type NONE is intentional — Discord itself can't sign AWS SigV4, so the Ed25519 signature check inside the handler is what actually authenticates requests.
3. `DISCORD_APPLICATION_ID=<app id> DISCORD_BOT_TOKEN=<bot token> infra/register-discord-commands.sh` — registers the global `/wake` and `/sleep` slash commands. Global commands can take up to an hour to show up in a server; re-run any time you change a command's name/description.

## Idle shutdown

`bot/idle_monitor.py` tracks how long `voice_clients` has been empty; after
`IDLE_SHUTDOWN_MINUTES` (default 20, set in `.env`) it calls
`ec2_control.stop_this_instance()`, which reads the instance's own ID via
IMDSv2 and calls `ec2:StopInstances` on itself. The bot also proactively
disconnects from a voice channel once it's the last non-bot member left, so
the idle clock starts as soon as everyone leaves rather than waiting for a
stale connection to time out on its own.

## Known gaps / next steps

- Spotify credentials are plain username/password env vars; consider `librespot`'s cached-credentials mode if rotating passwords is undesirable.
- No health check / alerting if librespot or the bot crash-loops beyond systemd's restart.
