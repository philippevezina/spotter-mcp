"""Workout id on activities, for linking by pushed workout (Phase 5, SPEC section 9).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("garmin_workout_id", sa.BigInteger(), nullable=True))
    op.create_index("activities_workout", "activities", ["garmin_workout_id"])
    op.execute(
        "UPDATE activities SET garmin_workout_id = (raw_json->>'workoutId')::bigint "
        "WHERE raw_json->>'workoutId' ~ '^[0-9]+$'"
    )


def downgrade() -> None:
    op.drop_index("activities_workout", table_name="activities")
    op.drop_column("activities", "garmin_workout_id")
