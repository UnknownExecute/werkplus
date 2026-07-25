"""Tests für die globale Suche über Adressen, Artikel und Belege."""

from sqlmodel import Session

from app.models import Adresse, AdressTyp, Artikel, Belegart, Rolle
from app.models.benutzer import Benutzer
from app.services import beleg_service
from app.services.auth_service import hash_passwort


def _login(engine, client, login="tester", passwort="geheim"):
    """Legt einen Benutzer an und meldet den Client an."""
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login,
            name="Tester",
            passwort_hash=hash_passwort(passwort),
            rolle=Rolle.SACHBEARBEITER,
            aktiv=True,
        )
        session.add(benutzer)
        session.commit()
    r = client.post(
        "/login", data={"login": login, "passwort": passwort}, follow_redirects=False
    )
    assert r.status_code == 302


def _testdaten(engine) -> tuple[str, str, str]:
    """Legt Adresse, Artikel und Beleg an; liefert (firma, artikelnr, belegnr)."""
    with Session(engine) as session:
        adresse = Adresse(
            typ=AdressTyp.KUNDE, firma="Musterfirma GmbH", ort="Linz", uid="ATU99999999"
        )
        session.add(adresse)
        artikel = Artikel(
            artikelnr="ART-777", bezeichnung="Testartikel", warengruppe="Sonstiges"
        )
        session.add(artikel)
        session.commit()
        session.refresh(adresse)
        beleg = beleg_service.erstelle_beleg(
            session, Belegart.ANGEBOT, adresse, benutzer_id=None
        )
        session.commit()
        return adresse.firma, artikel.artikelnr, beleg.belegnr


def test_suche_erfordert_login(client):
    r = client.get("/suche", params={"q": "Muster"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_suche_findet_adresse(engine, client):
    _login(engine, client)
    _testdaten(engine)
    r = client.get("/suche", params={"q": "Musterfirma"})
    assert r.status_code == 200
    assert "Musterfirma GmbH" in r.text


def test_suche_findet_artikel(engine, client):
    _login(engine, client)
    _testdaten(engine)
    r = client.get("/suche", params={"q": "ART-777"})
    assert r.status_code == 200
    assert "Testartikel" in r.text


def test_suche_findet_beleg(engine, client):
    _login(engine, client)
    _, _, belegnr = _testdaten(engine)
    r = client.get("/suche", params={"q": belegnr})
    assert r.status_code == 200
    assert belegnr in r.text


def test_suche_ohne_treffer_zeigt_hinweis(engine, client):
    _login(engine, client)
    _testdaten(engine)
    r = client.get("/suche", params={"q": "GibtEsNichtXYZ"})
    assert r.status_code == 200
    assert "Keine Treffer gefunden" in r.text


def test_suche_zu_kurz_liefert_keine_treffer(engine, client):
    _login(engine, client)
    _testdaten(engine)
    r = client.get("/suche", params={"q": "M"})
    assert r.status_code == 200
    assert "Musterfirma GmbH" not in r.text
    assert "Keine Treffer gefunden" in r.text
