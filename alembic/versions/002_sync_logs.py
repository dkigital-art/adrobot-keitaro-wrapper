"""add sync_logs

Revision ID: 002_sync_logs
Revises: 001_initial
Create Date: 2026-03-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_sync_logs"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sync_logs" not in inspector.get_table_names():
        op.create_table(
            "sync_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "campaign_id",
                sa.Integer(),
                sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "stream_id",
                sa.Integer(),
                sa.ForeignKey("streams.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_sync_logs_campaign_id", "sync_logs", ["campaign_id"])
        op.create_index("ix_sync_logs_stream_id", "sync_logs", ["stream_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_logs_stream_id", table_name="sync_logs")
    op.drop_index("ix_sync_logs_campaign_id", table_name="sync_logs")
    op.drop_table("sync_logs")
