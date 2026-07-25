"""Tests für die Mitarbeiter- & Rollenverwaltung (UI + Berechtigung)."""

from sqlmodel import Session, select

from app.main import app as fastapi_app
from app.models import Benutzer, Rolle, Rollenprofil, Zugriff
from app.routers import benutzer as benutzer_router
from app.services.auth_service import hash_passwort, verify_passwort

# Router idempotent registrieren, falls app.main ihn (noch) nicht eingebunden hat.
if not any(getattr(r, "path", "").startswith("/benutzer") for r in fastapi_app.routes):
    fastapi_app.include_router(benutzer_router.router)


def _login(engine, client, rolle=Rolle.ADMIN, login="admin", passwort="geheim"):
    """Legt einen Benutzer mit Rolle an und meldet den Client an. Gibt die id zurück."""
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login,
            name="Tester",
            passwort_hash=hash_passwort(passwort),
            rolle=rolle,
            aktiv=True,
        )
        session.add(benutzer)
        session.commit()
        session.refresh(benutzer)
        benutzer_id = benutzer.id
    r = client.post(
        "/login", data={"login": login, "passwort": passwort}, follow_redirects=False
    )
    assert r.status_code == 302
    return benutzer_id


def _rolle_anlegen(engine, name, **felder):
    with Session(engine) as session:
        rolle = Rollenprofil(name=name, **felder)
        session.add(rolle)
        session.commit()
        session.refresh(rolle)
        return rolle.id


# --- Zugriffsschutz -----------------------------------------------------------


def test_liste_ohne_login_redirect(client):
    r = client.get("/benutzer", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_rollen_ohne_login_redirect(client):
    r = client.get("/benutzer/rollen", follow_redirects=False)
    assert r.status_code == 302


def test_nicht_admin_403(engine, client):
    _login(engine, client, rolle=Rolle.SACHBEARBEITER, login="sb")
    assert client.get("/benutzer", follow_redirects=False).status_code == 403
    assert client.get("/benutzer/rollen", follow_redirects=False).status_code == 403


def test_admin_liste_200(engine, client):
    _login(engine, client)
    r = client.get("/benutzer")
    assert r.status_code == 200
    assert "Mitarbeiter" in r.text


# --- Mitarbeiter --------------------------------------------------------------


def test_mitarbeiter_anlegen(engine, client):
    _login(engine, client)
    r = client.post(
        "/benutzer/neu",
        data={"login": "monteur1", "name": "Max Muster", "rolle": "sachbearbeiter",
              "passwort": "start123"},
        follow_redirects=False,
    )
    assert r.status_code == 302

    with Session(engine) as session:
        b = session.exec(select(Benutzer).where(Benutzer.login == "monteur1")).one()
        assert b.name == "Max Muster"
        assert b.passwort_hash and b.passwort_hash != "start123"
        assert verify_passwort(b.passwort_hash, "start123")

    # In der Liste sichtbar
    assert "Max Muster" in client.get("/benutzer").text

    # Login des neuen Users möglich
    client.post("/logout")
    r = client.post(
        "/login", data={"login": "monteur1", "passwort": "start123"},
        follow_redirects=False,
    )
    assert r.status_code == 302


def test_doppelter_login_kein_500(engine, client):
    _login(engine, client)
    daten = {"login": "dopp", "name": "Erst", "rolle": "sachbearbeiter", "passwort": "x"}
    assert client.post("/benutzer/neu", data=daten, follow_redirects=False).status_code == 302
    r = client.post("/benutzer/neu", data=daten, follow_redirects=False)
    assert r.status_code == 200
    assert "bereits vergeben" in r.text
    with Session(engine) as session:
        anzahl = len(session.exec(select(Benutzer).where(Benutzer.login == "dopp")).all())
        assert anzahl == 1


def test_mitarbeiter_bearbeiten(engine, client):
    _login(engine, client)
    client.post(
        "/benutzer/neu",
        data={"login": "bearb", "name": "Alt", "rolle": "sachbearbeiter", "passwort": "p"},
    )
    with Session(engine) as session:
        bid = session.exec(select(Benutzer).where(Benutzer.login == "bearb")).one().id

    # Name/Rolle ändern und deaktivieren (aktiv nicht mitschicken)
    r = client.post(
        f"/benutzer/{bid}",
        data={"name": "Neu", "rolle": "admin"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        b = session.get(Benutzer, bid)
        assert b.name == "Neu"
        assert b.rolle == "admin"
        assert b.aktiv is False


def test_passwort_aendern(engine, client):
    _login(engine, client)
    client.post(
        "/benutzer/neu",
        data={"login": "pw", "name": "PW", "rolle": "sachbearbeiter", "passwort": "alt123"},
    )
    with Session(engine) as session:
        b = session.exec(select(Benutzer).where(Benutzer.login == "pw")).one()
        bid, alter_hash = b.id, b.passwort_hash

    client.post(f"/benutzer/{bid}", data={"name": "PW", "rolle": "sachbearbeiter",
                                          "passwort": "neu456", "aktiv": "true"})
    with Session(engine) as session:
        b = session.get(Benutzer, bid)
        assert b.passwort_hash != alter_hash
        assert verify_passwort(b.passwort_hash, "neu456")


def test_leeres_passwort_bleibt_erhalten(engine, client):
    _login(engine, client)
    client.post(
        "/benutzer/neu",
        data={"login": "keep", "name": "Keep", "rolle": "sachbearbeiter", "passwort": "bleibt"},
    )
    with Session(engine) as session:
        b = session.exec(select(Benutzer).where(Benutzer.login == "keep")).one()
        bid, alter_hash = b.id, b.passwort_hash

    client.post(f"/benutzer/{bid}", data={"name": "Keep2", "rolle": "sachbearbeiter",
                                          "aktiv": "true"})
    with Session(engine) as session:
        assert session.get(Benutzer, bid).passwort_hash == alter_hash


def test_selbst_nicht_deaktivierbar(engine, client):
    eigene_id = _login(engine, client)
    # aktiv nicht mitschicken → Versuch der Selbst-Deaktivierung
    client.post(f"/benutzer/{eigene_id}", data={"name": "Tester", "rolle": "admin"})
    with Session(engine) as session:
        assert session.get(Benutzer, eigene_id).aktiv is True


# --- Rollen -------------------------------------------------------------------


def test_rolle_anlegen_mit_bereichsrechten(engine, client):
    _login(engine, client)
    r = client.post(
        "/benutzer/rollen/neu",
        data={
            "name": "verkauf",
            "beschreibung": "Nur Verkauf",
            "darf_kalkulation_sehen": "true",
            "recht_verkauf": "bearbeiten",
            "recht_adressen": "ansehen",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        rolle = session.exec(select(Rollenprofil).where(Rollenprofil.name == "verkauf")).one()
        assert rolle.recht_verkauf == Zugriff.BEARBEITEN
        assert rolle.recht_adressen == Zugriff.ANSEHEN
        assert rolle.recht_lager == Zugriff.KEIN
        assert rolle.ist_admin is False
        assert rolle.darf_kalkulation_sehen is True


def test_rolle_doppelter_name_kein_500(engine, client):
    _login(engine, client)
    _rolle_anlegen(engine, "dublette")
    r = client.post("/benutzer/rollen/neu", data={"name": "dublette"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "existiert bereits" in r.text


def test_rolle_bearbeiten(engine, client):
    _login(engine, client)
    rid = _rolle_anlegen(engine, "monteur", recht_lager=Zugriff.ANSEHEN)
    r = client.post(
        f"/benutzer/rollen/{rid}",
        data={"name": "monteur", "recht_lager": "bearbeiten", "recht_anlagen": "ansehen"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        rolle = session.get(Rollenprofil, rid)
        assert rolle.recht_lager == Zugriff.BEARBEITEN
        assert rolle.recht_anlagen == Zugriff.ANSEHEN


def test_standard_rolle_nicht_loeschbar(engine, client):
    _login(engine, client)
    rid = _rolle_anlegen(engine, "admin", ist_admin=True, ist_standard=True)
    r = client.post(f"/benutzer/rollen/{rid}/loeschen", follow_redirects=False)
    assert r.status_code == 200
    assert "Standard" in r.text
    with Session(engine) as session:
        assert session.get(Rollenprofil, rid) is not None


def test_rolle_in_benutzung_nicht_loeschbar(engine, client):
    _login(engine, client)
    rid = _rolle_anlegen(engine, "genutzt")
    with Session(engine) as session:
        session.add(Benutzer(login="u1", name="U1", passwort_hash="h", rolle="genutzt"))
        session.commit()
    r = client.post(f"/benutzer/rollen/{rid}/loeschen", follow_redirects=False)
    assert r.status_code == 200
    assert "verwendet" in r.text
    with Session(engine) as session:
        assert session.get(Rollenprofil, rid) is not None


def test_unbenutzte_rolle_loeschbar(engine, client):
    _login(engine, client)
    rid = _rolle_anlegen(engine, "wegdamit")
    r = client.post(f"/benutzer/rollen/{rid}/loeschen", follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        assert session.get(Rollenprofil, rid) is None
