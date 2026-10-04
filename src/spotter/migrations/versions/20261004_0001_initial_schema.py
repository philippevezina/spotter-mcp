"""Initial schema (SPEC section 7, without kv_store).

Revision ID: 0001
Revises:
Create Date: 2026-10-04 16:40:06.104281
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("garmin_activity_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("start_time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("distance_m", sa.Numeric(precision=10, scale=1), nullable=True),
        sa.Column("elevation_gain_m", sa.Numeric(precision=7, scale=1), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("training_load", sa.Numeric(precision=7, scale=1), nullable=True),
        sa.Column("aerobic_te", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("anaerobic_te", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("perceived_effort", sa.Integer(), nullable=True),
        sa.Column("feel", sa.Integer(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "synced_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("garmin_activity_id", name=op.f("pk_activities")),
    )
    op.create_index("activities_date", "activities", ["local_date"], unique=False)
    op.create_table(
        "athlete",
        sa.Column(
            "id",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("display_units", sa.Text(), server_default=sa.text("'lb'"), nullable=False),
        sa.Column(
            "time_zone", sa.Text(), server_default=sa.text("'America/Toronto'"), nullable=False
        ),
        sa.Column("bodyweight_kg", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("training_age_years", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("available_days", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("session_minutes", sa.Integer(), nullable=True),
        sa.Column("equipment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_athlete_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete")),
    )
    op.create_table(
        "daily_metrics",
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("resting_hr", sa.Integer(), nullable=True),
        sa.Column("hrv_overnight_ms", sa.Integer(), nullable=True),
        sa.Column("hrv_status", sa.Text(), nullable=True),
        sa.Column("hrv_baseline_low", sa.Integer(), nullable=True),
        sa.Column("hrv_baseline_high", sa.Integer(), nullable=True),
        sa.Column("sleep_score", sa.Integer(), nullable=True),
        sa.Column("sleep_s", sa.Integer(), nullable=True),
        sa.Column("body_battery_max", sa.Integer(), nullable=True),
        sa.Column("body_battery_min", sa.Integer(), nullable=True),
        sa.Column("training_readiness", sa.Integer(), nullable=True),
        sa.Column("acute_load", sa.Numeric(precision=7, scale=1), nullable=True),
        sa.Column("load_balance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stress_avg", sa.Integer(), nullable=True),
        sa.Column("vo2max_run", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("vo2max_bike", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("bodyweight_kg", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "synced_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("local_date", name=op.f("pk_daily_metrics")),
    )
    op.create_table(
        "exercises",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("garmin_category", sa.Text(), nullable=False),
        sa.Column("garmin_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("curated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("movement_pattern", sa.Text(), nullable=True),
        sa.Column("primary_muscles", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("secondary_muscles", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("load_type", sa.Text(), nullable=True),
        sa.Column("increment_lb", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("e1rm_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exercises")),
        sa.UniqueConstraint(
            "garmin_category", "garmin_name", name=op.f("uq_exercises_garmin_category_garmin_name")
        ),
    )
    op.create_table(
        "garmin_tokens",
        sa.Column(
            "id",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_garmin_tokens_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_garmin_tokens")),
    )
    op.create_table(
        "sync_state",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("last_synced_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("source", name=op.f("pk_sync_state")),
    )
    op.create_table(
        "goals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_exercise_id", sa.BigInteger(), nullable=True),
        sa.Column("target_value", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("target_unit", sa.Text(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["target_exercise_id"],
            ["exercises.id"],
            name=op.f("fk_goals_target_exercise_id_exercises"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goals")),
    )
    op.create_table(
        "performed_sets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("activity_id", sa.BigInteger(), nullable=False),
        sa.Column("set_index", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.BigInteger(), nullable=True),
        sa.Column("garmin_category", sa.Text(), nullable=True),
        sa.Column("garmin_name", sa.Text(), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=True),
        sa.Column("weight_kg", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("duration_s", sa.Numeric(precision=7, scale=1), nullable=True),
        sa.Column("start_time", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.garmin_activity_id"],
            name=op.f("fk_performed_sets_activity_id_activities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["exercises.id"], name=op.f("fk_performed_sets_exercise_id_exercises")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_performed_sets")),
        sa.UniqueConstraint(
            "activity_id", "set_index", name=op.f("uq_performed_sets_activity_id_set_index")
        ),
    )
    op.create_table(
        "plans",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("goal_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("planned_end_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("weeks", sa.Integer(), nullable=False),
        sa.Column("sessions_per_week", sa.Integer(), nullable=False),
        sa.Column("deload_week", sa.Integer(), nullable=True),
        sa.Column("progression_model", sa.Text(), nullable=False),
        sa.Column("end_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("outcome_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name=op.f("fk_plans_goal_id_goals")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
    )
    op.create_index(
        "one_active_plan",
        "plans",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "adjustments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name=op.f("fk_adjustments_plan_id_plans")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjustments")),
    )
    op.create_table(
        "plan_days",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("day_order", sa.Integer(), nullable=False),
        sa.Column("preferred_weekday", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name=op.f("fk_plan_days_plan_id_plans"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_days")),
        sa.UniqueConstraint("plan_id", "day_order", name=op.f("uq_plan_days_plan_id_day_order")),
    )
    op.create_table(
        "plan_exercises",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_day_id", sa.BigInteger(), nullable=False),
        sa.Column("exercise_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("superset_group", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), server_default=sa.text("'accessory'"), nullable=False),
        sa.Column("sets", sa.Integer(), nullable=False),
        sa.Column("rep_min", sa.Integer(), nullable=False),
        sa.Column("rep_max", sa.Integer(), nullable=False),
        sa.Column("target_rir", sa.Integer(), nullable=False),
        sa.Column("rest_s", sa.Integer(), nullable=False),
        sa.Column("progression_override", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["exercises.id"], name=op.f("fk_plan_exercises_exercise_id_exercises")
        ),
        sa.ForeignKeyConstraint(
            ["plan_day_id"],
            ["plan_days.id"],
            name=op.f("fk_plan_exercises_plan_day_id_plan_days"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_exercises")),
        sa.UniqueConstraint(
            "plan_day_id", "position", name=op.f("uq_plan_exercises_plan_day_id_position")
        ),
    )
    op.create_table(
        "scheduled_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_day_id", sa.BigInteger(), nullable=False),
        sa.Column("week_no", sa.Integer(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("garmin_workout_id", sa.BigInteger(), nullable=True),
        sa.Column("garmin_schedule_id", sa.BigInteger(), nullable=True),
        sa.Column("activity_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.garmin_activity_id"],
            name=op.f("fk_scheduled_sessions_activity_id_activities"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_day_id"],
            ["plan_days.id"],
            name=op.f("fk_scheduled_sessions_plan_day_id_plan_days"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_sessions")),
        sa.UniqueConstraint(
            "plan_day_id", "week_no", name=op.f("uq_scheduled_sessions_plan_day_id_week_no")
        ),
    )
    op.create_table(
        "exercise_session_stats",
        sa.Column("activity_id", sa.BigInteger(), nullable=False),
        sa.Column("exercise_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("top_set_weight_kg", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("top_set_reps", sa.Integer(), nullable=True),
        sa.Column("best_e1rm_kg", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("total_reps", sa.Integer(), nullable=True),
        sa.Column("volume_kg", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("working_sets", sa.Integer(), nullable=True),
        sa.Column("scheduled_session_id", sa.BigInteger(), nullable=True),
        sa.Column("met_prescription", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.garmin_activity_id"],
            name=op.f("fk_exercise_session_stats_activity_id_activities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["exercises.id"],
            name=op.f("fk_exercise_session_stats_exercise_id_exercises"),
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_session_id"],
            ["scheduled_sessions.id"],
            name=op.f("fk_exercise_session_stats_scheduled_session_id_scheduled_sessions"),
        ),
        sa.PrimaryKeyConstraint(
            "activity_id", "exercise_id", name=op.f("pk_exercise_session_stats")
        ),
    )
    op.create_table(
        "prescribed_sets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scheduled_session_id", sa.BigInteger(), nullable=False),
        sa.Column("exercise_id", sa.BigInteger(), nullable=False),
        sa.Column("set_no", sa.Integer(), nullable=False),
        sa.Column("target_reps", sa.Integer(), nullable=False),
        sa.Column("target_weight_kg", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("target_rir", sa.Integer(), nullable=True),
        sa.Column("rest_s", sa.Integer(), nullable=True),
        sa.Column("rule_fired", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["exercises.id"], name=op.f("fk_prescribed_sets_exercise_id_exercises")
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_session_id"],
            ["scheduled_sessions.id"],
            name=op.f("fk_prescribed_sets_scheduled_session_id_scheduled_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prescribed_sets")),
        sa.UniqueConstraint(
            "scheduled_session_id",
            "exercise_id",
            "set_no",
            name=op.f("uq_prescribed_sets_scheduled_session_id_exercise_id_set_no"),
        ),
    )
    op.create_table(
        "session_notes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("scheduled_session_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rir_feedback", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_session_id"],
            ["scheduled_sessions.id"],
            name=op.f("fk_session_notes_scheduled_session_id_scheduled_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_notes")),
    )


def downgrade() -> None:
    op.drop_table("session_notes")
    op.drop_table("prescribed_sets")
    op.drop_table("exercise_session_stats")
    op.drop_table("scheduled_sessions")
    op.drop_table("plan_exercises")
    op.drop_table("plan_days")
    op.drop_table("adjustments")
    op.drop_index(
        "one_active_plan", table_name="plans", postgresql_where=sa.text("status = 'active'")
    )
    op.drop_table("plans")
    op.drop_table("performed_sets")
    op.drop_table("goals")
    op.drop_table("sync_state")
    op.drop_table("garmin_tokens")
    op.drop_table("exercises")
    op.drop_table("daily_metrics")
    op.drop_table("athlete")
    op.drop_index("activities_date", table_name="activities")
    op.drop_table("activities")
