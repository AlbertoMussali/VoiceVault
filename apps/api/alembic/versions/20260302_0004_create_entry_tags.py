"""create entry tags table

Revision ID: 20260302_0004
Revises: 20260302_0003
Create Date: 2026-03-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260302_0004"
down_revision = "20260302_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entry_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "tag_id", name="uq_entry_tags_entry_tag"),
    )
    op.create_index("ix_entry_tags_entry_id", "entry_tags", ["entry_id"], unique=False)
    op.create_index("ix_entry_tags_tag_id", "entry_tags", ["tag_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_entry_tags_tag_id", table_name="entry_tags")
    op.drop_index("ix_entry_tags_entry_id", table_name="entry_tags")
    op.drop_table("entry_tags")
