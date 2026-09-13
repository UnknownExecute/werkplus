"""Artikelstamm im Kplus-Stil: Matchcode/EAN-Suche, Filter, Lieferanten je Artikel,
Staffelpreise (auch im Beleg), reserviert/bestellt, letzter EK aus der EK-Rechnung."""

from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Artikel,
    ArtikelLieferant,
    ArtikelStaffel,
    Beleg,
    BelegPosition,
    BelegStatus,
    Lager,
    Lagerbestand,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import artikel_service, beleg_service
from app.services.auth_service import hash_passwort
from app.services.buchung import registriere_lager_hooks


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
    "artikelnr": "RO-1",
    "matchcode": "KUPFER22",
    "ean": "9001234567890",
    "alternativnr": "CU-22-5",
    "hersteller": "Wieland",
    "bezeichnung": "Kupferrohr 22mm",
    "warengruppe": "Installation",
    "unterwarengruppe": "Rohr",
    "einheit": "m",
    "ust_satz": "20",
    "listenpreis_netto": "10",
    "rabatt1_prozent": "0",
    "rabatt2_prozent": "0",
    "aufschlag_prozent": "50",
    "vk_preis": "0",
    "mindest_vk": "12",
    "mindestbestand": "20",
    "bemerkung": "nur Stangenware",
    "ist_lagerartikel": "true",
    "aktiv": "true",
}


def _artikel(client, **extra) -> int:
    r = client.post("/artikel/neu", data={**FORM, **extra}, follow_redirects=False)
    assert r.status_code == 302, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


def _lieferant(engine, firma="Lieferant AG") -> int:
    with Session(engine) as session:
        a = Adresse(typ=AdressTyp.LIEFERANT, firma=firma, ort="Wien")
        session.add(a)
        session.commit()
        return a.id


def _kunde(engine) -> int:
    with Session(engine) as session:
        a = Adresse(typ=AdressTyp.KUNDE, firma="Kunde GmbH", ort="Lienz")
        session.add(a)
        session.commit()
        return a.id


# ------------------------------------------------------------ Stammdaten


def test_neuer_artikel_mit_kplus_feldern(engine, client):
    _login(engine, client)
    a = _artikel(client)
    with Session(engine) as session:
        art = session.get(Artikel, a)
        assert (art.matchcode, art.ean) == ("KUPFER22", "9001234567890")
        assert art.alternativnr == "CU-22-5"
        assert (art.hersteller, art.unterwarengruppe, art.bemerkung) == (
            "Wieland", "Rohr", "nur Stangenware"
        )
        assert art.mindest_vk == Decimal("12") and art.mindestbestand == Decimal("20")
        assert art.vk_preis == Decimal("15.00")  # 10 + 50 %
        assert art.letzte_preisaenderung is None
    html = client.get(f"/artikel/{a}").text
    for reiter in ("Stammdaten", "Lieferanten", "Staffelpreise", "Bestand"):
        assert reiter in html
    assert "KUPFER22" in html and "EAN 9001234567890" in html

    # Preisänderung wird datiert
    r = client.post(f"/artikel/{a}", data={**FORM, "vk_preis": "16"}, follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        art = session.get(Artikel, a)
        assert art.vk_preis == Decimal("16") and art.letzte_preisaenderung == date.today()


def test_suche_ueber_matchcode_ean_alternativnr_und_filter(engine, client):
    _login(engine, client)
    lieferant_id = _lieferant(engine)
    a = _artikel(client)
    b = _artikel(client, artikelnr="DL-1", matchcode="STUNDE", ean="", alternativnr="",
                 bezeichnung="Montagestunde", warengruppe="Dienstleistung", ist_lagerartikel="")
    with Session(engine) as session:
        artikel_service.setze_lieferant(session, session.get(Artikel, a), lieferant_id)
        session.commit()

    for q in ("KUPFER", "900123456", "CU-22", "Kupferrohr"):
        r = client.get("/artikel/suche", params={"q": q})
        assert f"/artikel/{a}" in r.text and f"/artikel/{b}" not in r.text, q
    r = client.get("/artikel/suche", params={"artikelart": "dienstleistung"})
    assert f"/artikel/{b}" in r.text and f"/artikel/{a}" not in r.text
    r = client.get("/artikel/suche", params={"artikelart": "lager"})
    assert f"/artikel/{a}" in r.text and f"/artikel/{b}" not in r.text
    r = client.get("/artikel/suche", params={"warengruppe": "Installation"})
    assert f"/artikel/{a}" in r.text and f"/artikel/{b}" not in r.text
    r = client.get("/artikel/suche", params={"lieferant_id": str(lieferant_id)})
    assert f"/artikel/{a}" in r.text and f"/artikel/{b}" not in r.text
    # Liste zeigt Filter und Bestandsspalten
    html = client.get("/artikel").text
    assert "Lieferant AG" in html and "Reserv." in html and "Bestellt" in html
    # globale Suche kennt Matchcode
    assert "Kupferrohr 22mm" in client.get("/suche", params={"q": "KUPFER22"}).text


# ------------------------------------------------------------ Lieferanten


def test_lieferanten_je_artikel_haupt_und_loeschen(engine, client):
    _login(engine, client)
    l1, l2 = _lieferant(engine, "Erster"), _lieferant(engine, "Zweiter")
    a = _artikel(client)
    r = client.post(
        f"/artikel/{a}/lieferanten",
        data={"adresse_id": l1, "lieferanten_artikelnr": "L-111", "ek_preis": "9,50",
              "lieferzeit_tage": "3"},
    )
    assert r.status_code == 200 and "L-111" in r.text and "Erster" in r.text
    r = client.post(
        f"/artikel/{a}/lieferanten",
        data={"adresse_id": l2, "lieferanten_artikelnr": "Z-222", "ek_preis": "9,20",
              "ist_hauptlieferant": "true"},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        eintraege = artikel_service.lieferanten(session, a)
        assert [(e.adresse_id, e.ist_hauptlieferant) for e in eintraege] == [
            (l2, True), (l1, False)
        ]
        art = session.get(Artikel, a)
        assert artikel_service.ek_fuer_lieferant(session, art, l1) == Decimal("9.50")
        assert artikel_service.ek_fuer_lieferant(session, art, 999) == Decimal("10")
        erster = next(e for e in eintraege if e.adresse_id == l1)
        zweiter = next(e for e in eintraege if e.adresse_id == l2)
    # gleicher Lieferant nochmal → überschreibt statt doppelt
    client.post(f"/artikel/{a}/lieferanten", data={"adresse_id": l1, "ek_preis": "9,00"})
    with Session(engine) as session:
        assert len(artikel_service.lieferanten(session, a)) == 2
        assert artikel_service.lieferant_eintrag(session, a, l1).ek_preis == Decimal("9.00")
    # Haupt umsetzen, Haupt löschen → anderer wird Haupt
    client.post(f"/artikel/{a}/lieferanten/{erster.id}/haupt")
    r = client.post(f"/artikel/{a}/lieferanten/{erster.id}/loeschen")
    assert f'href="/adressen/{l1}"' not in r.text  # Zeile weg (Auswahlliste bleibt)
    assert f'href="/adressen/{l2}"' in r.text
    with Session(engine) as session:
        assert session.get(ArtikelLieferant, zweiter.id).ist_hauptlieferant is True
    # Kunde ist kein Lieferant → 422
    kunde = _kunde(engine)
    r = client.post(f"/artikel/{a}/lieferanten", data={"adresse_id": kunde})
    assert r.status_code == 422


def test_bestellung_nimmt_lieferanten_ek(engine, client):
    _login(engine, client)
    lieferant_id = _lieferant(engine)
    a = _artikel(client)
    with Session(engine) as session:
        artikel_service.setze_lieferant(
            session, session.get(Artikel, a), lieferant_id, ek_preis=Decimal("8.80")
        )
        session.commit()
    r = client.post("/belege/bestellung/neu", data={"adresse_id": lieferant_id},
                    follow_redirects=False)
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": a})
    with Session(engine) as session:
        pos = session.exec(select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)).one()
        assert pos.einzelpreis == Decimal("8.80")  # nicht der VK, nicht der Stamm-EK
    # anderer Lieferant ohne Eintrag → Stamm-EK
    anderer = _lieferant(engine, "Anderer")
    r = client.post("/belege/bestellung/neu", data={"adresse_id": anderer}, follow_redirects=False)
    beleg2 = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/belege/detail/{beleg2}/positionen", data={"artikel_id": a})
    with Session(engine) as session:
        pos = session.exec(select(BelegPosition).where(BelegPosition.beleg_id == beleg2)).one()
        assert pos.einzelpreis == Decimal("10")


def test_ek_rechnung_schreibt_letzten_ek_fort(engine, client):
    _login(engine, client)
    registriere_lager_hooks()
    lieferant_id = _lieferant(engine)
    a = _artikel(client)
    r = client.post(
        "/belege/ek_rechnung/neu",
        data={"adresse_id": lieferant_id, "datum": "2026-09-10"},
        follow_redirects=False,
    )
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": a})
    with Session(engine) as session:
        pos = session.exec(select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)).one()
        pos.einzelpreis = Decimal("8.40")
        session.add(pos)
        session.commit()
    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt", follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        art = session.get(Artikel, a)
        assert art.letzter_ek == Decimal("8.40")
        assert art.letzter_lieferant_id == lieferant_id and art.letzter_ek_am == date(2026, 9, 10)
        eintrag = artikel_service.lieferant_eintrag(session, a, lieferant_id)
        assert eintrag is not None and eintrag.ek_preis == Decimal("8.40")
        assert eintrag.ist_hauptlieferant is True
    html = client.get(f"/artikel/{a}").text
    assert "8,40" in html and "Lieferant AG" in html and "10.09.2026" in html


# ------------------------------------------------------------ Staffelpreise


def test_staffelpreise_crud_und_im_beleg(engine, client):
    _login(engine, client)
    kunde = _kunde(engine)
    a = _artikel(client)
    r = client.post(f"/artikel/{a}/staffeln", data={"ab_menge": "50", "vk_preis": "13,50"})
    assert r.status_code == 200 and "ab 50" in r.text
    client.post(f"/artikel/{a}/staffeln", data={"ab_menge": "100", "vk_preis": "12"})
    client.post(f"/artikel/{a}/staffeln", data={"ab_menge": "50", "vk_preis": "13"})  # überschreibt
    r = client.post(f"/artikel/{a}/staffeln", data={"ab_menge": "0", "vk_preis": "1"})
    assert r.status_code == 422
    with Session(engine) as session:
        art = session.get(Artikel, a)
        assert [(s.ab_menge, s.vk_preis) for s in artikel_service.staffeln(session, a)] == [
            (Decimal("50"), Decimal("13")), (Decimal("100"), Decimal("12"))
        ]
        assert artikel_service.staffelpreis(session, art, Decimal("10")) == Decimal("15.00")
        assert artikel_service.staffelpreis(session, art, Decimal("50")) == Decimal("13")
        assert artikel_service.staffelpreis(session, art, Decimal("250")) == Decimal("12")
        staffel_50 = artikel_service.staffeln(session, a)[0].id

    # Im Angebot: Menge 60 beim Einfügen → Staffelpreis; Mengenänderung zieht die Staffel nach
    r = client.post("/belege/angebot/neu", data={"adresse_id": kunde}, follow_redirects=False)
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        pos = beleg_service.fuege_position_hinzu(
            session, beleg, session.get(Artikel, a), menge=Decimal("60")
        )
        session.commit()
        assert pos.einzelpreis == Decimal("13")
        pos_id = pos.id
    daten = {"bezeichnung": "Kupferrohr 22mm", "menge": "120", "einheit": "m", "listenpreis": "10",
             "rabatt1_prozent": "0", "rabatt2_prozent": "0", "aufschlag_prozent": "30",
             "einzelpreis": "13", "rabatt_prozent": "0", "ust_satz": "20"}
    r = client.post(f"/belege/detail/{beleg_id}/positionen/{pos_id}", data=daten,
                    headers={"HX-Trigger": f"pd-menge-{pos_id}"})
    assert r.status_code == 200
    with Session(engine) as session:
        assert session.get(BelegPosition, pos_id).einzelpreis == Decimal("12")
    # manuell gesetzter Preis bleibt bei Mengenänderung erhalten
    r = client.post(f"/belege/detail/{beleg_id}/positionen/{pos_id}",
                    data={**daten, "menge": "5", "einzelpreis": "11"},
                    headers={"HX-Trigger": f"pd-menge-{pos_id}"})
    with Session(engine) as session:
        assert session.get(BelegPosition, pos_id).einzelpreis == Decimal("11")
    # Staffel löschen
    r = client.post(f"/artikel/{a}/staffeln/{staffel_50}/loeschen")
    assert "ab 50" not in r.text
    with Session(engine) as session:
        rest = session.exec(select(ArtikelStaffel).where(ArtikelStaffel.id == staffel_50)).first()
        assert rest is None


# ------------------------------------------------- Reserviert / Bestellt


def test_reserviert_und_bestellt(engine, client):
    _login(engine, client)
    kunde, lieferant_id = _kunde(engine), _lieferant(engine)
    a = _artikel(client)
    with Session(engine) as session:
        lager = Lager(name="Hauptlager")
        session.add(lager)
        session.flush()
        session.add(Lagerbestand(artikel_id=a, lager_id=lager.id, menge=Decimal("100")))
        session.commit()

    # Auftrag über 30 gedruckt → reserviert 30; Bestellung über 40 gedruckt → bestellt 40
    def _beleg(art, adresse_id, menge):
        r = client.post(
            f"/belege/{art}/neu", data={"adresse_id": adresse_id}, follow_redirects=False
        )
        beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
        with Session(engine) as session:
            beleg = session.get(Beleg, beleg_id)
            beleg_service.fuege_position_hinzu(session, beleg, session.get(Artikel, a), menge=menge)
            beleg_service.setze_status(session, beleg, BelegStatus.GEDRUCKT)
            session.commit()
        return beleg_id

    auftrag_id = _beleg("auftrag", kunde, Decimal("30"))
    _beleg("bestellung", lieferant_id, Decimal("40"))
    with Session(engine) as session:
        assert artikel_service.reserviert(session, a) == Decimal("30")
        assert artikel_service.bestellt(session, a) == Decimal("40")
    html = client.get(f"/artikel/{a}").text
    assert "Reserviert" in html and "Verfügbar" in html
    zeilen = client.get("/artikel/suche", params={"q": "RO-1"}).text
    assert ">30<" in zeilen and ">40<" in zeilen and ">70<" in zeilen  # Gesamt 100 − 30

    # Teillieferung 10 → reserviert 20; volle Lieferung → 0
    with Session(engine) as session:
        auftrag = session.get(Beleg, auftrag_id)
        pos = session.exec(select(BelegPosition).where(BelegPosition.beleg_id == auftrag_id)).one()
        beleg_service.erzeuge_folgebeleg(session, auftrag, mengen={pos.id: Decimal("10")})
        session.commit()
        assert artikel_service.reserviert(session, a) == Decimal("20")
        auftrag = session.get(Beleg, auftrag_id)
        beleg_service.erzeuge_folgebeleg(session, auftrag)
        session.commit()
        assert artikel_service.reserviert(session, a) == Decimal("0")
