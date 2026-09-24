"""spottydaemon CLI -- headless fallback for the cockpit UI's setup wizard
(supervisor/main.py's POST /api/supervisor/setup), for installs with no
browser handy. Reuses env_file.py/admin_store.py directly rather than
reimplementing their .env/admin.json handling."""

import argparse
import secrets
import subprocess
import sys

import admin_store
import env_file

SERVICES = ["discord-music-bot", "discord-dashboard"]


def _apply_pairs(pairs: list[str]) -> dict[str, str]:
    values = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            print(f"skipping malformed KEY=VALUE: {pair!r}", file=sys.stderr)
            continue
        values[key] = value
    return values


def cmd_setup(args: argparse.Namespace) -> None:
    if admin_store.is_configured():
        print("already set up -- use `spottydaemon set` to change values, or the cockpit UI's login screen")
        sys.exit(1)
    if len(args.admin_password) < 8:
        print("admin password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)

    values = {"DISCORD_TOKEN": args.bot_token, **_apply_pairs(args.set)}
    if not env_file.read_env().get("API_TOKEN"):
        values.setdefault("API_TOKEN", secrets.token_hex(32))
    env_file.set_env_values(values)
    admin_store.set_password(args.admin_password)
    print("saved. start it with: spottydaemon start")


def cmd_set(args: argparse.Namespace) -> None:
    values = _apply_pairs(args.pairs)
    if not values:
        print("nothing to set -- pass KEY=VALUE pairs", file=sys.stderr)
        sys.exit(1)
    env_file.set_env_values(values)
    print("set: " + ", ".join(values))


def cmd_passwd(args: argparse.Namespace) -> None:
    if not admin_store.is_configured():
        print("not set up yet -- run `spottydaemon setup` first", file=sys.stderr)
        sys.exit(1)
    if len(args.new_password) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)
    admin_store.set_password(args.new_password)
    print("admin password changed")


def _make_systemctl_cmd(action: str):
    def _run(args: argparse.Namespace) -> None:
        subprocess.run(["systemctl", action, *SERVICES], check=False)

    return _run


def cmd_status(args: argparse.Namespace) -> None:
    print("configured:", admin_store.is_configured())
    subprocess.run(["systemctl", "status", "--no-pager", "-l", *SERVICES], check=False)


def main() -> None:
    parser = argparse.ArgumentParser(prog="spottydaemon", description="Manage this bot install.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="first-run setup: bot token + admin password")
    p_setup.add_argument("--bot-token", required=True, help="Discord bot token (Bot page -> Reset Token)")
    p_setup.add_argument("--admin-password", required=True, help="cockpit UI login password, >=8 chars")
    p_setup.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE",
        help="extra .env value, repeatable (e.g. --set MAX_SLOTS=10)",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_set = sub.add_parser(
        "set",
        help="set one or more .env values without full setup (e.g. SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, MAX_SLOTS)",
    )
    p_set.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    p_set.set_defaults(func=cmd_set)

    p_passwd = sub.add_parser("passwd", help="change the admin (cockpit UI) password")
    p_passwd.add_argument("new_password", metavar="NEW_PASSWORD")
    p_passwd.set_defaults(func=cmd_passwd)

    for action in ("start", "stop", "restart"):
        p = sub.add_parser(action, help=f"{action} the bot + dashboard services")
        p.set_defaults(func=_make_systemctl_cmd(action))

    p_status = sub.add_parser("status", help="show setup + service status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
