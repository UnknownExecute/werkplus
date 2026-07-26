"""recht_zeiterfassung an rollenprofil

Revision ID: d3b8f5c21a07
Revises: c7a1e4b90f21
Create Date: 2026-07-26

Neuer Rechtebereich "zeiterfassung" (siehe app/models/rollenprofil.py BEREICHE).

server_default = 'KEIN': Bestehende Rollen bekommen bewusst KEIN Recht auf die
Arbeitszeiten — Rechte werden nie stillschweigend erweitert. Der Seed setzt sie
fuer admin/sachbearbeiter/monteur gezielt.

!!! ACHTUNG Enum-Falle (hier schon einmal falsch gemacht) !!!
SQLAlchemy/SQLModel speichert Enums als **NAMEN** (KEIN/ANSEHEN/BEARBEITEN),
nicht als Werte (kein/ansehen/bearbeiten). Eine Migration, die Kleinbuchstaben
schreibt, laesst spaeter JEDEN Request scheitern:
``LookupError: 'bearbeiten' is not among the defined enum values`` — und weil
``bereich_wache`` bei jedem Request ``lade_profil`` aufruft, antwortet die
gesamte App mit 500. Deshalb Spaltentyp und Werte exakt wie in der
Ursprungsmigration 4d89de911e3f: sa.Enum('KEIN','ANSEHEN','BEARBEITEN',
name='zugriff') und GROSSBUCHSTABEN in allen server_default/UPDATE-Werten.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3b8f5c21a07"
down_revision: Union[str, Sequence[str], None] = "c7a1e4b90f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    zugriff = sa.Enum("KEIN", "ANSEHEN", "BEARBEITEN", name="zugriff")
    with op.batch_alter_table("rollenprofil", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("recht_zeiterfassung", zugriff, nullable=False, server_default="KEIN")
        )
    # Admins haben ohnehin Vollzugriff (ist_admin), aber der Vollstaendigkeit
    # halber auch das Feld setzen — sonst zeigt die Rollenmaske "kein Zugriff".
    op.execute(
        "UPDATE rollenprofil SET recht_zeiterfassung = 'BEARBEITEN' WHERE ist_admin = 1"
    )
    # Selbstheilung: Falls eine Datenbank eine fruehere, fehlerhafte Fassung
    # dieser Migration mit Kleinbuchstaben erhalten hat, hier normalisieren.
    op.execute(
        "UPDATE rollenprofil SET recht_zeiterfassung = upper(recht_zeiterfassung) "
        "WHERE recht_zeiterfassung <> upper(recht_zeiterfassung)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("rollenprofil", schema=None) as batch_op:
        batch_op.drop_column("recht_zeiterfassung")
