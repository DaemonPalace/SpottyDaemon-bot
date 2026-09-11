# Discord Spotify Connect bot

A self-hosted Discord bot that streams multiple Spotify Premium accounts into
voice channels as real Spotify Connect devices, running on an EC2 instance
that sleeps itself when idle and wakes on a slash command. Friends link their
own Spotify account to a password-protected slot straight from Discord — no
SSH access to the server required.

> **This project was "vibe coded" — built end-to-end through conversational
> pairing with [Claude Code](https://claude.com/claude-code) (Anthropic's
> agentic coding CLI), not written by hand line-by-line.** It was a deliberate
> learning exercise: how far a conversational agent can take a real,
> multi-service AWS deployment (EC2, Lambda, SQS, IAM, Secrets Manager) from
> a feature request to a working, debugged production system — including
> diagnosing live bugs from production logs, not just writing code that
> compiles. See ["About this project"](#about-this-project) below for what
> that process actually looked like.

## Capabilities

- **Up to 5 simultaneous Spotify Connect streams**, each a real Spotify
  Connect device (via [`librespot`](https://github.com/librespot-org/librespot))
  controlled straight from the Spotify app — the bot only relays decoded
  audio into a voice channel, it doesn't reimplement playback controls.
- **Self-serve account linking** (`/link` + `/link-finish`): a friend claims
  a free slot, names it, sets a password, and links their own Spotify
  account entirely through Discord — working around the fact that
  librespot's OAuth flow only accepts loopback redirects by having the user
  paste back the failed redirect URL, which the bot replays to itself
  locally.
- **Password-gated `/connect`**, collected via a Discord modal popup rather
  than a plain command argument, so it never shows up in the channel's
  visible command-usage line.
- **Admin slot management** (`/delete-slot`, permission-gated on Manage
  Server, enforced both by Discord and again server-side since production
  traffic bypasses Discord's own gateway-level check).
- **Cost-optimized on-demand infrastructure**: the EC2 instance stops itself
  after 20 idle minutes and wakes back up via `/wake`, routed through a
  small always-on Lambda so the bot doesn't need to be running to receive
  that command.
- **Self-healing audio pipeline**: a background reader keeps each slot's
  named pipe drained (so librespot's writer can never block indefinitely on
  pause), paced to real playback speed (so idle draining can't race ahead
  of real time), with automatic reattachment if playback stops on its own
  so a pause doesn't require re-running `/connect`.
- **Fully scripted infrastructure** — IAM roles, the SQS relay queue, the
  Lambda deploy, and a guided EC2 instance bootstrap that walks a fresh
  install through entering Discord app credentials interactively.

## Architecture

```
Discord ──▶ Lambda (Function URL) ──▶ /wake, /sleep: direct EC2 API calls
                │                     /connect, /link: respond with a
                │                       password modal directly
                ▼
         SQS relay queue ──▶ EC2 bot process (long-polls the queue)
                                    │
                                    ├─ librespot × N (one per claimed slot,
                                    │   each a real Spotify Connect device)
                                    │   → named pipe → ffmpeg → Discord voice
                                    │
                                    └─ replies via Discord's webhook-followup
                                       API (no live gateway Interaction object
                                       on this path)
```

Discord delivers interactions to either the bot's gateway connection or a
single HTTP "Interactions Endpoint URL" — never both. Once that endpoint is
set (required so `/wake` works while the instance is stopped), the Lambda
becomes the front door for every interaction: it answers `/wake`/`/sleep`
and password modals itself, and relays anything that needs the running bot
process onto SQS.

## About this project

This bot exists because I wanted my friends to be able to use their own
Spotify accounts without going through me every time — and I wanted to see
how far I could get building a real, stateful, multi-AWS-service system by
directing Claude Code conversationally rather than writing the
implementation myself.

What that looked like in practice:

- Describing features in plain language ("I want people to be able to link
  their own Spotify account without SSH access") and having the agent
  research the actual constraints (librespot's OAuth client only accepts
  loopback redirects — a genuine dead end that required rethinking the
  whole linking flow around pasting back a failed redirect URL) before
  proposing an architecture.
- Reviewing and steering real design tradeoffs (password UX vs. Discord's
  command-usage visibility, fixed vs. dynamic security group ports, JSON
  file vs. database for slot state) rather than accepting the first
  answer.
- **Debugging real production incidents from live logs**, not synthetic
  tests: a named pipe that could block librespot indefinitely on pause, a
  fix for that which accidentally removed all backpressure and caused a
  rapid-fire track-skip storm (which very likely tripped Spotify's own
  rate limiting), and finally a self-healing reattach mechanism — each one
  diagnosed by tailing `journalctl` on the live instance while reproducing
  the bug, forming a hypothesis, and verifying it against a real named pipe
  before shipping the fix.
- Iterating entirely through conversation: branching, deploying to a real
  EC2 instance over SSH, redeploying a Lambda, and rolling changes forward
  based on what actually happened in production — not just what compiled
  locally.

The point wasn't to prove AI can replace writing code — it was to learn what
a tight feedback loop with an agentic tool actually looks like on
infrastructure that has real state, real users, and real failure modes, and
to get hands-on with the AWS services involved (EC2 instance roles, Lambda
Function URLs, SQS, Secrets Manager) along the way.

## Layout

- `bot/` — the Discord bot (discord.py) + librespot process manager + idle-shutdown monitor
- `systemd/discord-music-bot.service` — runs the bot on boot
- `lambda/wake_sleep.py` — Discord Interactions Endpoint handler (`/wake`, `/sleep`, password modals, SQS relay)
- `infra/` — setup scripts + IAM policy JSON for the Lambda role and the EC2 instance role

## Self-hosting (no AWS)

Just want to run this on your own Linux box, no EC2/Lambda/SQS? This is the
default: `INTERACTIONS_QUEUE_URL` and `HOST_CONTROLLER` are both optional,
defaulting to running the gateway `CommandTree` directly (all slash commands
work with no Interactions Endpoint URL configured) and to a no-op
"stop the host" action (`HOST_CONTROLLER=noop`, since a self-hoster turns
their own machine off).

1. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN` (and, if you
   want, `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` for Web API queue/
   now-playing features later — see "Spotify Web API access" below).
2. Install librespot + ffmpeg, then `pip install -r requirements.txt`.
3. Run `bot/main.py` directly, or install `systemd/discord-music-bot.service`
   for it to run on boot (adjust the hardcoded `/opt/discord-bot` paths/user
   if you're not using that convention).
4. Check `curl http://127.0.0.1:8787/healthz` once it's running — that's the
   diagnostics API (see `bot/api.py`), on by default, loopback-only.

Already running on AWS? See "EC2 setup" and "Lambda (wake/sleep) setup"
below instead — that path still works unchanged.

## EC2 setup

1. Launch `t3.micro` (free-tier eligible; use `t3.small` if 1 GiB RAM feels tight), Amazon Linux 2023 (x86_64), 8 GiB gp3 root volume, tag `Name=discord-music-bot`.
2. Create secret `discord-music-bot/credentials` in Secrets Manager (Secret type: "Other", plaintext JSON) with key `DISCORD_TOKEN`. ~$0.40/month total.
3. From your own machine: `infra/create-ec2-role.sh` creates the instance's IAM role + instance profile (lets the bot stop only this tagged instance and read only this one secret), then attach it per the command it prints.
4. `git clone` this repo to `~/discord-bot` on the instance, then run `infra/setup-instance.sh` **on the instance**. This is a guided, one-command setup: it installs system deps, ffmpeg, and Rust, builds librespot, deploys the bot to `/opt/discord-bot`, and interactively walks you through entering your Discord bot token, application ID, and public key (grab them from https://discord.com/developers/applications first). Safe to re-run (e.g. after a `git pull`) — it skips work already done and won't re-prompt once credentials are saved.
5. The script's final "next steps" summary tells you exactly what's left — starting the service, and (if you haven't already) creating the SQS relay queue and deploying the Lambda, both covered below.

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
2. Set `INTERACTIONS_QUEUE_URL=<that URL>` in `/opt/discord-bot/.env` on the instance (or paste it when `infra/setup-instance.sh` asks for it).
3. From your own machine: `DISCORD_PUBLIC_KEY=<from the Discord developer portal> INTERACTIONS_QUEUE_URL=<that URL> infra/deploy-lambda.sh` — builds `lambda/wake_sleep.py` + deps, creates the Lambda's IAM role, deploys the function, and creates a public Function URL with both resource-policy permissions it needs (`lambda:InvokeFunctionUrl` *and* `lambda:InvokeFunction` — AWS requires both as of Oct 2025; missing either gives a 403 `AccessDeniedException` before your code ever runs). Re-run any time `wake_sleep.py` changes.
4. Paste the Function URL it prints into the Discord app's "Interactions Endpoint URL" field. Discord's auth type NONE is intentional — Discord itself can't sign AWS SigV4, so the Ed25519 signature check inside the handler is what actually authenticates requests.
5. All slash commands (`/wake`, `/sleep`, `/connect`, `/disconnect`, `/link`, `/link-finish`, `/delete-slot`) are registered together by the bot itself on startup (`tree.sync()` in `main.py`) — command *registration* is unaffected by the gateway/webhook split above, only interaction *delivery* is. They have to be registered together: Discord's bulk-overwrite endpoint replaces the *entire* global command set on every call, so registering a subset from anywhere else (a separate script, a second `tree.sync()` with a different command list) silently deletes the rest. Global commands can take up to an hour to show up in a server after the bot's first startup.

## Self-serve Spotify linking

1. A friend runs `/link <slotname>` (lowercase letters/numbers/hyphens, e.g. `alices-jams`) and sets a password in the popup that appears.
2. The bot replies (ephemerally) with a Spotify login link and instructions. They log in with the Spotify account they want to use.
3. After logging in, Spotify redirects their browser to `http://127.0.0.1:<port>/...`. If the bot is running on a different machine than their browser (the normal case for a friend linking their own account), this fails to load — that's expected, `127.0.0.1` means *their* machine, not the bot's — and the failed url in the address bar is what librespot needs. If the bot happens to be running on the *same* machine as the browser (e.g. testing locally on your own box), the page loads and completes on its own, no url to copy.
4. Run `/link-finish` within `LINK_TIMEOUT_SECONDS` (default 15 min) to complete linking — paste the failed url in if you had to copy one, or leave it blank if the page loaded fine.
5. Once linked, anyone can `/connect <slotname>` and enter the slot's password in the popup to start streaming it.

This works without exposing any port on the EC2 instance: librespot's own
OAuth client only accepts loopback redirect URIs, so there's no way to make
Spotify redirect a remote browser straight back to a public address anyway.
`--enable-oauth` runs a real local HTTP server on `127.0.0.1:<port>`, actively
waiting for that exact callback request — since the bot process runs on the
same box, it just re-issues that request to itself using the query string
from the pasted-back url, which librespot's server receives exactly as if
the friend's own browser had reached it. See `bot/spotify_link.py` for
details. `/delete-slot <name>` (requires the Manage Server permission) wipes
a slot's credentials and frees it back up.

## Audio pipeline

Each linked slot's librespot process writes decoded PCM audio to a named
pipe, which `ffmpeg` reads from and streams into Discord voice while
`/connect`ed. `bot/librespot_manager.py` also keeps a permanent background
reader on that pipe for whenever ffmpeg isn't attached (idle, or between
pause/resume) — without it, librespot's writer can block indefinitely once
the ~64KB kernel pipe buffer fills, hanging the whole process until it's
restarted. That idle drain is deliberately paced to real playback speed
(not read-as-fast-as-possible) so it can't let librespot race ahead of real
time with nothing actually listening. `bot/commands.py` tracks which slot
each guild is *supposed* to be connected to and transparently reattaches
playback if ffmpeg ever stops on its own (observed happening after a long
Spotify-side pause), so a pause doesn't require running `/connect` again.

## Idle shutdown

`bot/idle_monitor.py` tracks how long `voice_clients` has been empty (and
whether a `/link` is currently in progress, which should also block
shutdown); after `IDLE_SHUTDOWN_MINUTES` (default 20, set in `.env`) it calls
`HostController.stop_host()` (`bot/host_control.py`) — a no-op by default
(`HOST_CONTROLLER=noop`), or `ec2_control.stop_this_instance()` (reads the
instance's own ID via IMDSv2, calls `ec2:StopInstances` on itself) when
`HOST_CONTROLLER=ec2`. The bot also proactively disconnects from a voice
channel once it's the last non-bot member left, so the idle clock starts as
soon as everyone leaves rather than waiting for a stale connection to time
out on its own.

## Spotify Web API access

`/link`/`/link-finish` only get librespot a Spotify Connect session — no
Web API scopes. `/link-web-api <slotname>` (requires the slot already be
linked) runs a separate Authorization Code + PKCE flow against the bot's own
Spotify app, same paste-the-failed-redirect UX as `/link-finish`. Requires
`SPOTIFY_CLIENT_ID` (and, if your app is a confidential client,
`SPOTIFY_CLIENT_SECRET`) set in `.env` — register an app at
developer.spotify.com and add `SPOTIFY_WEB_API_REDIRECT_URI` (default
`http://127.0.0.1:5589/callback`) as a redirect URI there. See
`bot/spotify_web_api.py`.

## Diagnostics API

`bot/api.py` serves a small read-only REST API, on by default at
`127.0.0.1:8787` (`API_HOST`/`API_PORT` in `.env`): `/healthz`,
`/api/latency`, `/api/slots`, `/api/slots/<name>/audio`, `/api/sessions`.
Set `API_TOKEN` to require `Authorization: Bearer <token>` on everything
except `/healthz` — recommended before exposing this beyond localhost.

## Known gaps / next steps

- No health check / alerting if librespot or the bot crash-loops beyond systemd's restart.
- A slot bootstrapped manually via `infra/bootstrap-spotify-oauth.sh` rather than `/link` has credentials but no name/password/claimed state in `slots.json` until someone runs `/link` against it or an admin edits that file by hand — the bot won't auto-start its librespot process at boot until it's recorded as claimed.
- If ffmpeg exits due to a genuine error (not just an idle pause) the self-healing reattach will retry against the same pipe every couple seconds; there's no backoff cap or alerting if it's stuck retrying a broken slot.
