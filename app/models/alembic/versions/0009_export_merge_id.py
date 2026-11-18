"""exports.merge_id for merge_raw exports

Revision ID: 0009_export_merge_id
Revises: 0008_data_files_merges
Create Date: 2026-11-08 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_export_merge_id"
down_revision: str | None = "0008_data_files_merges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("exports") as batch:
        batch.add_column(sa.Column("merge_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_exports_merge_id_merges", "merges", ["merge_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_exports_merge_id", ["merge_id"])


def downgrade() -> None:
    with op.batch_alter_table("exports") as batch:
        batch.drop_index("ix_exports_merge_id")
        batch.drop_constraint("fk_exports_merge_id_merges", type_="foreignkey")
        batch.drop_column("merge_id")
