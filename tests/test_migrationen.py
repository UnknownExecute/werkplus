"""Prüft die Migrationskette gegen die Modelle — die Testsuite tut das sonst NICHT.

Warum es diese Datei gibt: `tests/conftest.py` baut das Schema mit
`SQLModel.metadata.create_all()`. Die Alembic-Migrationen werden dabei nie
ausgeführt. Produktiv passiert genau das Gegenteil — `docker-entrypoint.sh`
ruft `alembic upgrade head` auf. Ein Fehler, der nur in einer Migration steckt,
ist für die restliche Suite deshalb unsichtbar.

Genau so ist ein echter Ausfall entstanden: Migration d3b8f5c21a07 schrieb den
Enum-**Wert** ('bearbeiten') statt des **Namens** ('BEARBEITEN'). SQLAlchemy
speichert Enums als Namen, also warf jedes Laden eines Rollenprofils
``LookupError`` — und weil ``bereich_wache`` bei jedem Request ein Profil lädt,
hätte die komplette App mit HTTP 500 geantwortet. Alle 505 anderen Tests waren
grün.

Diese Tests laufen bewusst gegen eine echte Datei-Datenbank und die echte
Migrationskette.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from app.models import BEREICHE, Rollenprofil, Zugriff

PROJEKT = Path(__file__).resolve().parent.parent


@pytest.fixture
def migrierte_db(tmp_path):
    """Baut eine Datenbank ausschließlich über ``alembic upgrade head``."""
    db = tmp_path / "migriert.db"
    ergebnis = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJEKT,
        env={
            **_basis_env(),
            "WERKPLUS_DATABASE_URL": f"sqlite:///{db.as_posix()}",
        },
        capture_output=True,
        text=True,
    )
    assert ergebnis.returncode == 0, (
        f"alembic upgrade head schlug fehl:\n{ergebnis.stdout}\n{ergebnis.stderr}"
    )
    return db


def _basis_env() -> dict:
    import os

    # PATH/SystemRoot u. a. mitnehmen, sonst startet Python auf Windows nicht.
    return {k: v for k, v in os.environ.items() if k != "WERKPLUS_DATABASE_URL"}


def test_rollenprofil_ist_nach_migration_lesbar(migrierte_db):
    """Der Regressionstest für die Enum-Falle: Profile müssen lesbar sein.

    Vor dem Fix schlug das mit ``LookupError: 'bearbeiten' is not among the
    defined enum values`` fehl.
    """
    engine = create_engine(f"sqlite:///{migrierte_db.as_posix()}")
    with Session(engine) as session:
        profile = session.exec(select(Rollenprofil)).all()
        # Jedes Rechtefeld muss sich in einen gültigen Zugriff auflösen lassen
        for profil in profile:
            for bereich in BEREICHE:
                stufe = getattr(profil, f"recht_{bereich}")
                assert isinstance(stufe, Zugriff), (
                    f"{profil.name}.recht_{bereich} ist {stufe!r}, kein Zugriff-Enum"
                )


def test_enum_spalten_enthalten_namen_keine_werte(migrierte_db):
    """Alle recht_*-Spalten müssen GROSSBUCHSTABEN-Namen enthalten.

    Prüft die rohen Textwerte, weil der ORM-Zugriff bei falschen Werten schon
    vorher scheitert und die Ursache dann schwerer zu erkennen ist.
    """
    erlaubt = {"KEIN", "ANSEHEN", "BEARBEITEN"}
    verbindung = sqlite3.connect(migrierte_db)
    spalten = [f"recht_{bereich}" for bereich in BEREICHE]
    zeilen = verbindung.execute(
        f"SELECT name, {', '.join(spalten)} FROM rollenprofil"
    ).fetchall()
    verbindung.close()
    assert zeilen, "Die Migration sollte Standardrollen anlegen"
    for zeile in zeilen:
        rollenname, *werte = zeile
        for spalte, wert in zip(spalten, werte, strict=True):
            assert wert in erlaubt, (
                f"{rollenname}.{spalte} = {wert!r} — Enums werden als NAME "
                f"gespeichert (KEIN/ANSEHEN/BEARBEITEN), nicht als Wert"
            )


def test_alle_bereiche_haben_eine_spalte(migrierte_db):
    """Jeder Bereich aus BEREICHE braucht seine Spalte — sonst fehlt eine Migration."""
    verbindung = sqlite3.connect(migrierte_db)
    vorhanden = {
        zeile[1] for zeile in verbindung.execute("PRAGMA table_info(rollenprofil)")
    }
    verbindung.close()
    fehlend = {f"recht_{bereich}" for bereich in BEREICHE} - vorhanden
    assert not fehlend, f"Spalten fehlen in der migrierten DB: {sorted(fehlend)}"


def test_neue_tabellen_existieren(migrierte_db):
    """Die Tabellen der Ausbaurunde müssen durch die Migration entstehen."""
    verbindung = sqlite3.connect(migrierte_db)
    tabellen = {
        zeile[0]
        for zeile in verbindung.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    verbindung.close()
    for tabelle in ("zahlung", "zeiterfassung"):
        assert tabelle in tabellen, f"Tabelle {tabelle!r} fehlt nach der Migration"
