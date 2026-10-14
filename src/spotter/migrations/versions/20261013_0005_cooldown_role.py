"""Cool-down role: stretches at the end of a plan day, timed, with no target RIR.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_CHECK = "role IN ('main', 'secondary', 'accessory', 'cooldown')"
COOLDOWN_CHECK = (
    "(role = 'cooldown') = (target_rir IS NULL) AND (role <> 'cooldown' OR hold_min_s IS NOT NULL)"
)


def upgrade() -> None:
    op.alter_column("plan_exercises", "target_rir", nullable=True)
    op.create_check_constraint("role", "plan_exercises", ROLE_CHECK)
    op.create_check_constraint("cooldown", "plan_exercises", COOLDOWN_CHECK)


def downgrade() -> None:
    # Cool-down rows have no target RIR; they cannot survive the NOT NULL column.
    op.execute("DELETE FROM plan_exercises WHERE role = 'cooldown'")
    op.drop_constraint("cooldown", "plan_exercises", type_="check")
    op.drop_constraint("role", "plan_exercises", type_="check")
    op.alter_column("plan_exercises", "target_rir", nullable=False)
