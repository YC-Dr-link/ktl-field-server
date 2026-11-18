"""case reopen history

Revision ID: 0007_case_reopens
Revises: 0006_exports
Create Date: 2026-11-08 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_case_reopens"
down_revision: str | None = "0006_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "case_reopens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reopened_by", sa.String(64), nullable=False),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_case_reopens_case_id", "case_reopens", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_case_reopens_case_id", table_name="case_reopens")
    op.drop_table("case_reopens")
