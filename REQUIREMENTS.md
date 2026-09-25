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

## Current state (2026-09-25)

- Working prototype, deployed manually to a private AWS EC2 instance. Deploy is a manual
  `git pull` + `rsync` + `systemctl restart` (`deploy.txt`, `infra/deploy-bot.sh`).
- `bot/` — Discord bot + Spotify Connect integration (host-agnostic except `ec2_control.py`).
- `supervisor/` — process manager + auth + API that runs/monitors the bot, backs the dashboard.
- `frontend/` — React setup wizard / dashboard UI, served via Caddy reverse proxy.
- `infra/` — all AWS-specific: EC2 bootstrap (`setup-instance.sh`, targets `dnf`/AL2023), IAM role
  creation, SQS interaction queue, Lambda deploy.
- `lambda/` — `wake_sleep.py`, EC2-only auto-sleep/wake via SQS + Lambda.
- `systemd/` — service units for bot, dashboard, Caddy (portable, not AWS-specific).
- Git branches today: `main`, `hosted`, `hosted-dev` (Stage 1).
- CI on push/PR with branch protection on `main`/`hosted` (Stage 2).
- One-command `install.sh` for apt/dnf distros, `update.sh`, `spottydaemon` CLI (Stage 3,
  closed 2026-09-25). No Docker image yet.
- Queue: everything added from the dashboard or Discord goes into a bot-managed, reorderable
  "Up next" list (`bot/up_next.py`), fed into Spotify's real queue one song at a time, since
  Spotify's API can't reorder or remove queue entries. The dashboard's left panel shows
  Spotify App Queue / Up next / Playlist; queued-vs-playlist is inferred by diffing queue
  snapshots (multiset diff at the head of the queue), so it's a heuristic.

## Stage 1 — Branch & code structuring ✅ Done

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

## Stage 2 — CI/CD pipeline ✅ Done

Backend CI: `py_compile`, `ruff check` (bug-catching rules, `ruff.toml`), and every
`bot/test_*.py`. Branch protection on `main`/`hosted` enforced for admins too.

**Gate:** every push/PR to `main`, `hosted`, and any `-dev` branch runs automated checks; merges
to a protected branch require the pipeline to pass.

- Lint + test on push/PR (Python: `bot/`, `supervisor/`, `lambda/`; JS: `frontend/`).
- Build check: frontend build (`vite build`), Python import/syntax check.
- Branch protection on `main` and `hosted` — no direct push, checks required to merge.
- Deploy step wired later (Stage 4, once Docker image exists) — CI/CD groundwork now, actual
  auto-deploy isn't a hard requirement of this stage.


## Stage 3 — Easy native Linux install ✅ Done (2026-09-25)

**Goal:** `git clone` + one command installs everything on any mainstream Linux distro.

Closed: verified by a fresh install on a new Ubuntu VM (apt path) running the bot on Discord,
alongside the earlier Amazon Linux 2023 (dnf path) installs. `install.sh` + `update.sh` +
`spottydaemon` CLI, README rewritten. The CLI takes the Spotify client ID via
`spottydaemon set SPOTIFY_CLIENT_ID=...` / `setup --set ...` rather than a dedicated
`--client-id` flag. No-domain installs can serve the dashboard on the LAN over plain HTTP
(`SUPERVISOR_HOST=0.0.0.0`, offered by the installer); Spotify Web API linking there uses the
`http://127.0.0.1:5589/callback` paste-back flow.

- Generalize `infra/setup-instance.sh` into a distro-aware installer (support at least
  `apt`/`dnf`); drop the AL2023/EC2-only assumptions.
- Installer does **not** collect secrets (Discord bot token, etc.) — that happens after install,
  via the supervisor UI setup wizard.
- CLI fallback for when the UI isn't available: `spottydaemon --bot-token ... --client-id ...`
  covering the same setup inputs as the UI wizard.
- `spottydaemon --help` documents every subcommand.
- Rewrite README with an accurate, EC2-independent Linux install guide.

## Stage 3.5 (bonus) — Frontend improvements

**Goal:** polish the dashboard / setup-wizard UX now that the install path is solid. Numbered 3.5
so later stage numbers stay stable.

- Scope TBD. Known rough edges found during Stage 3 testing:
  - Profile page keeps polling `player-state` every 3s (and logging 400s) while the profile's
    Spotify Web API isn't linked yet — skip polling until it is.
  - While the bot is still logging in to Discord, dashboard proxy calls surface as 500s with a
    traceback — return a clean 503 "bot starting" and show that state in the UI.
  - Spotify Web API paste-back linking is confusing on a no-domain install — clearer in-UI
    guidance (exact redirect URI to register, what the failed `127.0.0.1` page means).

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
