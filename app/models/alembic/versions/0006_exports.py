"""exports table

Revision ID: 0006_exports
Revises: 0005_value_edits
Create Date: 2026-11-07 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_exports"
down_revision: str | None = "0005_value_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("encoding", sa.String(8), nullable=False),
        sa.Column("include_photos", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(128), nullable=True),
        sa.Column("file_path", sa.String(256), nullable=True),
        sa.Column("preview", sa.JSON(), nullable=True),
        sa.Column("skipped_unconfirmed", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_exports_case_id", "exports", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_exports_case_id", table_name="exports")
    op.drop_table("exports")
