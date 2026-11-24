"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-03-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("keitaro_campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("geo", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaigns_keitaro_campaign_id", "campaigns", ["keitaro_campaign_id"], unique=True)

    op.create_table(
        "streams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE")),
        sa.Column("keitaro_stream_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("schema", sa.String(length=64), nullable=False),
        sa.Column("has_offers", sa.Boolean(), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("published_snapshot", sa.JSON(), nullable=True),
    )
    op.create_index("ix_streams_keitaro_stream_id", "streams", ["keitaro_stream_id"], unique=True)

    op.create_table(
        "stream_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id", ondelete="CASCADE")),
        sa.Column("keitaro_offer_id", sa.Integer(), nullable=False),
        sa.Column("offer_name", sa.String(length=512), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("stream_id", "keitaro_offer_id", name="uq_stream_offer"),
    )
    op.create_index("ix_stream_offers_keitaro_offer_id", "stream_offers", ["keitaro_offer_id"])


def downgrade() -> None:
    op.drop_table("stream_offers")
    op.drop_table("streams")
    op.drop_table("campaigns")
