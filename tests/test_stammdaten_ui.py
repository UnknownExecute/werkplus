"""Tests für die Stammdaten-UI (Adressen, Artikel, Aktivitäten)."""

from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    Anlage,
    Artikel,
    Beleg,
    Belegart,
    BelegStatus,
    Rolle,
    Seriennummer,
    SnStatus,
)
from app.models.benutzer import Benutzer
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


ADRESSE_FORM = {
    "typ": "kunde",
    "firma": "Muster GmbH",
    "ansprechpartner": "Max Muster",
    "strasse": "Hauptstraße 1",
    "plz": "4020",
    "ort": "Linz",
    "land": "Österreich",
    "uid": "ATU12345678",
    "email": "office@muster.at",
    "telefon": "0732 123456",
    "zahlungsziel_tage": "30",
    "rabattgruppe": "A",
    "standard_rabatt_prozent": "5,5",
    "notizen": "Testkunde",
    "aktiv": "true",
}


def test_adressen_liste_erfordert_login(client):
    r = client.get("/adressen", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_adressen_liste_200(engine, client):
    _login(engine, client)
    r = client.get("/adressen")
    assert r.status_code == 200
    assert "Adressen" in r.text


def test_adresse_anlegen_und_in_liste_sichtbar(engine, client):
    _login(engine, client)
    r = client.post("/adressen/neu", data=ADRESSE_FORM, follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        adresse = session.exec(select(Adresse).where(Adresse.firma == "Muster GmbH")).one()
        assert adresse.standard_rabatt_prozent == Decimal("5.5")
        assert adresse.zahlungsziel_tage == 30

    r = client.get("/adressen")
    assert "Muster GmbH" in r.text


def test_adresse_bearbeiten(engine, client):
    _login(engine, client)
    client.post("/adressen/neu", data=ADRESSE_FORM)
    with Session(engine) as session:
        adresse_id = session.exec(select(Adresse)).one().id

    daten = dict(ADRESSE_FORM, firma="Muster Neu GmbH", ort="Wels")
    r = client.post(f"/adressen/{adresse_id}", data=daten, follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        adresse = session.get(Adresse, adresse_id)
        assert adresse.firma == "Muster Neu GmbH"
        assert adresse.ort == "Wels"


def test_adressen_suche_filtert(engine, client):
    _login(engine, client)
    client.post("/adressen/neu", data=ADRESSE_FORM)
    client.post(
        "/adressen/neu",
        data=dict(
            ADRESSE_FORM,
            firma="Anders AG",
            ansprechpartner="Anna Anders",
            email="anna@anders.at",  # sonst trifft die Suche "Muster" die E-Mail-Domäne
            ort="Graz",
            typ="lieferant",
            uid="ATU87654321",
        ),
    )

    r = client.get("/adressen/suche", params={"q": "Muster"})
    assert "Muster GmbH" in r.text
    assert "Anders AG" not in r.text

    r = client.get("/adressen/suche", params={"q": "", "typ": "lieferant"})
    assert "Anders AG" in r.text
    assert "Muster GmbH" not in r.text


def test_adresse_dublette_firma_warnt_und_speichert_erst_mit_bestaetigung(engine, client):
    _login(engine, client)
    r = client.post("/adressen/neu", data=ADRESSE_FORM, follow_redirects=False)
    assert r.status_code == 302

    # Zweite Adresse mit gleicher Firma (case-insensitive), andere UID → Warnung
    daten = dict(ADRESSE_FORM, firma="muster gmbh", uid="ATU99999999")
    r = client.post("/adressen/neu", data=daten, follow_redirects=False)
    assert r.status_code == 200
    assert "existiert bereits" in r.text
    with Session(engine) as session:
        assert len(session.exec(select(Adresse)).all()) == 1  # nicht gespeichert

    # Zweiter Absendevorgang mit trotzdem_speichern → wird angelegt
    r = client.post(
        "/adressen/neu", data=dict(daten, trotzdem_speichern="true"), follow_redirects=False
    )
    assert r.status_code == 302
    with Session(engine) as session:
        assert len(session.exec(select(Adresse)).all()) == 2


def test_adresse_dublette_uid_warnt(engine, client):
    _login(engine, client)
    client.post("/adressen/neu", data=ADRESSE_FORM)  # UID ATU12345678

    # Andere Firma, aber gleiche UID → Warnung
    daten = dict(ADRESSE_FORM, firma="Ganz Andere GmbH")
    r = client.post("/adressen/neu", data=daten, follow_redirects=False)
    assert r.status_code == 200
    assert "existiert bereits" in r.text
    with Session(engine) as session:
        assert len(session.exec(select(Adresse)).all()) == 1


def test_adresse_detail_zeigt_kunden_cockpit_kennzahlen(engine, client):
    _login(engine, client)
    jahr = date.today().year
    with Session(engine) as session:
        kunde = Adresse(firma="Cockpit GmbH")
        artikel = Artikel(artikelnr="A-COCK", bezeichnung="Wärmepumpe")
        session.add(kunde)
        session.add(artikel)
        session.commit()
        session.refresh(kunde)
        session.refresh(artikel)

        sn = Seriennummer(
            artikel_id=artikel.id, sn="SN-COCK-1", status=SnStatus.BEIM_KUNDEN_VERBAUT
        )
        session.add(sn)
        session.commit()
        session.refresh(sn)

        # Aktive Anlage dieses Kunden
        session.add(
            Anlage(
                adresse_id=kunde.id,
                artikel_id=artikel.id,
                seriennummer_id=sn.id,
                standort="Keller",
                aktiv=True,
            )
        )

        # Bezahlte Rechnung (zählt zum Umsatz, nicht zu offenen Posten)
        session.add(
            Beleg(
                belegart=Belegart.RECHNUNG,
                belegnr="R-2024-001",
                status=BelegStatus.GEDRUCKT,
                adresse_id=kunde.id,
                datum=date(jahr, 3, 15),
                summe_netto=Decimal("1000.00"),
                summe_brutto=Decimal("1200.00"),
                bezahlt_am=date(jahr, 4, 1),
            )
        )
        # Offene Rechnung (zählt zum Umsatz UND zu offenen Posten)
        session.add(
            Beleg(
                belegart=Belegart.RECHNUNG,
                belegnr="R-2024-002",
                status=BelegStatus.GEDRUCKT,
                adresse_id=kunde.id,
                datum=date(jahr, 6, 1),
                summe_netto=Decimal("500.00"),
                summe_brutto=Decimal("600.00"),
                bezahlt_am=None,
            )
        )
        session.commit()
        kunde_id = kunde.id

    r = client.get(f"/adressen/{kunde_id}")
    assert r.status_code == 200
    # Jahresumsatz netto = 1000 + 500 = 1.500,00
    assert "1.500,00" in r.text
    assert f"Umsatz netto {jahr}" in r.text
    # Offene Posten: 1 offene Rechnung, Bruttosumme 600,00
    assert "600,00" in r.text
    assert "1 Rechnung" in r.text
    # Anlagenzahl
    assert "Anlagen" in r.text


ARTIKEL_FORM = {
    "artikelnr": "A-100",
    "bezeichnung": "Wärmepumpe X",
    "langtext": "Langbeschreibung",
    "warengruppe": "Heizung",
    "einheit": "Stk",
    "listenpreis_netto": "1.000,00",
    "rabatt1_prozent": "10",
    "rabatt2_prozent": "5",
    "aufschlag_prozent": "40",
    "vk_preis": "12,50",
    "ust_satz": "20",
    "seriennummernpflichtig": "true",
    "ist_lagerartikel": "true",
    "aktiv": "true",
}


def test_artikel_anlegen_mit_kommapreis(engine, client):
    _login(engine, client)
    r = client.post("/artikel/neu", data=ARTIKEL_FORM, follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        artikel = session.exec(select(Artikel).where(Artikel.artikelnr == "A-100")).one()
        # Kalkulation: 1000 − 10 % − 5 % = 855,00 EK; VK 12,50 wurde manuell übernommen
        assert artikel.listenpreis_netto == Decimal("1000.00")
        assert artikel.ek_preis == Decimal("855.00")
        assert artikel.vk_preis == Decimal("12.50")
        assert artikel.seriennummernpflichtig is True

    r = client.get("/artikel")
    assert "Wärmepumpe X" in r.text


def test_artikel_suche_filtert(engine, client):
    _login(engine, client)
    client.post("/artikel/neu", data=ARTIKEL_FORM)
    client.post(
        "/artikel/neu",
        data=dict(ARTIKEL_FORM, artikelnr="B-200", bezeichnung="Montagestunde", warengruppe=""),
    )

    r = client.get("/artikel/suche", params={"q": "Wärme"})
    assert "Wärmepumpe X" in r.text
    assert "Montagestunde" not in r.text


def test_artikel_detail_zeigt_seriennummern(engine, client):
    _login(engine, client)
    client.post("/artikel/neu", data=ARTIKEL_FORM)
    with Session(engine) as session:
        artikel_id = session.exec(select(Artikel)).one().id
        session.add(Seriennummer(artikel_id=artikel_id, sn="SN-0001", status=SnStatus.AUF_LAGER))
        session.add(Seriennummer(artikel_id=artikel_id, sn="SN-0002", status=SnStatus.VERKAUFT))
        session.commit()

    r = client.get(f"/artikel/{artikel_id}")
    assert r.status_code == 200
    assert "SN-0001" in r.text
    assert "SN-0002" in r.text
    assert "auf Lager" in r.text
    assert "verkauft" in r.text


def test_artikel_als_set_anlegen_und_detail_zeigt_komponenten_abschnitt(engine, client):
    _login(engine, client)
    daten = dict(ARTIKEL_FORM, artikelnr="SET-9", bezeichnung="Komplett-Set", ist_set="true")
    r = client.post("/artikel/neu", data=daten, follow_redirects=False)
    assert r.status_code == 302

    with Session(engine) as session:
        artikel = session.exec(select(Artikel).where(Artikel.artikelnr == "SET-9")).one()
        assert artikel.ist_set is True
        artikel_id = artikel.id

        # Ein Nicht-Set-Artikel als potenzielle Komponente
        komponente = Artikel(artikelnr="K-9", bezeichnung="Einzelteil")
        session.add(komponente)
        session.commit()

    r = client.get(f"/artikel/{artikel_id}")
    assert r.status_code == 200
    assert "Set-Komponenten" in r.text
    # Nicht-Set-Artikel erscheint im Auswahl-Dropdown
    assert "Einzelteil" in r.text


def test_artikel_ohne_ist_set_bleibt_normaler_artikel(engine, client):
    _login(engine, client)
    # ARTIKEL_FORM enthält kein ist_set -> Default False, kein Set-Abschnitt
    r = client.post("/artikel/neu", data=ARTIKEL_FORM, follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        artikel = session.exec(select(Artikel).where(Artikel.artikelnr == "A-100")).one()
        assert artikel.ist_set is False
        artikel_id = artikel.id

    r = client.get(f"/artikel/{artikel_id}")
    assert "Set-Komponenten" not in r.text


def test_aktivitaet_anlegen_erscheint_in_liste(engine, client):
    _login(engine, client)
    client.post("/adressen/neu", data=ADRESSE_FORM)
    with Session(engine) as session:
        adresse_id = session.exec(select(Adresse)).one().id

    r = client.post(
        f"/adressen/{adresse_id}/aktivitaeten",
        data={"typ": "telefonat", "betreff": "Rückruf wegen Angebot", "text": "Kunde erreicht"},
    )
    assert r.status_code == 200
    assert "Rückruf wegen Angebot" in r.text
    assert "Telefonat" in r.text

    # Aktivität erscheint auch auf der Detailseite
    r = client.get(f"/adressen/{adresse_id}")
    assert "Rückruf wegen Angebot" in r.text
