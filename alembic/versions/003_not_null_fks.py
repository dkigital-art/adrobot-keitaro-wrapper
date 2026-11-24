"""streams.campaign_id and stream_offers.stream_id NOT NULL (как в моделях)

Revision ID: 003_not_null_fks
Revises: 002_sync_logs
Create Date: 2026-11-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_not_null_fks"
down_revision: Union[str, None] = "002_sync_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("streams") as batch:
        batch.alter_column("campaign_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("stream_offers") as batch:
        batch.alter_column("stream_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("stream_offers") as batch:
        batch.alter_column("stream_id", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("streams") as batch:
        batch.alter_column("campaign_id", existing_type=sa.Integer(), nullable=True)
