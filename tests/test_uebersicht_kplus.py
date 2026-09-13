"""Kplus-Belegübersicht (VK/EK), Kopffelder, Angebot erhalten/nicht erhalten, Löschen.

Deckt die Runde „Belegübersicht im Kplus-Stil" ab: Reiter je Belegart,
Filter (Status/Datum/nur eigene/Auftragsart), Summenzeile, Aktionen aus der
Übersicht heraus, neue Kopffelder (Betreff/Termin/Auftragsart) im Editor und
auf dem PDF-HTML sowie die Nummernkreis-Einstellung.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Artikel,
    Beleg,
    Belegart,
    BelegPosition,
    BelegStatus,
    Zeiterfassung,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import beleg_service
from app.services.auth_service import hash_passwort
from app.services.fehler import BelegGesperrt, UngueltigerStatusUebergang

JAHR = date.today().year
JJ = f"{JAHR % 100:02d}"


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


def _stammdaten(engine) -> tuple[int, int]:
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.BEIDE, firma="Kaeser Kompressoren", ort="Lienz")
        artikel = Artikel(
            artikelnr="WT-1", bezeichnung="Wartungsteil", vk_preis=Decimal("100.00"), ust_satz=20
        )
        session.add(adresse)
        session.add(artikel)
        session.commit()
        return adresse.id, artikel.id


def _beleg(engine, art, adresse_id, **kopf) -> int:
    with Session(engine) as session:
        adresse = session.get(Adresse, adresse_id)
        beleg = beleg_service.erstelle_beleg(session, art, adresse, **kopf)
        session.commit()
        return beleg.id


def _mit_position(engine, beleg_id, artikel_id, menge="1"):
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        artikel = session.get(Artikel, artikel_id)
        beleg_service.fuege_position_hinzu(session, beleg, artikel, menge=Decimal(menge))
        session.commit()


def _status(engine, beleg_id, ziel):
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        beleg_service.setze_status(session, beleg, ziel)
        session.commit()


# ------------------------------------------------------------- Übersicht (Seite)


def test_uebersicht_vk_reiter_und_zeilen(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id, betreff="Wartungsteile CompAir")
    _beleg(engine, Belegart.AUFTRAG, adresse_id, betreff="Auftrag XY")

    r = client.get("/belege/uebersicht/vk")
    assert r.status_code == 200
    assert "Belegübersicht VK" in r.text
    for reiter in ("Angebot", "Auftrag", "Lieferschein", "Rechnung", "Gutschrift"):
        assert f"?art={reiter.lower()}" in r.text
    # Standardreiter = Angebot: nur das Angebot in der Tabelle
    assert f"/belege/detail/{angebot_id}" in r.text
    assert "Wartungsteile CompAir" in r.text
    assert "Auftrag XY" not in r.text
    assert 'data-nr="' + JJ + '0001"' in r.text
    # Aktionsleiste (Kplus-Begriffe)
    for aktion in ("Belegkopf aufrufen", "Positionen aufrufen", "Beleg drucken",
                   "Beleg kopieren", "Beleg löschen", "Auftrag erhalten",
                   "Auftrag nicht erhalten", "Adresse aufrufen"):
        assert aktion in r.text
    # Summenzeile
    assert "von 1 markiert" in r.text


def test_uebersicht_reiter_auftrag_mit_auftragsart(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    service_id = _beleg(engine, Belegart.AUFTRAG, adresse_id, auftragsart="service", betreff="S")
    liefer_id = _beleg(engine, Belegart.AUFTRAG, adresse_id, auftragsart="liefer", betreff="L")

    r = client.get("/belege/uebersicht/vk", params={"art": "auftrag"})
    assert "Serviceauftrag" in r.text and "Lieferauftrag" in r.text
    assert 'name="auftragsart"' in r.text  # Filter nur im Auftrags-Reiter

    r = client.get("/belege/uebersicht/vk", params={"art": "auftrag", "auftragsart": "service"})
    assert f"/belege/detail/{service_id}" in r.text
    assert f"/belege/detail/{liefer_id}" not in r.text


def test_uebersicht_ek_und_unbekannter_bereich(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    bestellung_id = _beleg(engine, Belegart.BESTELLUNG, adresse_id)

    r = client.get("/belege/uebersicht/ek")
    assert r.status_code == 200
    assert "Belegübersicht EK" in r.text
    assert "Preisanfrage" in r.text  # erster EK-Reiter (Kplus-Reihenfolge)
    assert "Eingangslieferschein" in r.text and "Eingangsrechnung" in r.text
    r = client.get("/belege/uebersicht/ek", params={"art": "bestellung"})
    assert f"/belege/detail/{bestellung_id}" in r.text
    assert "Auftrag erhalten" not in r.text  # nur im Angebots-Reiter

    assert client.get("/belege/uebersicht/quatsch").status_code == 404
    # Unbekannter Reiter → erster Reiter statt Fehler
    assert client.get("/belege/uebersicht/vk", params={"art": "bestellung"}).status_code == 200


def test_uebersicht_statusfilter(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    entwurf_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    gedruckt_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _mit_position(engine, gedruckt_id, artikel_id)
    _status(engine, gedruckt_id, BelegStatus.GEDRUCKT)
    erledigt_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _mit_position(engine, erledigt_id, artikel_id)
    _status(engine, erledigt_id, BelegStatus.GEDRUCKT)
    _status(engine, erledigt_id, BelegStatus.ERLEDIGT)

    def ids(status_filter):
        r = client.get(
            "/belege/uebersicht/vk/zeilen",
            params={"art": "angebot", "status_filter": status_filter},
        )
        assert r.status_code == 200
        return {i for i in (entwurf_id, gedruckt_id, erledigt_id) if f'data-id="{i}"' in r.text}

    assert ids("alle") == {entwurf_id, gedruckt_id, erledigt_id}
    assert ids("unerledigt") == {entwurf_id, gedruckt_id}
    assert ids("erledigt") == {erledigt_id}
    assert ids("nicht_gedruckt") == {entwurf_id}
    assert ids("storniert") == set()
    assert ids("gibtsnicht") == {entwurf_id, gedruckt_id, erledigt_id}  # unbekannt → alle


def test_uebersicht_datum_suche_nur_eigene_und_summe(engine, client):
    benutzer_id = _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    heute = date.today()
    alt_id = _beleg(
        engine, Belegart.RECHNUNG, adresse_id,
        datum=heute - timedelta(days=40), betreff="Altes Jahr",
    )
    neu_id = _beleg(
        engine, Belegart.RECHNUNG, adresse_id,
        datum=heute, betreff="Frisch", benutzer_id=benutzer_id,
    )
    vor_einer_woche = (heute - timedelta(days=7)).isoformat()
    _mit_position(engine, alt_id, artikel_id, "2")  # 240 brutto
    _mit_position(engine, neu_id, artikel_id, "1")  # 120 brutto

    basis = {"art": "rechnung"}
    r = client.get("/belege/uebersicht/vk/zeilen", params={**basis, "von": vor_einer_woche})
    assert f'data-id="{neu_id}"' in r.text and f'data-id="{alt_id}"' not in r.text
    r = client.get("/belege/uebersicht/vk/zeilen", params={**basis, "bis": vor_einer_woche})
    assert f'data-id="{alt_id}"' in r.text and f'data-id="{neu_id}"' not in r.text
    # Suchbegriff trifft auch den Betreff
    r = client.get("/belege/uebersicht/vk/zeilen", params={**basis, "q": "frisch"})
    assert f'data-id="{neu_id}"' in r.text and f'data-id="{alt_id}"' not in r.text
    # nur eigene Belege
    r = client.get("/belege/uebersicht/vk/zeilen", params={**basis, "nur_eigene": "1"})
    assert f'data-id="{neu_id}"' in r.text and f'data-id="{alt_id}"' not in r.text
    # Gesamtsumme über alle Treffer (nicht nur die Seite)
    r = client.get("/belege/uebersicht/vk/zeilen", params=basis)
    assert "360,00" in r.text
    # ungültiges Datum → kein Filter statt 422
    r = client.get("/belege/uebersicht/vk", params={**basis, "von": "31.02.2026"})
    assert r.status_code == 200


def test_uebersicht_zeigt_folgebeleg_und_ersteller(engine, client):
    benutzer_id = _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id, benutzer_id=benutzer_id)
    _mit_position(engine, angebot_id, artikel_id)
    _status(engine, angebot_id, BelegStatus.GEDRUCKT)
    with Session(engine) as session:
        angebot = session.get(Beleg, angebot_id)
        auftrag = beleg_service.erzeuge_folgebeleg(session, angebot)
        session.commit()
        auftrag_id, auftrag_nr = auftrag.id, auftrag.belegnr

    r = client.get("/belege/uebersicht/vk", params={"art": "angebot"})
    assert f'href="/belege/detail/{auftrag_id}">{auftrag_nr}</a>' in r.text
    assert "Name tester" in r.text


def test_uebersicht_rechte(engine, client):
    """Nur-Einkauf-Recht sieht die VK-Übersicht nicht; ohne Bearbeiten keine Neu-Aktion."""
    from app.models import Rollenprofil, Zugriff

    with Session(engine) as session:
        session.add(Rollenprofil(name="einkauf", recht_einkauf=Zugriff.BEARBEITEN))
        session.add(Rollenprofil(name="leser", recht_verkauf=Zugriff.ANSEHEN))
        session.commit()
    _login(engine, client, login="ek", rolle="einkauf")
    assert client.get("/belege/uebersicht/ek").status_code == 200
    assert client.get("/belege/uebersicht/vk").status_code == 403
    client.post("/logout")

    _login(engine, client, login="le", rolle="leser")
    r = client.get("/belege/uebersicht/vk")
    assert r.status_code == 200
    assert "Beleg löschen" not in r.text
    assert "Neu: Angebot" not in r.text


# --------------------------------------------------------- Kopffelder im Editor


def test_neu_und_kopf_mit_betreff_termin_auftragsart(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)

    r = client.post(
        "/belege/auftrag/neu",
        data={"adresse_id": adresse_id, "betreff": "Kompressor tauschen", "termin": "2026-09-30",
              "auftragsart": "service"},
        follow_redirects=False,
    )
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert (beleg.betreff, beleg.termin, beleg.auftragsart) == (
            "Kompressor tauschen", date(2026, 9, 30), "service"
        )

    html = client.get(f"/belege/detail/{beleg_id}").text
    assert "Kopfdaten" in html and "Positionen" in html  # Reiter
    assert 'value="Kompressor tauschen"' in html
    assert "Serviceauftrag" in html

    r = client.post(
        f"/belege/detail/{beleg_id}/kopf",
        data={"datum": "2026-09-12", "betreff": "Neu", "termin": "", "auftragsart": "liefer"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert (beleg.betreff, beleg.termin, beleg.auftragsart) == ("Neu", None, "liefer")


def test_auftragsart_nur_am_auftrag(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    r = client.post(
        "/belege/angebot/neu",
        data={"adresse_id": adresse_id, "auftragsart": "service"},
        follow_redirects=False,
    )
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).auftragsart == ""
    # ungültiger Wert am Auftrag → leer, kein 422
    r = client.post(
        "/belege/auftrag/neu",
        data={"adresse_id": adresse_id, "auftragsart": "kassabon"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).auftragsart == ""


def test_betreff_wandert_in_folgebeleg_und_kopie(engine):
    adresse_id, artikel_id = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id, betreff="Ölsauger")
    _mit_position(engine, angebot_id, artikel_id)
    _status(engine, angebot_id, BelegStatus.GEDRUCKT)
    with Session(engine) as session:
        angebot = session.get(Beleg, angebot_id)
        auftrag = beleg_service.erzeuge_folgebeleg(session, angebot)
        auftrag.auftragsart = "service"
        kopie = beleg_service.dupliziere_beleg(session, auftrag)
        assert auftrag.betreff == "Ölsauger"
        assert (kopie.betreff, kopie.auftragsart) == ("Ölsauger", "service")


def test_pdf_html_zeigt_betreff_termin_auftragsart(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg(
        engine, Belegart.AUFTRAG, adresse_id,
        betreff="Genmac Stromerzeuger", termin=date(2026, 10, 1), auftragsart="liefer",
    )
    _mit_position(engine, beleg_id, artikel_id)
    html = client.get(f"/belege/detail/{beleg_id}/druckvorschau").text
    assert "Genmac Stromerzeuger" in html
    assert "Termin: 01.10.2026" in html
    assert "Lieferauftrag" in html


# ------------------------------------------ Angebot: Auftrag erhalten / nicht


def test_auftrag_erhalten_erzeugt_auftrag_und_erledigt_angebot(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id, betreff="Tankgas")
    _mit_position(engine, angebot_id, artikel_id, "3")

    # Dialog (aus Editor oder Übersicht)
    r = client.get(f"/belege/detail/{angebot_id}/auftrag-erhalten-dialog")
    assert r.status_code == 200 and "Auftragsart" in r.text

    # aus dem ENTWURF heraus: Angebot wird dabei gebucht
    r = client.post(
        f"/belege/detail/{angebot_id}/auftrag-erhalten",
        data={"auftragsart": "bestell", "termin": "2026-09-20"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    auftrag_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        angebot = session.get(Beleg, angebot_id)
        auftrag = session.get(Beleg, auftrag_id)
        assert angebot.status == BelegStatus.ERLEDIGT
        assert angebot.angebotsergebnis == "erhalten"
        assert angebot.gedruckt_am is not None
        assert auftrag.belegart == Belegart.AUFTRAG
        assert auftrag.vorgaenger_beleg_id == angebot_id
        assert (auftrag.auftragsart, auftrag.termin, auftrag.betreff) == (
            "bestell", date(2026, 9, 20), "Tankgas"
        )
        positionen = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == auftrag_id)
        ).all()
        assert [p.menge for p in positionen] == [Decimal("3")]

    # zweites Mal: bereits abgeschlossen → Fehler statt zweitem Auftrag
    r = client.post(f"/belege/detail/{angebot_id}/auftrag-erhalten", follow_redirects=False)
    assert r.status_code == 302 and "fehler=" in r.headers["location"]
    assert client.get(f"/belege/detail/{angebot_id}/auftrag-erhalten-dialog").status_code == 409

    # Übersicht zeigt das Angebot als erledigt (Häkchen) mit Auftragsnummer
    html = client.get("/belege/uebersicht/vk", params={"art": "angebot"}).text
    assert "Auftrag erhalten" in html
    assert f"/belege/detail/{auftrag_id}" in html


def test_auftrag_nicht_erhalten(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    zurueck = "/belege/uebersicht/vk?art=angebot"

    r = client.post(
        f"/belege/detail/{angebot_id}/auftrag-nicht-erhalten",
        data={"zurueck": zurueck}, follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == zurueck
    with Session(engine) as session:
        angebot = session.get(Beleg, angebot_id)
        assert angebot.status == BelegStatus.ERLEDIGT
        assert angebot.angebotsergebnis == "nicht_erhalten"
        assert angebot.gedruckt_am is None  # nie gedruckt, direkt erledigt

    html = client.get("/belege/uebersicht/vk", params={"art": "angebot"}).text
    assert "Nicht erhalten" in html
    # Kein Folgebeleg mehr aus einem verlorenen Angebot
    with Session(engine) as session:
        angebot = session.get(Beleg, angebot_id)
        try:
            beleg_service.angebot_erhalten(session, angebot)
        except UngueltigerStatusUebergang:
            pass
        else:
            raise AssertionError("verlorenes Angebot darf keinen Auftrag mehr erzeugen")


def test_auftrag_erhalten_nur_fuer_angebote(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    auftrag_id = _beleg(engine, Belegart.AUFTRAG, adresse_id)
    r = client.post(f"/belege/detail/{auftrag_id}/auftrag-erhalten", follow_redirects=False)
    assert r.status_code == 302 and "fehler=" in r.headers["location"]
    assert client.get(f"/belege/detail/{auftrag_id}/auftrag-erhalten-dialog").status_code == 409


# --------------------------------------------------------------- Beleg löschen


def test_entwurf_loeschen_mit_positionen_und_zeiten(engine, client):
    benutzer_id = _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg(engine, Belegart.AUFTRAG, adresse_id)
    _mit_position(engine, beleg_id, artikel_id)
    with Session(engine) as session:
        session.add(Zeiterfassung(beleg_id=beleg_id, benutzer_id=benutzer_id, stunden=Decimal("2")))
        session.commit()

    r = client.post(f"/belege/detail/{beleg_id}/loeschen", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/belege/uebersicht/vk?art=auftrag"
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id) is None
        rest = session.exec(select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)).all()
        assert rest == []
        zeit = session.exec(select(Zeiterfassung)).one()
        assert zeit.beleg_id is None  # gelöst, nicht gelöscht
    assert client.get(f"/belege/detail/{beleg_id}").status_code == 404


def test_gebuchter_beleg_nicht_loeschbar(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _mit_position(engine, beleg_id, artikel_id)
    _status(engine, beleg_id, BelegStatus.GEDRUCKT)

    r = client.post(f"/belege/detail/{beleg_id}/loeschen", follow_redirects=False)
    assert r.status_code == 302 and "fehler=" in r.headers["location"]
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id) is not None
        try:
            beleg_service.loesche_entwurf(session, session.get(Beleg, beleg_id))
        except BelegGesperrt:
            pass
        else:
            raise AssertionError("gebuchter Beleg darf nicht gelöscht werden")


# ------------------------------------------------------ Einstellungen: Nummern


def test_einstellungen_nummernkreise(engine, client):
    _login(engine, client, login="admin", rolle=Rolle.ADMIN)
    adresse_id, _ = _stammdaten(engine)
    _beleg(engine, Belegart.ANGEBOT, adresse_id)  # 260001 vergeben

    html = client.get("/einstellungen").text
    assert f"Nummernkreise {JAHR}" in html
    assert 'name="stand_angebot"' in html
    assert f"{JJ}0002" in html  # nächste Angebotsnummer

    # Anschluss an Kplus: Angebote ab 23032 weiterzählen, Rechnungen ab 5001
    r = client.post(
        "/einstellungen/nummernkreise",
        data={"stand_angebot": "23031", "stand_rechnung": "5000"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "nk_gespeichert=1" in r.headers["location"]
    neu_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    re_id = _beleg(engine, Belegart.RECHNUNG, adresse_id)
    with Session(engine) as session:
        assert session.get(Beleg, neu_id).belegnr == f"{JJ}23032"
        assert session.get(Beleg, re_id).belegnr == f"{JJ}5001"

    # Zurücksetzen unter den Stand → Fehler, nichts geändert
    r = client.post(
        "/einstellungen/nummernkreise", data={"stand_angebot": "5"}, follow_redirects=False
    )
    assert "nk_fehler=" in r.headers["location"]
    r = client.post(
        "/einstellungen/nummernkreise", data={"stand_angebot": "abc"}, follow_redirects=False
    )
    assert "nk_fehler=" in r.headers["location"]


def test_nummernkreise_nur_admin(engine, client):
    _login(engine, client)
    r = client.post(
        "/einstellungen/nummernkreise", data={"stand_angebot": "9"}, follow_redirects=False
    )
    assert r.status_code in (302, 303, 403)
    assert "nk_gespeichert" not in r.headers.get("location", "")


def test_gleiche_nummer_in_verschiedenen_belegarten(engine):
    """Kplus-Nummernlogik: Angebot 260001 und Rechnung 260001 nebeneinander."""
    adresse_id, _ = _stammdaten(engine)
    a = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    r = _beleg(engine, Belegart.RECHNUNG, adresse_id)
    with Session(engine) as session:
        assert session.get(Beleg, a).belegnr == session.get(Beleg, r).belegnr == f"{JJ}0001"
