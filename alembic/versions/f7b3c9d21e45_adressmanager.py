"""Adressmanager im Kplus-Stil: Adressfelder, Ansprechpartner, Lieferadressen, Wiedervorlage

Revision ID: f7b3c9d21e45
Revises: e5f2a7c93b14
Create Date: 2026-09-13

1. adresse: Adressnummer, Matchcode, Fibunummer, Anrede/Titel/Name 2/Briefanrede,
   Telefon 2/Mobil/Fax/Homepage, Branche, Merkmale — alle TEXT mit server_default.
   Bestehende Adressen bekommen eine Adressnummer (10000 + id) und der Zähler
   ("adresse", Jahr 0) im Nummernkreis wird passend gesetzt.
2. Neue Tabellen ansprechpartner und lieferadresse. Vorhandene Freitext-
   Ansprechpartner werden als Hauptkontakt übernommen.
3. aktivitaet: wiedervorlage_am, erledigt, erledigt_am, beleg_id (ohne FK,
   siehe alembic/env.py zur SQLite-Batch-Falle).
4. beleg: lieferadresse_id (ohne FK, gleiche Begründung).
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b3c9d21e45"
down_revision: Union[str, Sequence[str], None] = "e5f2a7c93b14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADRESSE_TEXTFELDER = [
    "adressnr", "matchcode", "fibunummer", "anrede", "titel", "name2", "briefanrede",
    "telefon2", "mobil", "fax", "homepage", "branche", "merkmale",
]


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("adresse", schema=None) as batch_op:
        for feld in ADRESSE_TEXTFELDER:
            batch_op.add_column(sa.Column(feld, sa.String(), nullable=False, server_default=""))
        batch_op.create_index(batch_op.f("ix_adresse_adressnr"), ["adressnr"], unique=False)
        batch_op.create_index(batch_op.f("ix_adresse_matchcode"), ["matchcode"], unique=False)

    op.create_table(
        "ansprechpartner",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adresse_id", sa.Integer(), nullable=False),
        sa.Column("anrede", sa.String(), nullable=False),
        sa.Column("vorname", sa.String(), nullable=False),
        sa.Column("nachname", sa.String(), nullable=False),
        sa.Column("funktion", sa.String(), nullable=False),
        sa.Column("abteilung", sa.String(), nullable=False),
        sa.Column("telefon", sa.String(), nullable=False),
        sa.Column("mobil", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("notiz", sa.String(), nullable=False),
        sa.Column("ist_hauptkontakt", sa.Boolean(), nullable=False),
        sa.Column("aktiv", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["adresse_id"], ["adresse.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ansprechpartner", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ansprechpartner_adresse_id"), ["adresse_id"], unique=False
        )

    op.create_table(
        "lieferadresse",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adresse_id", sa.Integer(), nullable=False),
        sa.Column("bezeichnung", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("strasse", sa.String(), nullable=False),
        sa.Column("plz", sa.String(), nullable=False),
        sa.Column("ort", sa.String(), nullable=False),
        sa.Column("land", sa.String(), nullable=False),
        sa.Column("notiz", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["adresse_id"], ["adresse.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("lieferadresse", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_lieferadresse_adresse_id"), ["adresse_id"], unique=False
        )

    with op.batch_alter_table("aktivitaet", schema=None) as batch_op:
        batch_op.add_column(sa.Column("wiedervorlage_am", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("erledigt", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("erledigt_am", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("beleg_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_aktivitaet_wiedervorlage_am"), ["wiedervorlage_am"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_aktivitaet_erledigt"), ["erledigt"], unique=False)
        batch_op.create_index(batch_op.f("ix_aktivitaet_beleg_id"), ["beleg_id"], unique=False)

    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lieferadresse_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_beleg_lieferadresse_id"), ["lieferadresse_id"], unique=False
        )

    # --- Daten: Adressnummern vergeben (10000 + id) und Zähler dazu setzen
    op.execute("UPDATE adresse SET adressnr = CAST(10000 + id AS TEXT) WHERE adressnr = ''")
    op.execute(
        "INSERT INTO nummernkreis (belegart, jahr, praefix, letzte_nummer) "
        "SELECT 'adresse', 0, '', COALESCE(MAX(id), 0) FROM adresse "
        "WHERE NOT EXISTS (SELECT 1 FROM nummernkreis WHERE belegart = 'adresse' AND jahr = 0)"
    )
    # --- Daten: Freitext-Ansprechpartner als Hauptkontakt übernehmen
    op.execute(
        "INSERT INTO ansprechpartner (adresse_id, anrede, vorname, nachname, funktion, "
        "abteilung, telefon, mobil, email, notiz, ist_hauptkontakt, aktiv) "
        "SELECT id, '', '', ansprechpartner, '', '', '', '', '', '', 1, 1 "
        "FROM adresse WHERE trim(ansprechpartner) <> ''"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_beleg_lieferadresse_id"))
        batch_op.drop_column("lieferadresse_id")
    with op.batch_alter_table("aktivitaet", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_aktivitaet_beleg_id"))
        batch_op.drop_index(batch_op.f("ix_aktivitaet_erledigt"))
        batch_op.drop_index(batch_op.f("ix_aktivitaet_wiedervorlage_am"))
        batch_op.drop_column("beleg_id")
        batch_op.drop_column("erledigt_am")
        batch_op.drop_column("erledigt")
        batch_op.drop_column("wiedervorlage_am")
    with op.batch_alter_table("lieferadresse", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lieferadresse_adresse_id"))
    op.drop_table("lieferadresse")
    with op.batch_alter_table("ansprechpartner", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ansprechpartner_adresse_id"))
    op.drop_table("ansprechpartner")
    with op.batch_alter_table("adresse", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_adresse_matchcode"))
        batch_op.drop_index(batch_op.f("ix_adresse_adressnr"))
        for feld in reversed(ADRESSE_TEXTFELDER):
            batch_op.drop_column(feld)
    op.execute("DELETE FROM nummernkreis WHERE belegart = 'adresse' AND jahr = 0")
