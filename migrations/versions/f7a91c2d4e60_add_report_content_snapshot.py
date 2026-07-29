"""add report content snapshot

Revision ID: f7a91c2d4e60
Revises: 6d8c4be217a1
Create Date: 2026-07-29 15:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a91c2d4e60"
down_revision: str | Sequence[str] | None = "6d8c4be217a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("report", sa.Column("content_snapshot", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("report", "content_snapshot")
