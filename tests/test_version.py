"""Versionsanzeige und Cache-Busting der statischen Dateien.

Anlass aus dem Betrieb: Nach einem Update hatte der Browser die alte
``werkplus.js`` im Cache, während der Server bereits CSRF erzwang. Das alte
Skript sendet keinen Token → jeder POST scheiterte mit „CSRF-Token fehlt".
Ein harter Reload (Ctrl+F5) half, aber darauf darf sich niemand verlassen
müssen. Deshalb hängt an allen CSS-/JS-URLs ein ``?v=…``, das sich bei jedem
Ausrollen ändert.

Zweiter Anlass: Der Anwender wollte die Version in der Oberfläche kontrollieren
können, um zu sehen, ob ein Update angekommen ist.
"""

import re

from sqlmodel import Session

from app.models import Benutzer, Rolle
from app.services.auth_service import hash_passwort
from app.version import VERSION, asset_version, code_stand


def _admin_anmelden(engine, client) -> None:
    with Session(engine) as session:
        session.add(
            Benutzer(
                login="chef",
                name="Chefin",
                passwort_hash=hash_passwort("geheim"),
                rolle=Rolle.ADMIN,
                aktiv=True,
            )
        )
        session.commit()
    r = client.post(
        "/login", data={"login": "chef", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


# --- Cache-Busting ----------------------------------------------------------


def test_alle_assets_tragen_versionsparameter(engine, client):
    """Kein CSS/JS ohne ?v= — sonst kann altes JS mit neuem Server laufen."""
    _admin_anmelden(engine, client)
    html = client.get("/").text
    verweise = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert verweise, "Es wurden keine /static-Verweise gefunden"
    ohne_version = [v for v in verweise if "?v=" not in v]
    assert not ohne_version, f"Ohne Cache-Buster: {ohne_version}"


def test_versionsparameter_ist_ueberall_gleich(engine, client):
    """Ein Wert je Seite — sonst lädt der Browser unnötig mehrfach."""
    _admin_anmelden(engine, client)
    html = client.get("/").text
    werte = set(re.findall(r'/static/[^"?]+\?v=([^"]+)', html))
    assert len(werte) == 1, f"Unterschiedliche Cache-Buster: {werte}"


def test_asset_version_enthaelt_version_und_stand():
    """Die Kennung muss sich bei neuem Code ändern (Stand ist Teil davon)."""
    kennung = asset_version()
    assert kennung.startswith(VERSION)
    assert str(int(code_stand().timestamp())) in kennung


def test_statische_datei_mit_versionsparameter_wird_ausgeliefert(engine, client):
    """Der Parameter darf die Auslieferung nicht stören."""
    antwort = client.get(f"/static/werkplus.js?v={asset_version()}")
    assert antwort.status_code == 200
    assert "csrfToken" in antwort.text, "Das CSRF-Skript fehlt in werkplus.js"


# --- Versionsanzeige in den Einstellungen -----------------------------------


def test_einstellungen_zeigen_version_und_stand(engine, client):
    _admin_anmelden(engine, client)
    html = client.get("/einstellungen").text
    assert "Programmversion" in html
    assert VERSION in html
    # Der Stand als Datum (dd.mm.yyyy) muss auftauchen
    assert code_stand().strftime("%d.%m.%Y") in html


def test_einstellungen_zeigen_schema_revision(engine, client):
    """Die Alembic-Revision hilft zu prüfen, ob Migrationen durchliefen."""
    _admin_anmelden(engine, client)
    html = client.get("/einstellungen").text
    assert "Schema" in html
    # In der Testdatenbank (create_all) gibt es keine alembic_version-Tabelle —
    # die Anzeige muss das aushalten und darf die Seite nicht kippen.
    assert "unbekannt" in html or re.search(r"Schema [0-9a-f]{8,}", html)


def test_schema_revision_wird_als_klartext_gelesen(engine):
    """Kein Tuple-Abdruck wie „('d3b8f5c21a07',)" — das stand erst so in der UI.

    SQLAlchemy liefert ein Row-Objekt; ein ``isinstance(..., tuple)`` greift dort
    nicht, weshalb ``str()`` die Klammerform erzeugte.
    """
    from sqlalchemy import text
    from sqlmodel import Session

    from app.routers.einstellungen import _db_revision

    with Session(engine) as session:
        session.exec(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        session.exec(text("INSERT INTO alembic_version VALUES ('abc123def456')"))
        session.commit()
        assert _db_revision(session) == "abc123def456"


def test_schema_revision_ohne_tabelle_bleibt_leer(engine):
    """Ohne alembic_version darf die Anzeige nicht scheitern (create_all-Tests)."""
    from sqlmodel import Session

    from app.routers.einstellungen import _db_revision

    with Session(engine) as session:
        assert _db_revision(session) == ""


def test_einstellungen_nur_fuer_admin(engine, client):
    """Gegenprobe: ohne Anmeldung keine Versionsauskunft."""
    antwort = client.get("/einstellungen", follow_redirects=False)
    assert antwort.status_code in (302, 403)
