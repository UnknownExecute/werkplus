"""Favoriten (persönlicher Schnellzugriff, Kplus-Desktop)

Revision ID: d2f7a4b36c85
Revises: c1e6f3a29b74
Create Date: 2026-09-13
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f7a4b36c85"
down_revision: Union[str, Sequence[str], None] = "c1e6f3a29b74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "favorit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("benutzer_id", sa.Integer(), nullable=False),
        sa.Column("titel", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["benutzer_id"], ["benutzer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("favorit", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_favorit_benutzer_id"), ["benutzer_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("favorit", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_favorit_benutzer_id"))
    op.drop_table("favorit")
