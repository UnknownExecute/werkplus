"""zahlung, mahnwesen, zeiterfassung, wartungsvertrag, mindestbestand

Revision ID: c7a1e4b90f21
Revises: 96926869d9a0
Create Date: 2026-07-26

Fundament fuer die fachliche Ausbaurunde. Bewusst HANDGESCHRIEBEN (nicht
autogenerate): Alembic erzeugt fuer den Self-FK beleg.auftrag_id -> beleg.id
immer wieder create_foreign_key-Zeilen, die im SQLite-Batch-Modus scheitern.

Alle Decimal-Spalten werden als TEXT (sa.String(40)) gefuehrt — siehe
app/models/types.py (DecimalStr). Neue NOT-NULL-Spalten brauchen ein
server_default, damit bestehende Zeilen migrierbar bleiben.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a1e4b90f21"
down_revision: Union[str, Sequence[str], None] = "96926869d9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Decimal wird als TEXT gespeichert (DecimalStr)
DEZIMAL = sa.String(length=40)


def upgrade() -> None:
    """Upgrade schema."""
    # --- Zahlungen (Teilzahlungen + Skonto) ---------------------------------
    op.create_table(
        "zahlung",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("beleg_id", sa.Integer(), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("betrag", DEZIMAL, nullable=False),
        sa.Column("art", sa.String(), nullable=False),
        sa.Column("skonto_betrag", DEZIMAL, nullable=False),
        sa.Column("notiz", sa.String(), nullable=False),
        sa.Column("erstellt_von", sa.Integer(), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["beleg_id"], ["beleg.id"]),
        sa.ForeignKeyConstraint(["erstellt_von"], ["benutzer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("zahlung", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_zahlung_beleg_id"), ["beleg_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_zahlung_datum"), ["datum"], unique=False)
        batch_op.create_index(batch_op.f("ix_zahlung_art"), ["art"], unique=False)

    # --- Arbeitszeiterfassung ------------------------------------------------
    op.create_table(
        "zeiterfassung",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("beleg_id", sa.Integer(), nullable=True),
        sa.Column("benutzer_id", sa.Integer(), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("stunden", DEZIMAL, nullable=False),
        sa.Column("stundensatz", DEZIMAL, nullable=False),
        sa.Column("taetigkeit", sa.String(), nullable=False),
        sa.Column("verrechnet", sa.Boolean(), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["beleg_id"], ["beleg.id"]),
        sa.ForeignKeyConstraint(["benutzer_id"], ["benutzer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("zeiterfassung", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_zeiterfassung_beleg_id"), ["beleg_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_zeiterfassung_benutzer_id"), ["benutzer_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_zeiterfassung_datum"), ["datum"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_zeiterfassung_verrechnet"), ["verrechnet"], unique=False
        )

    # --- Beleg: Skonto + Mahnwesen ------------------------------------------
    # Hinweis: KEIN create_foreign_key hier — der Self-FK auftrag_id existiert
    # bewusst nur als Spalte + Index (siehe Modul-Docstring).
    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("skonto_prozent", DEZIMAL, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("skonto_tage", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("mahnstufe", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("letzte_mahnung_am", sa.Date(), nullable=True))
        batch_op.create_index(batch_op.f("ix_beleg_mahnstufe"), ["mahnstufe"], unique=False)

    # --- Adresse: Skonto-Vereinbarung ---------------------------------------
    with op.batch_alter_table("adresse", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("skonto_prozent", DEZIMAL, nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("skonto_tage", sa.Integer(), nullable=False, server_default="0")
        )

    # --- Benutzer: Stundensatz ----------------------------------------------
    with op.batch_alter_table("benutzer", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stundensatz", DEZIMAL, nullable=False, server_default="0"))

    # --- Anlage: Wartungsvertrag --------------------------------------------
    with op.batch_alter_table("anlage", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "wartungsintervall_monate", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(sa.Column("vertrag_bis", sa.Date(), nullable=True))

    # --- Artikel: Mindestbestand -------------------------------------------
    with op.batch_alter_table("artikel", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("mindestbestand", DEZIMAL, nullable=False, server_default="0")
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("artikel", schema=None) as batch_op:
        batch_op.drop_column("mindestbestand")

    with op.batch_alter_table("anlage", schema=None) as batch_op:
        batch_op.drop_column("vertrag_bis")
        batch_op.drop_column("wartungsintervall_monate")

    with op.batch_alter_table("benutzer", schema=None) as batch_op:
        batch_op.drop_column("stundensatz")

    with op.batch_alter_table("adresse", schema=None) as batch_op:
        batch_op.drop_column("skonto_tage")
        batch_op.drop_column("skonto_prozent")

    with op.batch_alter_table("beleg", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_beleg_mahnstufe"))
        batch_op.drop_column("letzte_mahnung_am")
        batch_op.drop_column("mahnstufe")
        batch_op.drop_column("skonto_tage")
        batch_op.drop_column("skonto_prozent")

    with op.batch_alter_table("zeiterfassung", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_zeiterfassung_verrechnet"))
        batch_op.drop_index(batch_op.f("ix_zeiterfassung_datum"))
        batch_op.drop_index(batch_op.f("ix_zeiterfassung_benutzer_id"))
        batch_op.drop_index(batch_op.f("ix_zeiterfassung_beleg_id"))
    op.drop_table("zeiterfassung")

    with op.batch_alter_table("zahlung", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_zahlung_art"))
        batch_op.drop_index(batch_op.f("ix_zahlung_datum"))
        batch_op.drop_index(batch_op.f("ix_zahlung_beleg_id"))
    op.drop_table("zahlung")
