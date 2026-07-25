from sqlmodel import Session

from app.models import Benutzer, Rolle
from app.routers.auth import MAX_FEHLVERSUCHE, _reset_rate_limit
from app.services.auth_service import authentifiziere, hash_passwort, verify_passwort


def _lege_benutzer_an(engine, login="admin", passwort="geheim", aktiv=True, rolle=Rolle.ADMIN):
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login, name="Test", passwort_hash=hash_passwort(passwort),
            rolle=rolle, aktiv=aktiv,
        )
        session.add(benutzer)
        session.commit()


def test_hash_und_verify():
    h = hash_passwort("geheim")
    assert h != "geheim"
    assert verify_passwort(h, "geheim")
    assert not verify_passwort(h, "falsch")


def test_authentifiziere(engine, session):
    _lege_benutzer_an(engine)
    assert authentifiziere(session, "admin", "geheim") is not None
    assert authentifiziere(session, "admin", "falsch") is None
    assert authentifiziere(session, "gibtsnicht", "geheim") is None


def test_inaktiver_benutzer_kann_sich_nicht_anmelden(engine, session):
    _lege_benutzer_an(engine, login="ex", aktiv=False)
    assert authentifiziere(session, "ex", "geheim") is None


def test_login_flow(engine, client):
    _lege_benutzer_an(engine)

    # ohne Login → Redirect zur Login-Seite
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"

    # falsches Passwort
    r = client.post("/login", data={"login": "admin", "passwort": "falsch"})
    assert r.status_code == 401

    # korrektes Login → Redirect aufs Dashboard, Session-Cookie gesetzt
    r = client.post(
        "/login", data={"login": "admin", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"

    r = client.get("/")
    assert r.status_code == 200
    assert "Dashboard" in r.text

    # Logout
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 302
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302


def test_rate_limit_sperrt_nach_zu_vielen_fehlversuchen(engine, client):
    _reset_rate_limit()
    _lege_benutzer_an(engine)

    # Erste MAX_FEHLVERSUCHE Fehlversuche → jeweils 401
    for _ in range(MAX_FEHLVERSUCHE):
        r = client.post("/login", data={"login": "admin", "passwort": "falsch"})
        assert r.status_code == 401

    # Nächster Versuch ist gesperrt → 429, auch mit korrektem Passwort
    r = client.post("/login", data={"login": "admin", "passwort": "geheim"})
    assert r.status_code == 429
    assert "Zu viele Fehlversuche" in r.text

    # Nach Reset ist Login wieder möglich, und Erfolg setzt den Zähler zurück
    _reset_rate_limit("admin")
    r = client.post(
        "/login", data={"login": "admin", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"

    _reset_rate_limit()
