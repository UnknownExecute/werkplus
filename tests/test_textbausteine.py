"""Tests für die Textbaustein-Verwaltung (UI + Service)."""

from sqlmodel import Session, select

from app.models import Rolle, Textbaustein, TextbausteinKategorie
from app.models.benutzer import Benutzer
from app.services import textbaustein_service
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


BAUSTEIN_FORM = {
    "kuerzel": "AGB1",
    "titel": "Allgemeine Geschäftsbedingungen",
    "text": "Es gelten unsere AGB.",
    "kategorie": "fuss",
    "aktiv": "true",
}


def test_liste_erfordert_login(client):
    r = client.get("/textbausteine", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_liste_200(engine, client):
    _login(engine, client)
    r = client.get("/textbausteine")
    assert r.status_code == 200
    assert "Textbausteine" in r.text


def test_anlegen_und_in_liste_sichtbar(engine, client):
    _login(engine, client)
    r = client.post("/textbausteine/neu", data=BAUSTEIN_FORM, follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        baustein = session.exec(
            select(Textbaustein).where(Textbaustein.kuerzel == "AGB1")
        ).one()
        assert baustein.titel == "Allgemeine Geschäftsbedingungen"
        assert baustein.kategorie == TextbausteinKategorie.FUSS
        assert baustein.aktiv is True

    r = client.get("/textbausteine")
    assert "Allgemeine Geschäftsbedingungen" in r.text


def test_bearbeiten_aendert_werte(engine, client):
    _login(engine, client)
    client.post("/textbausteine/neu", data=BAUSTEIN_FORM)
    with Session(engine) as session:
        baustein_id = session.exec(select(Textbaustein)).one().id

    daten = dict(
        BAUSTEIN_FORM, titel="AGB neu", kategorie="kopf", text="Neuer Text", aktiv=""
    )
    # aktiv abwählen: Checkbox nicht mitschicken
    del daten["aktiv"]
    r = client.post(f"/textbausteine/{baustein_id}", data=daten, follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        baustein = session.get(Textbaustein, baustein_id)
        assert baustein.titel == "AGB neu"
        assert baustein.kategorie == TextbausteinKategorie.KOPF
        assert baustein.text == "Neuer Text"
        assert baustein.aktiv is False


def test_kategorie_filter_und_suche(engine, client):
    _login(engine, client)
    client.post("/textbausteine/neu", data=BAUSTEIN_FORM)  # Fußtext, "AGB"
    client.post(
        "/textbausteine/neu",
        data=dict(
            BAUSTEIN_FORM,
            kuerzel="GRUSS",
            titel="Grußformel",
            text="Mit freundlichen Grüßen",
            kategorie="kopf",
        ),
    )

    # Textsuche
    r = client.get("/textbausteine/suche", params={"q": "Grüßen"})
    assert "Grußformel" in r.text
    assert "Allgemeine Geschäftsbedingungen" not in r.text

    # Kategorie-Filter
    r = client.get("/textbausteine/suche", params={"q": "", "kategorie": "fuss"})
    assert "Allgemeine Geschäftsbedingungen" in r.text
    assert "Grußformel" not in r.text


def test_service_suche_bausteine(session):
    session.add(
        Textbaustein(
            kuerzel="A", titel="Alpha", text="Erster", kategorie=TextbausteinKategorie.KOPF
        )
    )
    session.add(
        Textbaustein(
            kuerzel="B", titel="Beta", text="Zweiter", kategorie=TextbausteinKategorie.FUSS
        )
    )
    session.commit()

    # Query über Titel
    treffer = textbaustein_service.suche_bausteine(session, q="Alpha")
    assert [b.titel for b in treffer] == ["Alpha"]

    # Kategorie-Filter
    treffer = textbaustein_service.suche_bausteine(
        session, kategorie=TextbausteinKategorie.FUSS
    )
    assert [b.titel for b in treffer] == ["Beta"]

    # Ohne Filter: beide, sortiert nach Kategorie (kopf < fuss? -> alphabetisch nach Wert)
    treffer = textbaustein_service.suche_bausteine(session)
    assert len(treffer) == 2
