"""Local command line. Run against production with `uv run --env-file .env.local spotter ...`."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from spotter.config import get_settings
from spotter.db.schema import activities, exercise_session_stats, exercises, performed_sets
from spotter.db.seed import seed_exercises
from spotter.db.session import get_engine
from spotter.garmin import client as garmin_client
from spotter.garmin import tokens
from spotter.garmin.errors import GarminError
from spotter.garmin.mappers import STRENGTH_TYPE
from spotter.garmin.session import garmin_session
from spotter.sync import metrics, stats
from spotter.sync.sync import SyncResult, run_sync
from spotter.units import kg_to_lb


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


def _print_sync(result: SyncResult, refreshed: bool) -> None:
    for key, value in result.as_dict().items():
        print(f"{key}: {'' if value is None else str(value).lower()}")
    print(f"tokens_refreshed: {str(refreshed).lower()}")


def _sync(args: argparse.Namespace) -> None:
    engine = get_engine()
    lock_engine = get_engine(get_settings().migration_url)
    with garmin_session(engine) as session:
        result = run_sync(
            engine,
            session.client,
            full=args.full,
            force=args.force,
            metrics_days=args.metrics_days,
            lock_engine=lock_engine,
        )
    _print_sync(result, session.refreshed)


def _backfill(args: argparse.Namespace) -> None:
    engine = get_engine()
    lock_engine = get_engine(get_settings().migration_url)
    with garmin_session(engine) as session:
        result = run_sync(
            engine,
            session.client,
            since=date.fromisoformat(args.since),
            full=args.full,
            force=True,
            budget_s=None,
            metrics_days=args.metrics_days,
            lock_engine=lock_engine,
        )
    _print_sync(result, session.refreshed)


def _rebuild_stats(args: argparse.Namespace) -> None:
    with get_engine().begin() as conn:
        rebuilt = stats.rebuild_all(conn, exercise_id=args.exercise_id)
    print(f"activities rebuilt: {rebuilt}")


def _lb(kg: Decimal | None) -> str:
    return "BW" if kg is None else f"{kg_to_lb(kg)} lb"


def _strength_log(args: argparse.Namespace) -> None:
    """Recent strength sessions in lb: every set, then the derived stats."""
    with get_engine().connect() as conn:
        sessions = conn.execute(
            select(activities.c.garmin_activity_id, activities.c.local_date)
            .where(activities.c.type == STRENGTH_TYPE)
            .order_by(activities.c.start_time.desc())
            .limit(args.limit)
        ).all()
        for s in sessions:
            print(f"{s.local_date}  activity {s.garmin_activity_id}")
            sets = conn.execute(
                select(performed_sets, exercises.c.display_name)
                .outerjoin(exercises, exercises.c.id == performed_sets.c.exercise_id)
                .where(performed_sets.c.activity_id == s.garmin_activity_id)
                .order_by(performed_sets.c.set_index)
            ).all()
            for p in sets:
                name = p.display_name or f"{p.garmin_category}/{p.garmin_name} (unmapped)"
                print(f"  set {p.set_index:>2}  {name}: {p.reps} x {_lb(p.weight_kg)}")
            rows = conn.execute(
                select(exercise_session_stats, exercises.c.display_name)
                .join(exercises, exercises.c.id == exercise_session_stats.c.exercise_id)
                .where(exercise_session_stats.c.activity_id == s.garmin_activity_id)
                .order_by(exercises.c.display_name)
            ).all()
            for r in rows:
                e1rm = "-" if r.best_e1rm_kg is None else _lb(r.best_e1rm_kg)
                print(
                    f"  stats  {r.display_name}: top {_lb(r.top_set_weight_kg)} x {r.top_set_reps}"
                    f", working sets {r.working_sets}, reps {r.total_reps}"
                    f", volume {kg_to_lb(r.volume_kg)} lb, e1rm {e1rm}"
                )


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

    p = sub.add_parser(
        "sync", help="Sync recent activities, strength sets and daily metrics (60 s budget)."
    )
    p.add_argument("--force", action="store_true", help="Ignore the 10-minute minimum interval")
    p.add_argument("--full", action="store_true", help="Re-read sets of every strength session")
    p.add_argument(
        "--metrics-days",
        type=int,
        default=metrics.RANGE_DAYS,
        help=f"Daily metrics window ending today (default {metrics.RANGE_DAYS})",
    )
    p.set_defaults(func=_sync)

    p = sub.add_parser("backfill", help="Sync history since a date, with no time budget.")
    p.add_argument("--since", required=True, help="YYYY-MM-DD (activities)")
    p.add_argument("--full", action="store_true", help="Re-read sets already stored")
    p.add_argument(
        "--metrics-days",
        type=int,
        default=metrics.BACKFILL_DAYS,
        help=f"Daily metrics window ending today (default {metrics.BACKFILL_DAYS})",
    )
    p.set_defaults(func=_backfill)

    p = sub.add_parser("rebuild-stats", help="Recompute exercise session stats from stored sets.")
    p.add_argument("--exercise-id", type=int, help="Only sessions with this exercise")
    p.set_defaults(func=_rebuild_stats)

    p = sub.add_parser("strength-log", help="Show recent strength sessions and stats in lb.")
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=_strength_log)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (GarminError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
