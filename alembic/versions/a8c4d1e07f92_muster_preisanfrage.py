"""Beleg-Muster (ist_muster) — Preisanfrage braucht kein Schema

Revision ID: a8c4d1e07f92
Revises: f7b3c9d21e45
Create Date: 2026-09-13

beleg.ist_muster: Kplus „Muster" = Vorlage-Beleg, nie gebucht, eigener
Nummernkreis (M0001…). server_default 0, damit alle Bestandsbelege normale
Belege bleiben.

Die neue Belegart PREISANFRAGE braucht keine Schemaänderung: die Spalte
beleg.belegart ist in SQLite ein VARCHAR ohne CHECK-Constraint (SQLAlchemy
sa.Enum legt seit 1.4 keinen an), die Enum-Namen werden als Text gespeichert.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c4d1e07f92"
down_revision: Union[str, Sequence[str], None] = "f7b3c9d21e45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ist_muster", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.create_index(batch_op.f("ix_beleg_ist_muster"), ["ist_muster"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_beleg_ist_muster"))
        batch_op.drop_column("ist_muster")
