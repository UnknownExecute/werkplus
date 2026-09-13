"""Artikelstamm im Kplus-Stil: Matchcode/EAN/…, Lieferanten je Artikel, Staffelpreise

Revision ID: b9d5e2f18a03
Revises: a8c4d1e07f92
Create Date: 2026-09-13

artikel: matchcode, ean, alternativnr, hersteller, bemerkung, unterwarengruppe
(TEXT, server_default ''), mindest_vk / letzter_ek (Decimal als TEXT, '0'),
letzter_lieferant_id (ohne FK), letzter_ek_am, letzte_preisaenderung (Date).
Neue Tabellen artikel_lieferant und artikel_staffel.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d5e2f18a03"
down_revision: Union[str, Sequence[str], None] = "a8c4d1e07f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEXTFELDER = ["matchcode", "ean", "alternativnr", "hersteller", "bemerkung", "unterwarengruppe"]


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("artikel", schema=None) as batch_op:
        for feld in TEXTFELDER:
            batch_op.add_column(sa.Column(feld, sa.String(), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("mindest_vk", sa.String(length=40), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("letzter_ek", sa.String(length=40), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("letzter_lieferant_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("letzter_ek_am", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("letzte_preisaenderung", sa.Date(), nullable=True))
        batch_op.create_index(batch_op.f("ix_artikel_matchcode"), ["matchcode"], unique=False)
        batch_op.create_index(batch_op.f("ix_artikel_ean"), ["ean"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_artikel_letzter_lieferant_id"), ["letzter_lieferant_id"], unique=False
        )

    op.create_table(
        "artikel_lieferant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artikel_id", sa.Integer(), nullable=False),
        sa.Column("adresse_id", sa.Integer(), nullable=False),
        sa.Column("lieferanten_artikelnr", sa.String(), nullable=False),
        sa.Column("ek_preis", sa.String(length=40), nullable=False),
        sa.Column("lieferzeit_tage", sa.Integer(), nullable=False),
        sa.Column("ist_hauptlieferant", sa.Boolean(), nullable=False),
        sa.Column("notiz", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["adresse_id"], ["adresse.id"]),
        sa.ForeignKeyConstraint(["artikel_id"], ["artikel.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artikel_id", "adresse_id", name="uq_artikel_lieferant"),
    )
    with op.batch_alter_table("artikel_lieferant", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artikel_lieferant_adresse_id"), ["adresse_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_artikel_lieferant_artikel_id"), ["artikel_id"], unique=False
        )

    op.create_table(
        "artikel_staffel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artikel_id", sa.Integer(), nullable=False),
        sa.Column("ab_menge", sa.String(length=40), nullable=False),
        sa.Column("vk_preis", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["artikel_id"], ["artikel.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artikel_id", "ab_menge", name="uq_artikel_staffel"),
    )
    with op.batch_alter_table("artikel_staffel", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artikel_staffel_artikel_id"), ["artikel_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("artikel_staffel", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artikel_staffel_artikel_id"))
    op.drop_table("artikel_staffel")
    with op.batch_alter_table("artikel_lieferant", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artikel_lieferant_artikel_id"))
        batch_op.drop_index(batch_op.f("ix_artikel_lieferant_adresse_id"))
    op.drop_table("artikel_lieferant")
    with op.batch_alter_table("artikel", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artikel_letzter_lieferant_id"))
        batch_op.drop_index(batch_op.f("ix_artikel_ean"))
        batch_op.drop_index(batch_op.f("ix_artikel_matchcode"))
        for feld in ("letzte_preisaenderung", "letzter_ek_am", "letzter_lieferant_id",
                     "letzter_ek", "mindest_vk", *reversed(TEXTFELDER)):
            batch_op.drop_column(feld)
