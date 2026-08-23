# Discord Spotify Connect bot

Bot streams audio from up to `MAX_SLOTS` (default 5) Spotify Premium
accounts into Discord voice channels. Each account runs as a real Spotify
Connect device (via `librespot`), so playback (play/pause/skip/queue) is
controlled straight from the Spotify app — the bot just relays decoded
audio into the voice channel.

Friends can self-serve link their own account to a free, named,
password-protected slot with `/link` + `/link-finish` — no SSH access or
owner involvement needed. `/connect <slot>` then prompts for that slot's
password via a Discord popup (not a plain command argument, so it doesn't
show up in the channel's visible command-usage line). See "Self-serve
Spotify linking" below. Server admins can free a slot back up with
`/delete-slot`.

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
2. Create secret `discord-music-bot/credentials` in Secrets Manager (Secret type: "Other", plaintext JSON) with key `DISCORD_TOKEN`. ~$0.40/month total.
3. From your own machine: `infra/create-ec2-role.sh` creates the instance's IAM role + instance profile (lets the bot stop only this tagged instance and read only this one secret), then attach it per the command it prints.
4. `git clone` this repo to `~/discord-bot` on the instance, then run `infra/setup-instance.sh` (on the instance) — installs system deps, ffmpeg, Rust, builds librespot, deploys the bot to `/opt/discord-bot`, and installs the systemd service. Safe to re-run (e.g. after a `git pull`) — it skips work already done and never touches an existing `.env`.
5. Edit `/opt/discord-bot/.env`: confirm `SECRETS_MANAGER_SECRET_ID=discord-music-bot/credentials` is set, leave `DISCORD_TOKEN` blank.
6. `sudo systemctl start discord-music-bot`. Slots start unclaimed — either you `/link` your own account like anyone else, or run `infra/bootstrap-spotify-oauth.sh` once per slot for accounts you want to bootstrap yourself (see that script's header for the SSH tunnel steps); a manually-bootstrapped slot has no name/password until someone runs `/link` against it or an admin sets one up by hand in `slots.json`.

## Lambda (wake/sleep) setup

Discord delivers interactions to either the bot's gateway connection or a
single HTTP "Interactions Endpoint URL" — never both. Once that endpoint is
set (required so `/wake` works while the instance, and therefore the bot
process, is stopped), Discord stops sending most other commands over the
gateway too. `lambda/wake_sleep.py` handles `/wake`/`/sleep` directly, shows
a password-collecting modal directly for `/connect`/`/link` (Discord
requires responding to a command with a modal immediately, so this can't be
relayed), and relays everything else — `/disconnect`, `/link-finish`,
`/delete-slot`, and every modal *submission* — onto an SQS queue;
`bot/interaction_relay.py` long-polls that queue and replies via Discord's
webhook-followup API. No inbound network exposure needed on the EC2 side
for any of this.

1. From your own machine: `infra/create-interaction-queue.sh` — creates the relay queue, prints its URL.
2. Set `INTERACTIONS_QUEUE_URL=<that URL>` in `/opt/discord-bot/.env` on the instance.
3. From your own machine: `DISCORD_PUBLIC_KEY=<from the Discord developer portal> INTERACTIONS_QUEUE_URL=<that URL> infra/deploy-lambda.sh` — builds `lambda/wake_sleep.py` + deps, creates the Lambda's IAM role, deploys the function, and creates a public Function URL with both resource-policy permissions it needs (`lambda:InvokeFunctionUrl` *and* `lambda:InvokeFunction` — AWS requires both as of Oct 2025; missing either gives a 403 `AccessDeniedException` before your code ever runs). Re-run any time `wake_sleep.py` changes.
4. Paste the Function URL it prints into the Discord app's "Interactions Endpoint URL" field. Discord's auth type NONE is intentional — Discord itself can't sign AWS SigV4, so the Ed25519 signature check inside the handler is what actually authenticates requests.
5. All slash commands (`/wake`, `/sleep`, `/connect`, `/disconnect`, `/link`, `/link-finish`, `/delete-slot`) are registered together by the bot itself on startup (`tree.sync()` in `main.py`) — command *registration* is unaffected by the gateway/webhook split above, only interaction *delivery* is. They have to be registered together: Discord's bulk-overwrite endpoint replaces the *entire* global command set on every call, so registering a subset from anywhere else (a separate script, a second `tree.sync()` with a different command list) silently deletes the rest. Global commands can take up to an hour to show up in a server after the bot's first startup.

## Self-serve Spotify linking

1. A friend runs `/link <slotname>` (lowercase letters/numbers/hyphens, e.g. `alices-jams`) and sets a password in the popup that appears.
2. The bot replies (ephemerally) with a Spotify login link and instructions. They log in with the Spotify account they want to use.
3. After logging in, Spotify redirects their browser to `http://127.0.0.1:<port>/...` — this fails to load (expected, since `127.0.0.1` means *their* machine, not the bot's), but the failed url in the address bar is what librespot needs.
4. They copy that url and run `/link-finish <url>` within 5 minutes to complete linking.
5. Once linked, anyone can `/connect <slotname>` and enter the slot's password in the popup to start streaming it.

This works without exposing any port on the EC2 instance: librespot's own
OAuth client only accepts loopback redirect URIs, so there's no way to make
Spotify redirect a remote browser straight back to a public address anyway.
The bot instead runs librespot's `--enable-oauth` locally with its stdin
piped, and feeds it the pasted-back redirect url itself — see
`bot/spotify_link.py` for details. `/delete-slot <name>` (requires the
Manage Server permission) wipes a slot's credentials and frees it back up.

## Idle shutdown

`bot/idle_monitor.py` tracks how long `voice_clients` has been empty; after
`IDLE_SHUTDOWN_MINUTES` (default 20, set in `.env`) it calls
`ec2_control.stop_this_instance()`, which reads the instance's own ID via
IMDSv2 and calls `ec2:StopInstances` on itself. The bot also proactively
disconnects from a voice channel once it's the last non-bot member left, so
the idle clock starts as soon as everyone leaves rather than waiting for a
stale connection to time out on its own.

## Known gaps / next steps

- No health check / alerting if librespot or the bot crash-loops beyond systemd's restart.
- A slot bootstrapped manually via `infra/bootstrap-spotify-oauth.sh` rather than `/link` has credentials but no name/password/claimed state in `slots.json` until someone runs `/link` against it or an admin edits that file by hand — the bot won't auto-start its librespot process at boot until it's recorded as claimed.
