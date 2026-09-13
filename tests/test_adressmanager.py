"""Adressmanager (Kplus-Stil): Adressnummern, Kplus-Felder, Ansprechpartner,
Lieferadressen (auch am Beleg/PDF), Aktivitäten mit Wiedervorlage, Wiedervorlage-Seite."""

from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Aktivitaet,
    Ansprechpartner,
    Artikel,
    Beleg,
    Belegart,
    Lieferadresse,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import adresse_service, beleg_service
from app.services.auth_service import hash_passwort


def _login(engine, client, login="tester", rolle=Rolle.SACHBEARBEITER) -> int:
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login, name=f"Name {login}", passwort_hash=hash_passwort("geheim"),
            rolle=rolle, aktiv=True,
        )
        session.add(benutzer)
        session.commit()
        benutzer_id = benutzer.id
    r = client.post("/login", data={"login": login, "passwort": "geheim"}, follow_redirects=False)
    assert r.status_code == 302
    return benutzer_id


FORM = {
    "typ": "kunde",
    "firma": "Schedl Energie + Technik GmbH",
    "matchcode": "SCHEDL",
    "anrede": "Firma",
    "name2": "Abteilung Einkauf",
    "briefanrede": "Sehr geehrte Damen und Herren,",
    "strasse": "Industriezone 3",
    "plz": "9900",
    "ort": "Lienz",
    "land": "Österreich",
    "telefon": "04852 1234",
    "telefon2": "04852 1235",
    "mobil": "0664 999",
    "fax": "04852 1236",
    "email": "office@schedl.at",
    "homepage": "www.schedl.at",
    "branche": "Industrie",
    "merkmale": "Wartungsvertrag, Großkunde",
    "fibunummer": "200123",
    "zahlungsziel_tage": "30",
    "skonto_prozent": "2",
    "skonto_tage": "10",
    "standard_rabatt_prozent": "5",
    "aktiv": "true",
}


def _adresse_anlegen(client, **extra) -> int:
    r = client.post("/adressen/neu", data={**FORM, **extra}, follow_redirects=False)
    assert r.status_code == 302, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


# ------------------------------------------------------------- Stammdaten


def test_neue_adresse_bekommt_adressnummer_und_kplus_felder(engine, client):
    _login(engine, client)
    a1 = _adresse_anlegen(client)
    a2 = _adresse_anlegen(
        client, firma="Zweiter Kunde", matchcode="ZWEI", trotzdem_speichern="true"
    )
    with Session(engine) as session:
        erste, zweite = session.get(Adresse, a1), session.get(Adresse, a2)
        assert erste.adressnr == "10001"
        assert zweite.adressnr == "10002"
        assert erste.matchcode == "SCHEDL"
        assert erste.name2 == "Abteilung Einkauf"
        assert erste.briefanrede.startswith("Sehr geehrte")
        assert (erste.telefon2, erste.mobil, erste.fax) == ("04852 1235", "0664 999", "04852 1236")
        assert erste.homepage == "www.schedl.at"
        assert erste.branche == "Industrie"
        assert erste.merkmal_liste == ["Wartungsvertrag", "Großkunde"]
        assert erste.fibunummer == "200123"
        assert (erste.skonto_prozent, erste.skonto_tage) == (Decimal("2"), 10)

    html = client.get(f"/adressen/{a1}").text
    assert "10001" in html and "SCHEDL" in html
    reiter = ("Adressdaten", "Ansprechpartner", "Lieferadressen", "Aktivitäten", "Belege",
              "Anlagen")
    for name in reiter:
        assert name in html
    assert "Neues Angebot" in html  # Schnellstart in den Beleg


def test_manuelle_adressnummer_muss_frei_sein(engine, client):
    _login(engine, client)
    _adresse_anlegen(client, adressnr="500")
    r = client.post(
        "/adressen/neu", data={**FORM, "firma": "Andere", "adressnr": "500"}, follow_redirects=False
    )
    assert r.status_code == 409
    assert "bereits vergeben" in r.text
    # Bearbeiten auf eine fremde Nummer ebenso
    a2 = _adresse_anlegen(client, firma="Dritte")
    r = client.post(f"/adressen/{a2}", data={**FORM, "firma": "Dritte", "adressnr": "500"})
    assert r.status_code == 409
    with Session(engine) as session:
        assert session.get(Adresse, a2).adressnr == "10001"


def test_adressnummer_startwert_in_einstellungen(engine, client):
    _login(engine, client, login="admin", rolle=Rolle.ADMIN)
    r = client.post(
        "/einstellungen/nummernkreise", data={"stand_adresse": "20500"}, follow_redirects=False
    )
    assert "nk_gespeichert=1" in r.headers["location"]
    a = _adresse_anlegen(client)
    with Session(engine) as session:
        assert session.get(Adresse, a).adressnr == "20501"
    html = client.get("/einstellungen").text
    assert 'name="stand_adresse"' in html and "20501" in html
    r = client.post(
        "/einstellungen/nummernkreise", data={"stand_adresse": "5"}, follow_redirects=False
    )
    assert "nk_fehler=" in r.headers["location"]


def test_liste_und_suche_ueber_nummer_matchcode_telefon(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client)
    _adresse_anlegen(client, firma="Anderer Betrieb", matchcode="ANDER", telefon="0316 777",
                     email="x@anderer.at", ort="Graz", plz="8010", name2="")
    html = client.get("/adressen").text
    assert "Adressmanager" in html and "10001" in html and "SCHEDL" in html
    for q in ("10001", "sched", "04852 1234", "Lienz"):
        r = client.get("/adressen/suche", params={"q": q})
        assert f"/adressen/{a}" in r.text, q
        assert "Anderer Betrieb" not in r.text, q
    # globale Suche kennt Nummer und Matchcode
    assert "Schedl Energie" in client.get("/suche", params={"q": "SCHEDL"}).text
    assert "Schedl Energie" in client.get("/suche", params={"q": "10001"}).text


# ---------------------------------------------------------- Ansprechpartner


def test_schnellerfassung_legt_hauptkontakt_an(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client, ansprechpartner="Mario Egger")
    with Session(engine) as session:
        stmt = select(Ansprechpartner).where(Ansprechpartner.adresse_id == a)
        kontakte = session.exec(stmt).all()
        assert [(k.nachname, k.ist_hauptkontakt) for k in kontakte] == [("Mario Egger", True)]
        assert session.get(Adresse, a).ansprechpartner == "Mario Egger"


def test_ansprechpartner_anlegen_haupt_bearbeiten_loeschen(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client)
    r = client.post(
        f"/adressen/{a}/ansprechpartner",
        data={"anrede": "Herr", "vorname": "Stephan", "nachname": "Hofer", "funktion": "Einkauf",
              "telefon": "04852 99", "email": "hofer@schedl.at"},
    )
    assert r.status_code == 200 and "Herr Stephan Hofer" in r.text
    r = client.post(
        f"/adressen/{a}/ansprechpartner",
        data={"anrede": "Frau", "vorname": "Julia", "nachname": "Koram",
              "ist_hauptkontakt": "true"},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        adresse = session.get(Adresse, a)
        assert adresse.ansprechpartner == "Frau Julia Koram"  # Hauptkontakt denormalisiert
        kontakte = adresse_service.ansprechpartner_liste(session, a)
        assert [k.ist_hauptkontakt for k in kontakte] == [True, False]
        hofer = next(k for k in kontakte if k.nachname == "Hofer")
        koram = next(k for k in kontakte if k.nachname == "Koram")

    # Haupt umsetzen
    r = client.post(f"/adressen/{a}/ansprechpartner/{hofer.id}/haupt")
    assert r.status_code == 200
    with Session(engine) as session:
        assert session.get(Adresse, a).ansprechpartner == "Herr Stephan Hofer"
    # Bearbeiten (Formular + Speichern)
    assert "Ansprechpartner bearbeiten" in client.get(
        f"/adressen/{a}/ansprechpartner/{hofer.id}/form"
    ).text
    r = client.post(
        f"/adressen/{a}/ansprechpartner/{hofer.id}",
        data={"anrede": "Herr", "vorname": "Stefan", "nachname": "Hofer", "aktiv": "true"},
    )
    assert r.status_code == 200 and "Herr Stefan Hofer" in r.text
    # Löschen des Hauptkontakts → nächster wird Haupt
    r = client.post(f"/adressen/{a}/ansprechpartner/{hofer.id}/loeschen")
    assert r.status_code == 200 and "Hofer" not in r.text
    with Session(engine) as session:
        assert session.get(Adresse, a).ansprechpartner == "Frau Julia Koram"
        assert session.get(Ansprechpartner, koram.id).ist_hauptkontakt is True
    # fremde Adresse → 404
    b = _adresse_anlegen(client, firma="Fremd")
    assert client.post(f"/adressen/{b}/ansprechpartner/{koram.id}/haupt").status_code == 404


# ------------------------------------------------------------ Lieferadressen


def _artikel(engine) -> int:
    with Session(engine) as session:
        artikel = Artikel(artikelnr="A1", bezeichnung="Ding", vk_preis=Decimal("10"), ust_satz=20)
        session.add(artikel)
        session.commit()
        return artikel.id


def test_lieferadresse_crud_beleg_und_pdf(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client)
    r = client.post(
        f"/adressen/{a}/lieferadressen",
        data={"bezeichnung": "Baustelle Nord", "name": "Schedl Bauleitung", "strasse": "Feldweg 9",
              "plz": "9900", "ort": "Lienz", "notiz": "Mo–Fr 7–15 Uhr"},
    )
    assert r.status_code == 200 and "Baustelle Nord" in r.text
    with Session(engine) as session:
        la = session.exec(select(Lieferadresse).where(Lieferadresse.adresse_id == a)).one()
        la_id = la.id

    # Beleg: Lieferadresse im Kopf wählbar, wird gespeichert und aufs PDF gedruckt
    artikel_id = _artikel(engine)
    r = client.post("/belege/auftrag/neu", data={"adresse_id": a}, follow_redirects=False)
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    html = client.get(f"/belege/detail/{beleg_id}").text
    assert 'name="lieferadresse_id"' in html and "Baustelle Nord" in html
    client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    r = client.post(
        f"/belege/detail/{beleg_id}/kopf",
        data={"datum": "2026-09-13", "lieferadresse_id": str(la_id)}, follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).lieferadresse_id == la_id
    pdf_html = client.get(f"/belege/detail/{beleg_id}/druckvorschau").text
    assert "Lieferadresse:" in pdf_html and "Feldweg 9" in pdf_html

    # Folgebeleg erbt die Lieferadresse
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")
    r = client.post(f"/belege/detail/{beleg_id}/folgebeleg", follow_redirects=False)
    ls_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    with Session(engine) as session:
        assert session.get(Beleg, ls_id).lieferadresse_id == la_id

    # Bearbeiten, dann Löschen → Belege verlieren den Bezug, kein Fehler
    r = client.post(f"/adressen/{a}/lieferadressen/{la_id}", data={"bezeichnung": "Baustelle Süd",
                                                                   "ort": "Lienz"})
    assert "Baustelle Süd" in r.text
    r = client.post(f"/adressen/{a}/lieferadressen/{la_id}/loeschen")
    assert r.status_code == 200 and "Baustelle Süd" not in r.text
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).lieferadresse_id is None
        assert session.get(Beleg, ls_id).lieferadresse_id is None


def test_lieferadresse_fremder_adresse_wird_ignoriert(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client)
    b = _adresse_anlegen(client, firma="Anderer")
    client.post(f"/adressen/{b}/lieferadressen", data={"bezeichnung": "Fremd", "ort": "Wien"})
    with Session(engine) as session:
        fremd_id = session.exec(select(Lieferadresse)).one().id
    r = client.post("/belege/auftrag/neu", data={"adresse_id": a}, follow_redirects=False)
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/belege/detail/{beleg_id}/kopf", data={"lieferadresse_id": str(fremd_id)})
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).lieferadresse_id is None


# ------------------------------------------------- Aktivitäten / Wiedervorlage


def test_aktivitaet_mit_wiedervorlage_und_belegbezug(engine, client):
    benutzer_id = _login(engine, client)
    a = _adresse_anlegen(client)
    with Session(engine) as session:
        adresse = session.get(Adresse, a)
        beleg = beleg_service.erstelle_beleg(session, Belegart.ANGEBOT, adresse, betreff="Test")
        session.commit()
        beleg_id, belegnr = beleg.id, beleg.belegnr
    gestern = (date.today() - timedelta(days=1)).isoformat()
    r = client.post(
        f"/adressen/{a}/aktivitaeten",
        data={"typ": "aufgabe", "betreff": "Angebot nachfassen", "wiedervorlage_am": gestern,
              "beleg_id": str(beleg_id)},
    )
    assert r.status_code == 200
    assert "Aufgabe" in r.text and "Angebot nachfassen" in r.text
    assert "list-group-item-danger" in r.text  # fällig → rot
    assert belegnr in r.text and "Name tester" in r.text
    with Session(engine) as session:
        akt = session.exec(select(Aktivitaet)).one()
        assert akt.beleg_id == beleg_id and akt.benutzer_id == benutzer_id
        assert akt.wiedervorlage_am == date.today() - timedelta(days=1)
        akt_id = akt.id

    # Erledigt umschalten und wieder öffnen
    r = client.post(f"/adressen/{a}/aktivitaeten/{akt_id}/erledigt")
    assert "Wieder öffnen" in r.text and "list-group-item-danger" not in r.text
    with Session(engine) as session:
        akt = session.get(Aktivitaet, akt_id)
        assert akt.erledigt and akt.erledigt_am is not None
    r = client.post(f"/adressen/{a}/aktivitaeten/{akt_id}/erledigt")
    assert "list-group-item-danger" in r.text
    # Löschen
    r = client.post(f"/adressen/{a}/aktivitaeten/{akt_id}/loeschen")
    assert r.status_code == 200 and "Angebot nachfassen" not in r.text


def test_wiedervorlage_seite_und_dashboard(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client)
    heute = date.today()
    daten = [
        ("Überfällig", heute - timedelta(days=3)),
        ("Heute", heute),
        ("Nächste Woche", heute + timedelta(days=5)),
        ("Später", heute + timedelta(days=30)),
    ]
    for betreff, wv in daten:
        client.post(f"/adressen/{a}/aktivitaeten",
                    data={"typ": "termin", "betreff": betreff, "wiedervorlage_am": wv.isoformat()})
    client.post(f"/adressen/{a}/aktivitaeten", data={"typ": "notiz", "betreff": "Ohne Datum"})

    r = client.get("/wiedervorlage")
    assert r.status_code == 200
    assert "Überfällig" in r.text and "Heute" in r.text
    assert "Nächste Woche" not in r.text and "Ohne Datum" not in r.text
    assert "2 fällig" in r.text
    r = client.get("/wiedervorlage", params={"filter": "woche"})
    assert "Nächste Woche" in r.text and "Später" not in r.text
    r = client.get("/wiedervorlage", params={"filter": "alle"})
    assert "Später" in r.text and "Ohne Datum" not in r.text

    # Dashboard-Kachel zeigt die fälligen
    html = client.get("/").text
    assert "Wiedervorlage fällig" in html and "Überfällig" in html and "Später" not in html

    # Aus der Wiedervorlage heraus erledigen → zurück zur Liste, Eintrag weg
    with Session(engine) as session:
        akt = session.exec(select(Aktivitaet).where(Aktivitaet.betreff == "Überfällig")).one()
    r = client.post(f"/adressen/{a}/aktivitaeten/{akt.id}/erledigt?zurueck=wiedervorlage",
                    follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/wiedervorlage"
    assert "Überfällig" not in client.get("/wiedervorlage").text

    # nur eigene: anderer Benutzer sieht mit Filter nichts
    client.post("/logout")
    _login(engine, client, login="zweiter")
    r = client.get("/wiedervorlage", params={"nur_eigene": "1"})
    assert "Heute" not in r.text
    assert "Heute" in client.get("/wiedervorlage").text


def test_wiedervorlage_braucht_adressrecht(engine, client):
    from app.models import Rollenprofil, Zugriff

    with Session(engine) as session:
        session.add(Rollenprofil(name="nurlager", recht_lager=Zugriff.ANSEHEN))
        session.commit()
    _login(engine, client, login="lagerist", rolle="nurlager")
    assert client.get("/wiedervorlage").status_code == 403


def test_beleg_neu_mit_vorgewaehlter_adresse(engine, client):
    _login(engine, client)
    a = _adresse_anlegen(client)
    _adresse_anlegen(client, firma="Andere", typ=AdressTyp.KUNDE)
    html = client.get("/belege/angebot/neu", params={"adresse_id": a}).text
    assert f'value="{a}" selected' in html
