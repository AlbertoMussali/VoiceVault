"""add ask summary fields

Revision ID: 20260302_0009
Revises: 20260302_0008
Create Date: 2026-03-02 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260302_0009"
down_revision = "20260302_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ask_queries",
        sa.Column("summary_status", sa.String(length=32), nullable=False, server_default=sa.text("'not_requested'")),
    )
    op.add_column("ask_queries", sa.Column("summary_json", sa.JSON(), nullable=True))
    op.add_column("ask_queries", sa.Column("summary_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ask_queries", "summary_error")
    op.drop_column("ask_queries", "summary_json")
    op.drop_column("ask_queries", "summary_status")
