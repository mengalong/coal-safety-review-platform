"""add model gateway security and call audit

Revision ID: 319adf728c10
Revises: f7a91c2d4e60
Create Date: 2026-07-29 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "319adf728c10"
down_revision: str | Sequence[str] | None = "f7a91c2d4e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_config", sa.Column("credential_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "model_config",
        sa.Column("key_rotated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "model_call_log",
        sa.Column("model_config_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["model_config_id"], ["model_config.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_model_call_log_model_config_id", "model_call_log", ["model_config_id"])
    op.create_index("ix_model_call_log_trace_id", "model_call_log", ["trace_id"])
    op.create_index("ix_model_call_log_operation", "model_call_log", ["operation"])
    op.create_index("ix_model_call_log_status", "model_call_log", ["status"])


def downgrade() -> None:
    op.drop_index("ix_model_call_log_status", table_name="model_call_log")
    op.drop_index("ix_model_call_log_operation", table_name="model_call_log")
    op.drop_index("ix_model_call_log_trace_id", table_name="model_call_log")
    op.drop_index("ix_model_call_log_model_config_id", table_name="model_call_log")
    op.drop_table("model_call_log")
    op.drop_column("model_config", "key_rotated_at")
    op.drop_column("model_config", "credential_version")
