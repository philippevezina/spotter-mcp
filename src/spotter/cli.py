"""Local command line. Run against production with `uv run --env-file .env.local spotter ...`."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import func, select

from spotter.db.schema import exercises
from spotter.db.seed import seed_exercises
from spotter.db.session import get_engine
from spotter.garmin import client as garmin_client
from spotter.garmin import tokens
from spotter.garmin.errors import GarminError
from spotter.garmin.session import garmin_session


def _bootstrap_login(_: argparse.Namespace) -> None:
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    token_json = garmin_client.login_with_credentials(
        email, password, prompt_mfa=lambda: input("MFA code: ").strip()
    )
    with get_engine().begin() as conn:
        tokens.save_tokens(conn, token_json)
    print("Garmin tokens saved (encrypted).")


def _import_tokens(args: argparse.Namespace) -> None:
    token_json = Path(args.path).read_text()
    keys = sorted(tokens.validate_token_json(token_json))
    with get_engine().begin() as conn:
        tokens.save_tokens(conn, token_json)
    print(f"Garmin tokens imported (encrypted). Keys: {', '.join(keys)}")


def _garmin_check(_: argparse.Namespace) -> None:
    with garmin_session(get_engine()) as session:
        session.client.check()
    print("ok")
    print(f"refreshed: {str(session.refreshed).lower()}")


def _seed_exercises(_: argparse.Namespace) -> None:
    with get_engine().begin() as conn:
        inserted = seed_exercises(conn, garmin_client.exercise_catalog())
        total = conn.execute(select(func.count()).select_from(exercises)).scalar_one()
    print(f"inserted: {inserted}, total: {total}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotter", description="Spotter admin commands. Works with Garmin Connect."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "bootstrap-login",
        help="Log in to Garmin interactively and store encrypted tokens. On 429, stop.",
    )
    p.set_defaults(func=_bootstrap_login)

    p = sub.add_parser("import-tokens", help="Store an existing token JSON file, encrypted.")
    p.add_argument("path", help="Path to a token store JSON file")
    p.set_defaults(func=_import_tokens)

    p = sub.add_parser(
        "garmin-check", help="Verify the stored tokens with one read call. Saves a refresh."
    )
    p.set_defaults(func=_garmin_check)

    p = sub.add_parser("seed-exercises", help="Load the Garmin exercise catalog. Idempotent.")
    p.set_defaults(func=_seed_exercises)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (GarminError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
