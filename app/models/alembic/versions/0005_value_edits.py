"""value edit history

Revision ID: 0005_value_edits
Revises: 0004_photos
Create Date: 2026-11-06 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_value_edits"
down_revision: str | None = "0004_photos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "value_edits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("photo_id", sa.Integer(), sa.ForeignKey("photos.id"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("value_key", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=False),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("edited_by", sa.String(64), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_value_edits_case_id", "value_edits", ["case_id"])
    op.create_index("ix_value_edits_photo_key", "value_edits", ["photo_id", "value_key"])


def downgrade() -> None:
    op.drop_index("ix_value_edits_photo_key", table_name="value_edits")
    op.drop_index("ix_value_edits_case_id", table_name="value_edits")
    op.drop_table("value_edits")
