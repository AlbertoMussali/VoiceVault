"""add transcript full text search column and gin index

Revision ID: 20260302_0005
Revises: 20260302_0004
Create Date: 2026-03-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260302_0005"
down_revision = "20260302_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.execute(
        """
        UPDATE transcripts
        SET search_vector = to_tsvector('english', COALESCE(transcript_text, ''))
        """
    )
    op.execute(
        """
        CREATE FUNCTION transcripts_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', COALESCE(NEW.transcript_text, ''));
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER transcripts_search_vector_trigger
        BEFORE INSERT OR UPDATE OF transcript_text
        ON transcripts
        FOR EACH ROW
        EXECUTE FUNCTION transcripts_search_vector_update()
        """
    )
    op.alter_column("transcripts", "search_vector", nullable=False)
    op.create_index(
        "ix_transcripts_search_vector_gin",
        "transcripts",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_transcripts_search_vector_gin", table_name="transcripts")
    op.execute("DROP TRIGGER IF EXISTS transcripts_search_vector_trigger ON transcripts")
    op.execute("DROP FUNCTION IF EXISTS transcripts_search_vector_update()")
    op.drop_column("transcripts", "search_vector")
