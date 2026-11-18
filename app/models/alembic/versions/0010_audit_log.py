"""audit log hash chain

Revision ID: 0010_audit_log
Revises: 0009_export_merge_id
Create Date: 2026-11-16 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_audit_log"
down_revision: str | None = "0009_export_merge_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("at", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_audit_log_case_id", "audit_log", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_case_id", table_name="audit_log")
    op.drop_table("audit_log")
