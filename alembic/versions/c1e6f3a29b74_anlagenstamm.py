"""Anlagenstamm im Kplus-Stil: freie Stammdaten, Komponenten, Wartungsvertrag, Auftrag aus Anlage

Revision ID: c1e6f3a29b74
Revises: b9d5e2f18a03
Create Date: 2026-09-13

anlage: artikel_id/seriennummer_id werden optional (freie Altanlagen), neue
Felder anlagenr, bezeichnung, kostenstelle, hersteller, typ, seriennummer_text,
ansprechpartner_id, lieferant_id, herstellungsdatum, technische_info, art,
anlagengruppe, untergruppe, vertrag_nr/von/pauschale/notiz. Backfill:
Anlagenummer 1000+id, Bezeichnung/Hersteller aus dem Artikel, SN-Text aus der
Seriennummer; Zähler "anlage" (Jahr 0) im Nummernkreis.
Neue Tabelle anlage_komponente. beleg.anlage_id (ohne FK, s. alembic/env.py).
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e6f3a29b74"
down_revision: Union[str, Sequence[str], None] = "b9d5e2f18a03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEXTFELDER = [
    "anlagenr", "bezeichnung", "kostenstelle", "hersteller", "typ", "seriennummer_text",
    "technische_info", "art", "anlagengruppe", "untergruppe", "vertrag_nr", "vertrag_notiz",
]


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("anlage", schema=None) as batch_op:
        batch_op.alter_column("artikel_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("seriennummer_id", existing_type=sa.Integer(), nullable=True)
        for feld in TEXTFELDER:
            batch_op.add_column(sa.Column(feld, sa.String(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("ansprechpartner_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("lieferant_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("herstellungsdatum", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("vertrag_von", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("vertrag_pauschale", sa.String(length=40), nullable=False, server_default="0")
        )
        for feld in ("anlagenr", "bezeichnung", "hersteller", "seriennummer_text", "art",
                     "anlagengruppe", "ansprechpartner_id", "lieferant_id"):
            batch_op.create_index(batch_op.f(f"ix_anlage_{feld}"), [feld], unique=False)

    op.create_table(
        "anlage_komponente",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("anlage_id", sa.Integer(), nullable=False),
        sa.Column("bezeichnung", sa.String(), nullable=False),
        sa.Column("artikel_id", sa.Integer(), nullable=True),
        sa.Column("hersteller", sa.String(), nullable=False),
        sa.Column("typ", sa.String(), nullable=False),
        sa.Column("seriennummer_text", sa.String(), nullable=False),
        sa.Column("einbaudatum", sa.Date(), nullable=True),
        sa.Column("notiz", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["anlage_id"], ["anlage.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("anlage_komponente", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_anlage_komponente_anlage_id"), ["anlage_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_anlage_komponente_artikel_id"), ["artikel_id"], unique=False
        )

    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.add_column(sa.Column("anlage_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_beleg_anlage_id"), ["anlage_id"], unique=False)

    # --- Daten: Bestandsanlagen mit Nummer, Bezeichnung, Hersteller, SN-Text versorgen
    op.execute("UPDATE anlage SET anlagenr = CAST(1000 + id AS TEXT) WHERE anlagenr = ''")
    op.execute(
        "UPDATE anlage SET bezeichnung = COALESCE("
        "(SELECT bezeichnung FROM artikel WHERE artikel.id = anlage.artikel_id), '') "
        "WHERE bezeichnung = '' AND artikel_id IS NOT NULL"
    )
    op.execute(
        "UPDATE anlage SET hersteller = COALESCE("
        "(SELECT hersteller FROM artikel WHERE artikel.id = anlage.artikel_id), '') "
        "WHERE hersteller = '' AND artikel_id IS NOT NULL"
    )
    op.execute(
        "UPDATE anlage SET seriennummer_text = COALESCE("
        "(SELECT sn FROM seriennummer WHERE seriennummer.id = anlage.seriennummer_id), '') "
        "WHERE seriennummer_text = '' AND seriennummer_id IS NOT NULL"
    )
    op.execute(
        "INSERT INTO nummernkreis (belegart, jahr, praefix, letzte_nummer) "
        "SELECT 'anlage', 0, '', COALESCE(MAX(id), 0) FROM anlage "
        "WHERE NOT EXISTS (SELECT 1 FROM nummernkreis WHERE belegart = 'anlage' AND jahr = 0)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_beleg_anlage_id"))
        batch_op.drop_column("anlage_id")
    with op.batch_alter_table("anlage_komponente", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_anlage_komponente_artikel_id"))
        batch_op.drop_index(batch_op.f("ix_anlage_komponente_anlage_id"))
    op.drop_table("anlage_komponente")
    with op.batch_alter_table("anlage", schema=None) as batch_op:
        for feld in ("anlagenr", "bezeichnung", "hersteller", "seriennummer_text", "art",
                     "anlagengruppe", "ansprechpartner_id", "lieferant_id"):
            batch_op.drop_index(batch_op.f(f"ix_anlage_{feld}"))
        for feld in ("vertrag_pauschale", "vertrag_von", "herstellungsdatum", "lieferant_id",
                     "ansprechpartner_id", *reversed(TEXTFELDER)):
            batch_op.drop_column(feld)
        batch_op.alter_column("seriennummer_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("artikel_id", existing_type=sa.Integer(), nullable=False)
    op.execute("DELETE FROM nummernkreis WHERE belegart = 'anlage' AND jahr = 0")
