"""Timed exercises: measure on exercises, hold ranges, hold targets and hold stats.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN_CHECK = (
    "(rep_min IS NOT NULL AND rep_max IS NOT NULL AND hold_min_s IS NULL"
    " AND hold_max_s IS NULL) OR (rep_min IS NULL AND rep_max IS NULL"
    " AND hold_min_s IS NOT NULL AND hold_max_s IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(
        "exercises",
        sa.Column("measure", sa.Text(), nullable=False, server_default=sa.text("'reps'")),
    )
    op.create_check_constraint("measure", "exercises", "measure IN ('reps', 'time')")

    op.alter_column("plan_exercises", "rep_min", nullable=True)
    op.alter_column("plan_exercises", "rep_max", nullable=True)
    op.add_column("plan_exercises", sa.Column("hold_min_s", sa.Integer(), nullable=True))
    op.add_column("plan_exercises", sa.Column("hold_max_s", sa.Integer(), nullable=True))
    op.create_check_constraint("reps_or_hold", "plan_exercises", PLAN_CHECK)

    op.alter_column("prescribed_sets", "target_reps", nullable=True)
    op.add_column("prescribed_sets", sa.Column("target_hold_s", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "reps_or_hold", "prescribed_sets", "(target_reps IS NULL) <> (target_hold_s IS NULL)"
    )

    op.add_column(
        "exercise_session_stats",
        sa.Column("top_set_hold_s", sa.Numeric(precision=7, scale=1), nullable=True),
    )
    op.add_column(
        "exercise_session_stats",
        sa.Column("total_hold_s", sa.Numeric(precision=8, scale=1), nullable=True),
    )


def downgrade() -> None:
    # Timed rows have no reps; they cannot survive the NOT NULL columns.
    op.execute("DELETE FROM prescribed_sets WHERE target_reps IS NULL")
    op.execute("DELETE FROM plan_exercises WHERE rep_min IS NULL")
    op.drop_column("exercise_session_stats", "total_hold_s")
    op.drop_column("exercise_session_stats", "top_set_hold_s")
    op.drop_constraint("reps_or_hold", "prescribed_sets", type_="check")
    op.drop_column("prescribed_sets", "target_hold_s")
    op.alter_column("prescribed_sets", "target_reps", nullable=False)
    op.drop_constraint("reps_or_hold", "plan_exercises", type_="check")
    op.drop_column("plan_exercises", "hold_max_s")
    op.drop_column("plan_exercises", "hold_min_s")
    op.alter_column("plan_exercises", "rep_max", nullable=False)
    op.alter_column("plan_exercises", "rep_min", nullable=False)
    op.drop_constraint("measure", "exercises", type_="check")
    op.drop_column("exercises", "measure")
