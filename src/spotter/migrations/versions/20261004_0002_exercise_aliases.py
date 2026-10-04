"""Exercise aliases for map_exercise (decisions.md, "map_exercise persists through aliases").

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exercise_aliases",
        sa.Column("garmin_category", sa.Text(), nullable=False),
        sa.Column("garmin_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("exercise_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"],
            ["exercises.id"],
            name=op.f("fk_exercise_aliases_exercise_id_exercises"),
        ),
        sa.PrimaryKeyConstraint("garmin_category", "garmin_name", name=op.f("pk_exercise_aliases")),
    )


def downgrade() -> None:
    op.drop_table("exercise_aliases")
