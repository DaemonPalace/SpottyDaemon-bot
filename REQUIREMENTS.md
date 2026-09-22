# Requirements & Roadmap

Three delivery tracks, in priority order:

1. **HOSTED** — self-hosted on any Linux server, any cloud (or bare metal). This is the *production* target.
2. **LOCAL APP** — run the bot on a personal machine (Linux first, Windows/Mac stretch).
3. **MOBILE APP** — run the bot locally on a phone (Android first, iPhone stretch/optional).

## Guiding constraint: AWS is dev-only, not the product

AWS (EC2, Lambda, SQS, Secrets Manager) is the author's *private development environment* — it is
how the prototype has been run and tested so far, nothing more. The shipped product must run
standalone on any Linux server, self-hosted or in any cloud, with no AWS dependency required.
Anything AWS-specific (`bot/ec2_control.py`, `lambda/wake_sleep.py`, `infra/create-ec2-role.sh`,
`infra/create-interaction-queue.sh`, `infra/deploy-lambda.sh`, `infra/bootstrap-spotify-oauth.sh`)
stays legacy/opt-in behind the existing `HostController` abstraction (`bot/host_control.py`:
`NoopHostController` is the default; `Ec2HostController` is the legacy path), never a hard
dependency.

## Current state (2026-09-22)

- Working prototype, deployed manually to a private AWS EC2 instance. Deploy is a manual
  `git pull` + `rsync` + `systemctl restart` (`deploy.txt`, `infra/deploy-bot.sh`).
- `bot/` — Discord bot + Spotify Connect integration (host-agnostic except `ec2_control.py`).
- `supervisor/` — process manager + auth + API that runs/monitors the bot, backs the dashboard.
- `frontend/` — React setup wizard / dashboard UI, served via Caddy reverse proxy.
- `infra/` — all AWS-specific: EC2 bootstrap (`setup-instance.sh`, targets `dnf`/AL2023), IAM role
  creation, SQS interaction queue, Lambda deploy.
- `lambda/` — `wake_sleep.py`, EC2-only auto-sleep/wake via SQS + Lambda.
- `systemd/` — service units for bot, dashboard, Caddy (portable, not AWS-specific).
- Git branches today: `main`, `backend-feature`, `ui-feature` — no hosted/app split yet.
- No install script for a non-EC2 Linux box; no CLI; no Docker image.

## Stage 1 — Branch & code structuring

**Gate:** each branch builds and runs standalone, containing only what its track needs.

Branches:
- `main` — trunk; stage gates merge here.
- `hosted` / `hosted-dev` — the self-hostable server product (`bot/`, `supervisor/`, `frontend/`,
  `systemd/`). AWS pieces (`ec2_control.py`, `lambda/`, the EC2-only `infra/` scripts) stay behind
  `HOST_CONTROLLER=ec2` opt-in, not required to run.
- `app-linux` / `app-linux-dev` — local desktop execution track. Branches only once this track's
  work actually starts (Stage 4).
- `app-android` / `app-android-dev` — mobile track. Branches only once Stage 5 starts.

No `app-windows` / `app-mac` / `app-ios` branches yet — those are bonus sub-scopes of the desktop
and mobile tracks (Stages 5 and 7), not separate tracks. Spin one up only when work on it begins.

## Stage 2 — CI/CD pipeline

**Gate:** every push/PR to `main`, `hosted`, and any `-dev` branch runs automated checks; merges
to a protected branch require the pipeline to pass.

- Lint + test on push/PR (Python: `bot/`, `supervisor/`, `lambda/`; JS: `frontend/`).
- Build check: frontend build (`vite build`), Python import/syntax check.
- Branch protection on `main` and `hosted` — no direct push, checks required to merge.
- Deploy step wired later (Stage 4, once Docker image exists) — CI/CD groundwork now, actual
  auto-deploy isn't a hard requirement of this stage.


## Stage 3 — Easy native Linux install

**Goal:** `git clone` + one command installs everything on any mainstream Linux distro.

- Generalize `infra/setup-instance.sh` into a distro-aware installer (support at least
  `apt`/`dnf`); drop the AL2023/EC2-only assumptions.
- Installer does **not** collect secrets (Discord bot token, etc.) — that happens after install,
  via the supervisor UI setup wizard.
- CLI fallback for when the UI isn't available: `spottydaemon --bot-token ... --client-id ...`
  covering the same setup inputs as the UI wizard.
- `spottydaemon --help` documents every subcommand.
- Rewrite README with an accurate, EC2-independent Linux install guide.

## Stage 4 — Docker image

**Goal:** same one-command ergonomics via Docker.

- `Dockerfile` + `docker-compose.yml`.
- Runtime config via compose environment/args (e.g. `MAX_SLOTS=10`).
- Document in README alongside the native install path.

## Stage 5 (bonus) — Desktop app

- Package the local-run flow as an installable app, Linux first (validated already by the
  author's own Fedora testing laptop).
- Windows app: stretch, only after Linux path is solid.
- Mac app: stretch, only after Linux path is solid.
- Scope beyond "runs the existing bot locally with a UI" is TBD.

## Stage 6 — Android app

- Locally hosts the bot on-device.
- Scope TBD.

## Stage 7 (optional) — iPhone app

- Same idea as Android; iOS background-execution limits are the likely blocker. Optional, revisit
  after Stage 6 lands.

## Open questions

- How much of `bot/`/`supervisor/` is shared vs. forked between `hosted` and `app-linux` once the
  desktop track starts.
- Desktop/mobile app scope (Stages 5–7) not yet defined beyond "run the bot locally like the
  Fedora test setup."
