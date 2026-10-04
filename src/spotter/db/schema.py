"""SQLAlchemy Core tables (SPEC section 7). Source of truth for Alembic.

Timestamps are UTC `timestamptz`. Dates are the athlete's local date (America/Toronto).
Weights are kg. Only `spotter.units` converts.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

NOW = text("now()")


def _timestamptz() -> TIMESTAMP:
    return TIMESTAMP(timezone=True)


# Reference ---------------------------------------------------------------

athlete = Table(
    "athlete",
    metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=False, server_default=text("1")),
    Column("display_units", Text, nullable=False, server_default=text("'lb'")),
    Column("time_zone", Text, nullable=False, server_default=text("'America/Toronto'")),
    Column("bodyweight_kg", Numeric(6, 2)),
    Column("training_age_years", Numeric(4, 1)),
    Column("available_days", JSONB),
    Column("session_minutes", Integer),
    Column("equipment", JSONB),
    Column("limitations", Text),
    Column("updated_at", _timestamptz(), nullable=False, server_default=NOW),
    CheckConstraint("id = 1", name="single_row"),
)

exercises = Table(
    "exercises",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("display_name", Text, nullable=False),
    Column("garmin_category", Text, nullable=False),
    Column("garmin_name", Text, nullable=False, server_default=text("''")),  # '' = category only
    Column("curated", Boolean, nullable=False, server_default=text("false")),
    Column("movement_pattern", Text),
    Column("primary_muscles", JSONB),
    Column("secondary_muscles", JSONB),
    Column("load_type", Text),
    Column("increment_lb", Numeric(5, 2)),
    Column("e1rm_eligible", Boolean, nullable=False, server_default=text("false")),
    UniqueConstraint("garmin_category", "garmin_name"),
)

# Written by map_exercise. Sync maps a set by exact catalog match first, then by alias.
exercise_aliases = Table(
    "exercise_aliases",
    metadata,
    Column("garmin_category", Text, nullable=False),
    Column("garmin_name", Text, nullable=False, server_default=text("''")),
    Column("exercise_id", BigInteger, ForeignKey("exercises.id"), nullable=False),
    Column("created_at", _timestamptz(), nullable=False, server_default=NOW),
    PrimaryKeyConstraint("garmin_category", "garmin_name"),
)

goals = Table(
    "goals",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("target_exercise_id", BigInteger, ForeignKey("exercises.id")),
    Column("target_value", Numeric(8, 2)),
    Column("target_unit", Text),
    Column("target_date", Date),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("created_at", _timestamptz(), nullable=False, server_default=NOW),
)

# Plan (intent) -----------------------------------------------------------

plans = Table(
    "plans",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("goal_id", BigInteger, ForeignKey("goals.id")),
    Column("name", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'draft'")),
    Column("start_date", Date),
    Column("planned_end_date", Date),
    Column("actual_end_date", Date),
    Column("weeks", Integer, nullable=False),
    Column("sessions_per_week", Integer, nullable=False),
    Column("deload_week", Integer),
    Column("progression_model", Text, nullable=False),
    Column("end_criteria", JSONB, nullable=False),
    Column("rationale", Text, nullable=False),
    Column("outcome_summary", Text),
    Column("created_at", _timestamptz(), nullable=False, server_default=NOW),
)
Index(
    "one_active_plan",
    plans.c.status,
    unique=True,
    postgresql_where=text("status = 'active'"),
)

plan_days = Table(
    "plan_days",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("plan_id", BigInteger, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
    Column("label", Text, nullable=False),
    Column("day_order", Integer, nullable=False),
    Column("preferred_weekday", Text),
    UniqueConstraint("plan_id", "day_order"),
)

plan_exercises = Table(
    "plan_exercises",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "plan_day_id",
        BigInteger,
        ForeignKey("plan_days.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("exercise_id", BigInteger, ForeignKey("exercises.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("superset_group", Text),
    Column("role", Text, nullable=False, server_default=text("'accessory'")),
    Column("sets", Integer, nullable=False),
    Column("rep_min", Integer, nullable=False),
    Column("rep_max", Integer, nullable=False),
    Column("target_rir", Integer, nullable=False),
    Column("rest_s", Integer, nullable=False),
    Column("progression_override", JSONB),
    UniqueConstraint("plan_day_id", "position"),
)

# Actuals (from Garmin) ---------------------------------------------------
# `activities` comes before `scheduled_sessions`, which references it.

activities = Table(
    "activities",
    metadata,
    Column("garmin_activity_id", BigInteger, primary_key=True, autoincrement=False),
    Column("type", Text, nullable=False),
    Column("start_time", _timestamptz(), nullable=False),
    Column("local_date", Date, nullable=False),
    Column("duration_s", Integer),
    Column("distance_m", Numeric(10, 1)),
    Column("elevation_gain_m", Numeric(7, 1)),
    Column("avg_hr", Integer),
    Column("max_hr", Integer),
    Column("training_load", Numeric(7, 1)),
    Column("aerobic_te", Numeric(3, 1)),
    Column("anaerobic_te", Numeric(3, 1)),
    Column("perceived_effort", Integer),
    Column("feel", Integer),
    Column("raw_json", JSONB, nullable=False),
    Column("synced_at", _timestamptz(), nullable=False, server_default=NOW),
)
Index("activities_date", activities.c.local_date)

scheduled_sessions = Table(
    "scheduled_sessions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "plan_day_id",
        BigInteger,
        ForeignKey("plan_days.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("week_no", Integer, nullable=False),
    Column("scheduled_date", Date, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'planned'")),
    Column("garmin_workout_id", BigInteger),
    Column("garmin_schedule_id", BigInteger),
    Column("activity_id", BigInteger, ForeignKey("activities.garmin_activity_id")),
    UniqueConstraint("plan_day_id", "week_no"),
)

prescribed_sets = Table(
    "prescribed_sets",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "scheduled_session_id",
        BigInteger,
        ForeignKey("scheduled_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("exercise_id", BigInteger, ForeignKey("exercises.id"), nullable=False),
    Column("set_no", Integer, nullable=False),
    Column("target_reps", Integer, nullable=False),
    Column("target_weight_kg", Numeric(7, 3)),  # null for bodyweight
    Column("target_rir", Integer),
    Column("rest_s", Integer),
    Column("rule_fired", Text),
    UniqueConstraint("scheduled_session_id", "exercise_id", "set_no"),
)

performed_sets = Table(
    "performed_sets",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "activity_id",
        BigInteger,
        ForeignKey("activities.garmin_activity_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("set_index", Integer, nullable=False),
    Column("exercise_id", BigInteger, ForeignKey("exercises.id")),  # null until mapped
    Column("garmin_category", Text),
    Column("garmin_name", Text),
    Column("reps", Integer),
    Column("weight_kg", Numeric(7, 3)),
    Column("duration_s", Numeric(7, 1)),
    Column("start_time", _timestamptz()),
    UniqueConstraint("activity_id", "set_index"),
)

daily_metrics = Table(
    "daily_metrics",
    metadata,
    Column("local_date", Date, primary_key=True),
    Column("resting_hr", Integer),
    Column("hrv_overnight_ms", Integer),
    Column("hrv_status", Text),
    Column("hrv_baseline_low", Integer),
    Column("hrv_baseline_high", Integer),
    Column("sleep_score", Integer),
    Column("sleep_s", Integer),
    Column("body_battery_max", Integer),
    Column("body_battery_min", Integer),
    Column("training_readiness", Integer),
    Column("acute_load", Numeric(7, 1)),
    Column("load_balance", JSONB),
    Column("stress_avg", Integer),
    Column("vo2max_run", Numeric(4, 1)),
    Column("vo2max_bike", Numeric(4, 1)),
    Column("bodyweight_kg", Numeric(6, 2)),
    Column("raw_json", JSONB, nullable=False),
    Column("synced_at", _timestamptz(), nullable=False, server_default=NOW),
)

exercise_session_stats = Table(
    "exercise_session_stats",
    metadata,
    Column(
        "activity_id",
        BigInteger,
        ForeignKey("activities.garmin_activity_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("exercise_id", BigInteger, ForeignKey("exercises.id"), nullable=False),
    Column("local_date", Date, nullable=False),
    Column("top_set_weight_kg", Numeric(7, 3)),
    Column("top_set_reps", Integer),
    Column("best_e1rm_kg", Numeric(7, 3)),
    Column("total_reps", Integer),
    Column("volume_kg", Numeric(10, 2)),
    Column("working_sets", Integer),
    Column("scheduled_session_id", BigInteger, ForeignKey("scheduled_sessions.id")),
    Column("met_prescription", Boolean),
    PrimaryKeyConstraint("activity_id", "exercise_id"),
)

# Claude's memory ---------------------------------------------------------

adjustments = Table(
    "adjustments",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("plan_id", BigInteger, ForeignKey("plans.id")),
    Column("created_at", _timestamptz(), nullable=False, server_default=NOW),
    Column("kind", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("payload", JSONB),
)

session_notes = Table(
    "session_notes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("local_date", Date, nullable=False),
    Column("scheduled_session_id", BigInteger, ForeignKey("scheduled_sessions.id")),
    Column("text", Text, nullable=False),
    Column("tags", JSONB),
    Column("rir_feedback", JSONB),
    Column("created_at", _timestamptz(), nullable=False, server_default=NOW),
)

# Infrastructure ----------------------------------------------------------
# OAuth client storage is owned by py-key-value's PostgreSQLStore (decisions.md), not here.

sync_state = Table(
    "sync_state",
    metadata,
    Column("source", Text, primary_key=True),
    Column("last_synced_at", _timestamptz()),
    Column("cursor", JSONB),
)

garmin_tokens = Table(
    "garmin_tokens",
    metadata,
    Column("id", SmallInteger, primary_key=True, autoincrement=False, server_default=text("1")),
    Column("ciphertext", LargeBinary, nullable=False),  # Fernet-encrypted token store JSON
    Column("updated_at", _timestamptz(), nullable=False, server_default=NOW),
    CheckConstraint("id = 1", name="single_row"),
)
