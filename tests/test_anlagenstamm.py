"""Anlagenstamm im Kplus-Stil: freie Anlagen (ohne Lagerartikel), Anlagenummer,
Anlagensuche mit Filtern, Komponenten, Wartungsvertrag, Wartungsintervall,
Auftrag aus Anlage, Anlagenblatt, Belege-Reiter."""

from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Anlage,
    AnlageKomponente,
    Ansprechpartner,
    Artikel,
    Beleg,
    Belegart,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import anlagen_service
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


def _kunde(engine, firma="Hotel Alpenblick GmbH") -> int:
    with Session(engine) as session:
        a = Adresse(typ=AdressTyp.KUNDE, firma=firma, ort="Innsbruck", adressnr="10001")
        session.add(a)
        session.commit()
        session.add(Ansprechpartner(adresse_id=a.id, nachname="Maier", ist_hauptkontakt=True))
        session.commit()
        return a.id


FREI = {
    "bezeichnung": "Ölkessel Viessmann Vitola 200\nHeizzentrale",
    "kostenstelle": "HZ-02",
    "hersteller": "Viessmann",
    "typ": "Vitola 200",
    "seriennummer_text": "7183 5546",
    "standort": "Nebengebäude",
    "technische_info": "27 kW",
    "art": "Heizung",
    "anlagengruppe": "Wärmeerzeuger",
    "untergruppe": "Öl",
    "herstellungsdatum": "2009-05-01",
    "inbetriebnahme": "2009-10-15",
    "garantieende": "2011-10-15",
    "naechste_wartung": "",
    "wartungsintervall_monate": "12",
}


def _freie_anlage(client, kunde_id, **extra) -> int:
    r = client.post("/anlagen/neu", data={"adresse_id": kunde_id, **FREI, **extra},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


# ------------------------------------------------------------ Freie Anlage


def test_freie_anlage_ohne_artikel_mit_kplus_feldern(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    with Session(engine) as session:
        anlage = session.get(Anlage, a)
        assert anlage.artikel_id is None and anlage.seriennummer_id is None
        assert anlage.anlagenr == "1001"
        assert anlage.bezeichnung.startswith("Ölkessel")
        assert anlage.kurz == "Ölkessel Viessmann Vitola 200 Vitola 200"
        assert (anlage.hersteller, anlage.typ, anlage.seriennummer_text) == (
            "Viessmann", "Vitola 200", "7183 5546"
        )
        assert (anlage.art, anlage.anlagengruppe) == ("Heizung", "Wärmeerzeuger")
        assert anlage.untergruppe == "Öl"
        assert anlage.kostenstelle == "HZ-02" and anlage.herstellungsdatum == date(2009, 5, 1)
        assert anlage.wartungsintervall_monate == 12
    b = _freie_anlage(client, kunde_id, bezeichnung="Zweite")
    with Session(engine) as session:
        assert session.get(Anlage, b).anlagenr == "1002"

    html = client.get(f"/anlagen/{a}").text
    for reiter in ("Anlagedaten", "Wartung", "Komponenten", "Wartungsvertrag", "Belege"):
        assert reiter in html
    assert "1001" in html and "Viessmann" in html and "Auftrag erstellen" in html
    # ohne Bezeichnung und ohne Artikel → Fehler im Formular
    r = client.post("/anlagen/neu", data={"adresse_id": kunde_id, "hersteller": "X"})
    assert "Anlagebezeichnung" in r.text


def test_anlage_mit_lagerartikel_uebernimmt_bezeichnung_und_sn(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    with Session(engine) as session:
        artikel = Artikel(artikelnr="HZ-1", bezeichnung="Gastherme Testino", hersteller="Vaillant",
                          seriennummernpflichtig=True, vk_preis=Decimal("1000"))
        session.add(artikel)
        session.commit()
        artikel_id = artikel.id
    r = client.post(
        "/anlagen/neu",
        data={"adresse_id": kunde_id, "artikel_id": artikel_id, "sn_text": "GT-4711",
              "standort": "Keller"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    a = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        anlage = session.get(Anlage, a)
        assert anlage.artikel_id == artikel_id and anlage.seriennummer_id is not None
        assert anlage.bezeichnung == "Gastherme Testino"
        assert anlage.hersteller == "Vaillant" and anlage.seriennummer_text == "GT-4711"


def test_bearbeiten_speichert_alle_kplus_felder(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    with Session(engine) as session:
        kontakt_id = session.exec(select(Ansprechpartner)).one().id
        lieferant = Adresse(typ=AdressTyp.LIEFERANT, firma="Viessmann Werke")
        session.add(lieferant)
        session.commit()
        lieferant_id = lieferant.id
    r = client.post(
        f"/anlagen/{a}",
        data={**FREI, "typ": "Vitola 222", "ansprechpartner_id": str(kontakt_id),
              "lieferant_id": str(lieferant_id), "aktiv": "true", "tab": "daten"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"].endswith("?tab=daten")
    with Session(engine) as session:
        anlage = session.get(Anlage, a)
        assert anlage.typ == "Vitola 222"
        assert anlage.ansprechpartner_id == kontakt_id and anlage.lieferant_id == lieferant_id
        assert anlage.aktiv is True
    html = client.get(f"/anlagen/{a}").text
    assert "Maier" in html and "Viessmann Werke" in html


# ------------------------------------------------------------ Anlagensuche


def test_anlagensuche_filter(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    heiz = _freie_anlage(client, kunde_id)
    klima = _freie_anlage(client, kunde_id, bezeichnung="Klimasplit Daikin", hersteller="Daikin",
                          typ="FTX", seriennummer_text="DK-99", art="Klima",
                          anlagengruppe="Klimageräte", kostenstelle="KL-01",
                          standort="Gastraum")
    with Session(engine) as session:
        anlage = session.get(Anlage, heiz)
        anlage.vertrag_nr = "WV-1"
        session.add(anlage)
        session.commit()

    def ids(**params):
        r = client.get("/anlagen/suche", params=params)
        assert r.status_code == 200
        return {i for i in (heiz, klima) if f'href="/anlagen/{i}"' in r.text}

    for q in ("1001", "Vitola", "Viessmann", "7183", "HZ-02", "Nebengeb"):
        assert ids(q=q) == {heiz}, q
    assert ids(q="Alpenblick") == {heiz, klima}  # beide beim selben Kunden
    assert ids(q="DK-99") == {klima}
    assert ids(art="Klima") == {klima}
    assert ids(gruppe="Wärmeerzeuger") == {heiz}
    assert ids(vertrag="1") == {heiz}
    assert ids() == {heiz, klima}
    html = client.get("/anlagen").text
    assert "Anlagengruppe" in html and "Nur mit Wartungsvertrag" in html
    # globale Suche kennt Anlagen
    assert "Vitola" in client.get("/suche", params={"q": "7183"}).text


# ------------------------------------------------------------ Komponenten


def test_komponenten_anlegen_und_loeschen(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    r = client.post(
        f"/anlagen/{a}/komponenten",
        data={"bezeichnung": "Ölbrenner", "hersteller": "Weishaupt", "typ": "WL10",
              "seriennummer_text": "WL10-88712", "einbaudatum": "2019-03-01", "notiz": "getauscht"},
    )
    assert r.status_code == 200 and "Ölbrenner" in r.text and "WL10-88712" in r.text
    client.post(f"/anlagen/{a}/komponenten", data={"bezeichnung": "Pumpe"})
    assert client.post(f"/anlagen/{a}/komponenten", data={"bezeichnung": " "}).status_code == 422
    with Session(engine) as session:
        komps = anlagen_service.komponenten(session, a)
        assert [k.bezeichnung for k in komps] == ["Ölbrenner", "Pumpe"]
        assert komps[0].einbaudatum == date(2019, 3, 1)
        brenner_id = komps[0].id
    r = client.post(f"/anlagen/{a}/komponenten/{brenner_id}/loeschen")
    assert "Ölbrenner" not in r.text and "Pumpe" in r.text
    # fremde Anlage → kein Löschen
    b = _freie_anlage(client, kunde_id, bezeichnung="Andere")
    with Session(engine) as session:
        pumpe_id = session.exec(select(AnlageKomponente)).one().id
    client.post(f"/anlagen/{b}/komponenten/{pumpe_id}/loeschen")
    with Session(engine) as session:
        assert session.get(AnlageKomponente, pumpe_id) is not None


# ------------------------------------------------------------ Wartung / Vertrag


def test_wartungsvertrag_und_intervall(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    r = client.post(
        f"/anlagen/{a}",
        data={**FREI, "vertrag_nr": "WV-2024-017", "vertrag_von": "2024-01-01",
              "vertrag_bis": "2026-12-31", "vertrag_pauschale": "290,00",
              "vertrag_notiz": "Jahreswartung inkl. Abgasmessung", "aktiv": "true",
              "tab": "vertrag"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        anlage = session.get(Anlage, a)
        assert anlage.hat_wartungsvertrag
        assert anlage.vertrag_pauschale == Decimal("290.00")
        assert anlage.vertrag_bis == date(2026, 12, 31)
    html = client.get(f"/anlagen/{a}").text
    assert "WV-2024-017" in html and "Wartungsvertrag</span>" in html

    # Serviceeinsatz ohne Datum für die nächste Wartung → Intervall greift (12 Monate)
    r = client.post(
        f"/anlagen/{a}/serviceeinsatz",
        data={"datum": "2026-09-13", "beschreibung": "Jahreswartung", "naechste_wartung": ""},
    )
    assert r.status_code == 200 and "Jahreswartung" in r.text
    with Session(engine) as session:
        assert session.get(Anlage, a).naechste_wartung == date(2027, 9, 13)
    # explizites Datum gewinnt
    client.post(
        f"/anlagen/{a}/serviceeinsatz",
        data={"datum": "2026-09-13", "beschreibung": "Nachbesserung",
              "naechste_wartung": "2027-03-01"},
    )
    with Session(engine) as session:
        assert session.get(Anlage, a).naechste_wartung == date(2027, 3, 1)
    assert anlagen_service.monate_spaeter(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert anlagen_service.monate_spaeter(date(2026, 11, 30), 3) == date(2027, 2, 28)


# ------------------------------------------------------------ Auftrag / Blatt


def test_auftrag_aus_anlage(engine, client):
    benutzer_id = _login(engine, client)
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    r = client.post(f"/anlagen/{a}/auftrag", data={"auftragsart": "service"},
                    follow_redirects=False)
    assert r.status_code == 302
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert beleg.belegart == Belegart.AUFTRAG and beleg.auftragsart == "service"
        assert beleg.adresse_id == kunde_id and beleg.anlage_id == a
        assert beleg.erstellt_von == benutzer_id
        assert beleg.betreff.startswith("Service Ölkessel")
        assert "Seriennummer: 7183 5546" in beleg.kopftext
        assert "Standort: Nebengebäude" in beleg.kopftext
    # Angebot aus Anlage
    r = client.post(
        f"/anlagen/{a}/auftrag", data={"belegart": "angebot", "betreff": "Kesseltausch"},
        follow_redirects=False,
    )
    angebot_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        angebot = session.get(Beleg, angebot_id)
        assert angebot.belegart == Belegart.ANGEBOT and angebot.auftragsart == ""
        assert angebot.betreff == "Kesseltausch" and angebot.anlage_id == a
    # Belege-Reiter der Anlage und Anlagenlink im Editor
    html = client.get(f"/anlagen/{a}?tab=belege").text
    assert "Kesseltausch" in html and f"/belege/detail/{beleg_id}" in html
    assert f"/anlagen/{a}" in client.get(f"/belege/detail/{beleg_id}").text


def test_auftrag_aus_anlage_braucht_verkaufsrecht(engine, client):
    from app.models import Rollenprofil, Zugriff

    with Session(engine) as session:
        session.add(Rollenprofil(name="nuranlagen", recht_anlagen=Zugriff.BEARBEITEN))
        session.commit()
    _login(engine, client, login="monteur", rolle="nuranlagen")
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    assert f'action="/anlagen/{a}/auftrag"' not in client.get(f"/anlagen/{a}").text
    assert client.post(f"/anlagen/{a}/auftrag").status_code == 403


def test_anlagenblatt(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    a = _freie_anlage(client, kunde_id)
    client.post(f"/anlagen/{a}/komponenten", data={"bezeichnung": "Ölbrenner", "typ": "WL10"})
    html = client.get(f"/anlagen/{a}/blatt").text
    assert "Anlagenblatt" in html and "1001" in html and "Ölbrenner" in html
    assert "Hotel Alpenblick" in html and "7183 5546" in html


def test_dashboard_und_kundenseite_zeigen_freie_anlage(engine, client):
    _login(engine, client)
    kunde_id = _kunde(engine)
    gestern = (date.today() - timedelta(days=1)).isoformat()
    a = _freie_anlage(client, kunde_id, naechste_wartung=gestern)
    html = client.get("/").text
    assert "Ölkessel Viessmann Vitola 200" in html and f"/anlagen/{a}" in html
    html = client.get(f"/adressen/{kunde_id}").text
    assert "1001" in html and "7183 5546" in html and "Vitola 200" in html
