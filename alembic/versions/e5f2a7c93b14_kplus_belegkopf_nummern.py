"""Kplus-Belegkopf: Betreff/Termin/Auftragsart/Angebotsergebnis + Nummern je Belegart

Revision ID: e5f2a7c93b14
Revises: d3b8f5c21a07
Create Date: 2026-09-12

1. Neue Kopffelder am Beleg (Belegübersicht im Kplus-Stil):
   betreff, termin, auftragsart, angebotsergebnis — alle mit server_default,
   damit bestehende Zeilen gültig bleiben (NOT-NULL-Regel dieses Projekts).
   Bewusst reine TEXT-Spalten, keine sa.Enum: siehe Enum-Falle in d3b8f5c21a07.

2. Nummernlogik: Belegnummern sind ab jetzt nur noch **je Belegart** eindeutig
   (Format JJNNNN, z. B. 260001 — Angebot und Rechnung dürfen dieselbe Nummer
   tragen). Deshalb wird der globale Unique-Index auf belegnr durch einen
   normalen Index plus einen zusammengesetzten Unique-Index (belegart, belegnr)
   ersetzt. Alte Nummern (RE-2026-0001) bleiben unverändert gültig.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f2a7c93b14"
down_revision: Union[str, Sequence[str], None] = "d3b8f5c21a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("betreff", sa.String(), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("termin", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("auftragsart", sa.String(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("angebotsergebnis", sa.String(), nullable=False, server_default="")
        )
        batch_op.create_index(batch_op.f("ix_beleg_betreff"), ["betreff"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_beleg_auftragsart"), ["auftragsart"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_beleg_angebotsergebnis"), ["angebotsergebnis"], unique=False
        )
        # Eindeutigkeit je Belegart statt global
        batch_op.drop_index(batch_op.f("ix_beleg_belegnr"))
        batch_op.create_index(batch_op.f("ix_beleg_belegnr"), ["belegnr"], unique=False)
        batch_op.create_index(
            "ix_beleg_belegart_belegnr", ["belegart", "belegnr"], unique=True
        )
    # Altbestand: erledigte Angebote, aus denen bereits ein Auftrag entstand,
    # gelten als „Auftrag erhalten" (Enum-Werte sind NAMEN, siehe d3b8f5c21a07).
    op.execute(
        "UPDATE beleg SET angebotsergebnis = 'erhalten' "
        "WHERE belegart = 'ANGEBOT' AND status = 'ERLEDIGT' AND angebotsergebnis = '' "
        "AND id IN (SELECT vorgaenger_beleg_id FROM beleg WHERE vorgaenger_beleg_id IS NOT NULL)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.drop_index("ix_beleg_belegart_belegnr")
        batch_op.drop_index(batch_op.f("ix_beleg_belegnr"))
        batch_op.create_index(batch_op.f("ix_beleg_belegnr"), ["belegnr"], unique=True)
        batch_op.drop_index(batch_op.f("ix_beleg_angebotsergebnis"))
        batch_op.drop_index(batch_op.f("ix_beleg_auftragsart"))
        batch_op.drop_index(batch_op.f("ix_beleg_betreff"))
        batch_op.drop_column("angebotsergebnis")
        batch_op.drop_column("auftragsart")
        batch_op.drop_column("termin")
        batch_op.drop_column("betreff")
