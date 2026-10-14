"""Removed plan days: update_plan keeps a removed day that has history.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plan_days", sa.Column("removed_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("plan_days", "removed_on")
