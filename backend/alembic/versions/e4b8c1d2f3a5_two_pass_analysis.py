"""Record the first-pass node limit of two-pass analysis per game.

Revision ID: e4b8c1d2f3a5
Revises: d1e7a3c5b902
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b8c1d2f3a5"
down_revision = "d1e7a3c5b902"


def upgrade() -> None:
    op.add_column("games", sa.Column("quick_nodes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "quick_nodes")
