"""create export jobs table

Revision ID: 20260302_0007
Revises: 20260302_0006
Create Date: 2026-03-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260302_0007"
down_revision = "20260302_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("export_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False, server_default=sa.text("'txt'")),
        sa.Column("artifact_storage_key", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_export_jobs_created_at", "export_jobs", ["created_at"], unique=False)
    op.create_index("ix_export_jobs_status", "export_jobs", ["status"], unique=False)
    op.create_index("ix_export_jobs_user_id", "export_jobs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_export_jobs_user_id", table_name="export_jobs")
    op.drop_index("ix_export_jobs_status", table_name="export_jobs")
    op.drop_index("ix_export_jobs_created_at", table_name="export_jobs")
    op.drop_table("export_jobs")
