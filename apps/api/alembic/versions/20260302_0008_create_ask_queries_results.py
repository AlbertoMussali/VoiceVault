"""create ask queries and ask results tables

Revision ID: 20260302_0008
Revises: 20260302_0007
Create Date: 2026-03-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260302_0008"
down_revision = "20260302_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ask_queries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("result_limit", sa.Integer(), nullable=False, server_default=sa.text("8")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'done'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ask_queries_created_at", "ask_queries", ["created_at"], unique=False)
    op.create_index("ix_ask_queries_user_id", "ask_queries", ["user_id"], unique=False)

    op.create_table(
        "ask_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ask_query_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snippet_text", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("result_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["ask_query_id"], ["ask_queries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ask_query_id", "result_order", name="uq_ask_results_query_order"),
    )
    op.create_index("ix_ask_results_ask_query_id", "ask_results", ["ask_query_id"], unique=False)
    op.create_index("ix_ask_results_created_at", "ask_results", ["created_at"], unique=False)
    op.create_index("ix_ask_results_entry_id", "ask_results", ["entry_id"], unique=False)
    op.create_index("ix_ask_results_transcript_id", "ask_results", ["transcript_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ask_results_transcript_id", table_name="ask_results")
    op.drop_index("ix_ask_results_entry_id", table_name="ask_results")
    op.drop_index("ix_ask_results_created_at", table_name="ask_results")
    op.drop_index("ix_ask_results_ask_query_id", table_name="ask_results")
    op.drop_table("ask_results")

    op.drop_index("ix_ask_queries_user_id", table_name="ask_queries")
    op.drop_index("ix_ask_queries_created_at", table_name="ask_queries")
    op.drop_table("ask_queries")
