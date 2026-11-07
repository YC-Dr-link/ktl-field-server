"""data files, merges

Revision ID: 0006_data_files_merges
Revises: 0005_value_edits
Create Date: 2026-11-07 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_data_files_merges"
down_revision: str | None = "0005_value_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "data_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("selector", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("stored_name", sa.String(128), nullable=False),
        sa.Column("original_path", sa.String(256), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("detected", sa.JSON(), nullable=True),
        sa.Column("uploaded_by", sa.String(64), nullable=False),
        sa.Column("uploaded_at", _TS, nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_data_files_case_id", "data_files", ["case_id"])

    op.create_table(
        "merges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("selector", sa.String(32), nullable=False),
        sa.Column("file_ids", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("table", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_merges_case_id", "merges", ["case_id"])
    op.create_index("ix_merges_status", "merges", ["status"])


def downgrade() -> None:
    op.drop_index("ix_merges_status", table_name="merges")
    op.drop_index("ix_merges_case_id", table_name="merges")
    op.drop_table("merges")
    op.drop_index("ix_data_files_case_id", table_name="data_files")
    op.drop_table("data_files")
