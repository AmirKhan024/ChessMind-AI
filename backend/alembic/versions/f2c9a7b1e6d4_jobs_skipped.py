"""Count games skipped after repeated failures, so one bad game no longer fails a job.

Revision ID: f2c9a7b1e6d4
Revises: e4b8c1d2f3a5
"""

import sqlalchemy as sa
from alembic import op

revision = "f2c9a7b1e6d4"
down_revision = "e4b8c1d2f3a5"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("jobs", "skipped")
