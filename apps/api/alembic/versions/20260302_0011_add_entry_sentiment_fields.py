"""add entry sentiment fields

Revision ID: 20260302_0011
Revises: 20260302_0010
Create Date: 2026-03-02 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_0011"
down_revision = "20260302_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entries", sa.Column("sentiment_label", sa.String(length=32), nullable=True))
    op.add_column("entries", sa.Column("sentiment_score", sa.Numeric(precision=4, scale=3), nullable=True))
    op.create_check_constraint(
        "ck_entries_sentiment_score_range",
        "entries",
        "(sentiment_score IS NULL) OR (sentiment_score >= 0 AND sentiment_score <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_entries_sentiment_score_range", "entries", type_="check")
    op.drop_column("entries", "sentiment_score")
    op.drop_column("entries", "sentiment_label")
