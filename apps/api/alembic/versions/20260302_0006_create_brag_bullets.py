"""create brag bullets table

Revision ID: 20260302_0006
Revises: 20260302_0005
Create Date: 2026-03-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260302_0006"
down_revision = "20260302_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brag_bullets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("bullet_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brag_bullets_bucket", "brag_bullets", ["bucket"], unique=False)
    op.create_index("ix_brag_bullets_created_at", "brag_bullets", ["created_at"], unique=False)
    op.create_index("ix_brag_bullets_user_id", "brag_bullets", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_brag_bullets_user_id", table_name="brag_bullets")
    op.drop_index("ix_brag_bullets_created_at", table_name="brag_bullets")
    op.drop_index("ix_brag_bullets_bucket", table_name="brag_bullets")
    op.drop_table("brag_bullets")
