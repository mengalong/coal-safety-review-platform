"""add auth session

Revision ID: 6d8c4be217a1
Revises: 20d253b7e1b5
Create Date: 2026-07-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d8c4be217a1"
down_revision: str | Sequence[str] | None = "20d253b7e1b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_session",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_session_expires_at"), "auth_session", ["expires_at"], unique=False)
    op.create_index(op.f("ix_auth_session_status"), "auth_session", ["status"], unique=False)
    op.create_index(op.f("ix_auth_session_user_id"), "auth_session", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_session_user_id"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_status"), table_name="auth_session")
    op.drop_index(op.f("ix_auth_session_expires_at"), table_name="auth_session")
    op.drop_table("auth_session")
