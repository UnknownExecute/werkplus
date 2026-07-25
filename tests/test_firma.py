"""Tests für Firmenstammdaten-Service und Einstellungsseite."""

from sqlmodel import Session, select

from app.config import settings
from app.main import app as fastapi_app
from app.models import Firma, Rolle
from app.models.benutzer import Benutzer
from app.routers import einstellungen
from app.services import firma_service
from app.services.auth_service import hash_passwort

# Router idempotent registrieren, falls app.main ihn (noch) nicht eingebunden hat.
if not any(getattr(r, "path", None) == "/einstellungen" for r in fastapi_app.routes):
    fastapi_app.include_router(einstellungen.router)


def _login(engine, client, rolle=Rolle.SACHBEARBEITER, login="tester", passwort="geheim"):
    """Legt einen Benutzer mit Rolle an und meldet den Client an."""
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
    r = client.post(
        "/login", data={"login": login, "passwort": passwort}, follow_redirects=False
    )
    assert r.status_code == 302


class TestFirmaService:
    def test_lade_firma_erstellt_aus_defaults(self, session):
        firma = firma_service.lade_firma(session)
        assert firma.id == 1
        for feld in firma_service.FIRMA_FELDER:
            assert getattr(firma, feld) == getattr(settings.firma, feld)

    def test_lade_firma_liefert_dieselbe_row_ohne_duplikat(self, session):
        erste = firma_service.lade_firma(session)
        zweite = firma_service.lade_firma(session)
        assert zweite.id == erste.id == 1
        assert len(session.exec(select(Firma)).all()) == 1

    def test_aktualisiere_firma_persistent(self, session):
        firma_service.aktualisiere_firma(session, name="Neue Firma GmbH", uid="ATU55555555")
        neu = firma_service.lade_firma(session)
        assert neu.name == "Neue Firma GmbH"
        assert neu.uid == "ATU55555555"


class TestEinstellungsseite:
    def test_get_ohne_login_redirect_login(self, client):
        r = client.get("/einstellungen", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"

    def test_get_als_nicht_admin_403(self, engine, client):
        _login(engine, client, rolle=Rolle.SACHBEARBEITER)
        r = client.get("/einstellungen", follow_redirects=False)
        assert r.status_code == 403

    def test_get_als_admin_200_mit_feldern(self, engine, client):
        _login(engine, client, rolle=Rolle.ADMIN)
        r = client.get("/einstellungen")
        assert r.status_code == 200
        # Vorbefüllt mit den Default-Firmendaten
        assert settings.firma.name in r.text
        assert settings.firma.uid in r.text
        assert 'name="iban"' in r.text

    def test_post_als_admin_aendert_daten(self, engine, client):
        _login(engine, client, rolle=Rolle.ADMIN)
        r = client.post(
            "/einstellungen",
            data={
                "name": "Geänderte Firma GmbH",
                "strasse": "Neugasse 5",
                "plz": "1020",
                "ort": "Wien",
                "land": "Österreich",
                "uid": "ATU66666666",
                "telefon": "+43 1 234567",
                "email": "office@geaendert.at",
                "iban": "AT99 8888 7777 6666 5555",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        with Session(engine) as session:
            firma = firma_service.lade_firma(session)
            assert firma.name == "Geänderte Firma GmbH"
            assert firma.uid == "ATU66666666"
