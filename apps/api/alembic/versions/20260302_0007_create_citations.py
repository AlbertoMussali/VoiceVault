"""create citations and brag bullet citation mapping tables

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
        "citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_version", sa.Integer(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=False),
        sa.Column("snippet_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_citations_created_at", "citations", ["created_at"], unique=False)
    op.create_index("ix_citations_transcript_id", "citations", ["transcript_id"], unique=False)
    op.create_index("ix_citations_user_id", "citations", ["user_id"], unique=False)

    op.create_table(
        "brag_bullet_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bullet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("citation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["bullet_id"], ["brag_bullets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["citation_id"], ["citations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bullet_id", "citation_id", name="uq_brag_bullet_citations_bullet_citation"),
    )
    op.create_index("ix_brag_bullet_citations_bullet_id", "brag_bullet_citations", ["bullet_id"], unique=False)
    op.create_index("ix_brag_bullet_citations_citation_id", "brag_bullet_citations", ["citation_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_brag_bullet_citations_citation_id", table_name="brag_bullet_citations")
    op.drop_index("ix_brag_bullet_citations_bullet_id", table_name="brag_bullet_citations")
    op.drop_table("brag_bullet_citations")

    op.drop_index("ix_citations_user_id", table_name="citations")
    op.drop_index("ix_citations_transcript_id", table_name="citations")
    op.drop_index("ix_citations_created_at", table_name="citations")
    op.drop_table("citations")
