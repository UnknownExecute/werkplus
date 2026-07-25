"""Tests für die M5-Anlagenverwaltung: Bestandsanlagen, Anlage aus Lieferschein,
Wartungsfilter, Serviceeinsätze und Suche."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Anlage,
    Artikel,
    Lager,
    Lagerbestand,
    Seriennummer,
    Serviceeinsatz,
    SnStatus,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import beleg_service
from app.services.auth_service import hash_passwort
from app.services.buchung import registriere_lager_hooks


@pytest.fixture(autouse=True)
def _lager_hooks():
    """Globale Buchungs-Hooks je Test frisch registrieren und danach aufräumen.

    Andere Testmodule leeren die Hook-Registry in ihrem Teardown — Registrierung
    aus app.main ist daher nicht verlässlich (Muster aus test_lager_ui).
    """
    registriere_lager_hooks()
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


def _login(engine, client, login="tester", passwort="geheim", rolle=Rolle.SACHBEARBEITER):
    """Legt einen Benutzer an und meldet den Client an."""
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
    r = client.post("/login", data={"login": login, "passwort": passwort}, follow_redirects=False)
    assert r.status_code == 302


def _stammdaten(engine) -> dict:
    """Standardlager, Kunde und ein SN-pflichtiger Lagerartikel (Bestand 3, 3 SNs)."""
    with Session(engine) as session:
        lager = Lager(name="Hauptlager", ist_standard=True)
        kunde = Adresse(typ=AdressTyp.KUNDE, firma="Muster GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="HZ-1",
            bezeichnung="Gastherme Testino",
            einheit="Stk",
            vk_preis=Decimal("100.00"),
            ust_satz=20,
            seriennummernpflichtig=True,
            ist_lagerartikel=True,
        )
        session.add(lager)
        session.add(kunde)
        session.add(artikel)
        session.flush()
        session.add(Lagerbestand(artikel_id=artikel.id, lager_id=lager.id, menge=Decimal("3")))
        sn_ids = []
        for i in range(1, 4):
            sn = Seriennummer(
                artikel_id=artikel.id,
                sn=f"SN-{i:04d}",
                status=SnStatus.AUF_LAGER,
                lager_id=lager.id,
            )
            session.add(sn)
            session.flush()
            sn_ids.append(sn.id)
        session.commit()
        return {
            "lager_id": lager.id,
            "kunde_id": kunde.id,
            "artikel_id": artikel.id,
            "sn_ids": sn_ids,
        }


def _beleg_anlegen(client, adresse_id: int, belegart: str) -> int:
    r = client.post(
        f"/belege/{belegart}/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    assert r.status_code == 302
    return int(r.headers["location"].rsplit("/", 1)[-1])


def _lieferschein_buchen(client, engine, daten: dict, sn_ids: list[int]) -> int:
    """Lieferschein mit einer Position (Menge = len(sn_ids)) anlegen, SNs zuweisen, buchen."""
    from app.models import BelegPosition

    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": daten["artikel_id"]}
    )
    assert r.status_code == 200
    with Session(engine) as session:
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).one()
        position_id = position.id
        bezeichnung = position.bezeichnung
    if len(sn_ids) != 1:
        r = client.post(
            f"/belege/detail/{beleg_id}/positionen/{position_id}",
            data={
                "bezeichnung": bezeichnung,
                "menge": str(len(sn_ids)),
                "einheit": "Stk",
                "einzelpreis": "100",
                "rabatt_prozent": "0",
                "ust_satz": "20",
            },
        )
        assert r.status_code == 200
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-zuweisen",
        data={"seriennummer_ids": sn_ids},
    )
    assert r.status_code == 200
    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt", follow_redirects=False)
    assert r.status_code == 302
    assert "fehler=" not in r.headers["location"]
    return beleg_id


def _anlage_direkt(
    engine,
    *,
    adresse_id: int,
    artikel_id: int,
    sn_text: str,
    naechste_wartung: date | None = None,
    standort: str = "",
) -> int:
    """Legt SN + Anlage direkt in der Datenbank an (Abkürzung für Filter-/Suchtests)."""
    with Session(engine) as session:
        sn = Seriennummer(
            artikel_id=artikel_id, sn=sn_text, status=SnStatus.BEIM_KUNDEN_VERBAUT
        )
        session.add(sn)
        session.flush()
        anlage = Anlage(
            adresse_id=adresse_id,
            artikel_id=artikel_id,
            seriennummer_id=sn.id,
            naechste_wartung=naechste_wartung,
            standort=standort,
        )
        session.add(anlage)
        session.commit()
        return anlage.id


def test_bestandsanlage_anlegen(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)

    r = client.post(
        "/anlagen/neu",
        data={
            "adresse_id": daten["kunde_id"],
            "artikel_id": daten["artikel_id"],
            "seriennummer_id": "",
            "sn_text": "ALT-0001",
            "standort": "Heizraum Keller",
            "inbetriebnahme": "2020-05-01",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    with Session(engine) as session:
        sn = session.exec(select(Seriennummer).where(Seriennummer.sn == "ALT-0001")).one()
        assert sn.status == SnStatus.BEIM_KUNDEN_VERBAUT
        assert sn.lager_id is None
        anlage = session.exec(select(Anlage).where(Anlage.seriennummer_id == sn.id)).one()
        assert anlage.adresse_id == daten["kunde_id"]
        assert anlage.inbetriebnahme == date(2020, 5, 1)

    r = client.get("/anlagen")
    assert r.status_code == 200
    assert "ALT-0001" in r.text
    assert "Muster GmbH" in r.text
    assert "Heizraum Keller" in r.text


def test_doppelte_sn_zeigt_fehler_im_formular(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    form = {
        "adresse_id": daten["kunde_id"],
        "artikel_id": daten["artikel_id"],
        "seriennummer_id": "",
        "sn_text": "ALT-0001",
    }
    r = client.post("/anlagen/neu", data=form, follow_redirects=False)
    assert r.status_code == 302

    r = client.post("/anlagen/neu", data=form, follow_redirects=False)
    assert r.status_code == 200  # kein 500 — Formular mit Fehlermeldung
    assert "existiert bereits" in r.text
    with Session(engine) as session:
        assert len(session.exec(select(Anlage)).all()) == 1


def test_weder_noch_beide_sn_angaben_zeigt_fehler(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    r = client.post(
        "/anlagen/neu",
        data={
            "adresse_id": daten["kunde_id"],
            "artikel_id": daten["artikel_id"],
            "seriennummer_id": "",
            "sn_text": "",
        },
    )
    assert r.status_code == 200
    assert "genau eines von beiden" in r.text


def test_anlage_aus_gebuchtem_lieferschein(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _lieferschein_buchen(client, engine, daten, daten["sn_ids"][:2])

    # Kandidaten-Formular zeigt beide zugewiesenen SNs
    r = client.get("/anlagen/neu", params={"von_beleg": beleg_id})
    assert r.status_code == 200
    assert "SN-0001" in r.text
    assert "SN-0002" in r.text
    assert "Muster GmbH" in r.text

    # Anlage für die erste SN anlegen (Inbetriebnahme leer → Belegdatum)
    r = client.post(
        "/anlagen/neu",
        data={
            "von_beleg": beleg_id,
            "seriennummer_id": daten["sn_ids"][0],
            "standort": "Dachboden",
            "inbetriebnahme": "",
            "naechste_wartung": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == f"/anlagen/neu?von_beleg={beleg_id}"

    with Session(engine) as session:
        sn = session.get(Seriennummer, daten["sn_ids"][0])
        assert sn.status == SnStatus.BEIM_KUNDEN_VERBAUT
        anlage = session.exec(
            select(Anlage).where(Anlage.seriennummer_id == sn.id)
        ).one()
        assert anlage.adresse_id == daten["kunde_id"]
        assert anlage.inbetriebnahme == date.today()  # Belegdatum als Default
        assert anlage.standort == "Dachboden"

    # Kandidat verschwindet, die zweite SN bleibt übrig
    r = client.get("/anlagen/neu", params={"von_beleg": beleg_id})
    assert r.status_code == 200
    assert "SN-0001" not in r.text
    assert "SN-0002" in r.text


def test_von_beleg_verlangt_gebuchten_lieferschein(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")  # bleibt Entwurf
    r = client.get("/anlagen/neu", params={"von_beleg": beleg_id})
    assert r.status_code == 404


def test_wartungsfilter_zeigt_faellige_und_bald_faellige(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    heute = date.today()
    _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="W-UEBERFAELLIG",
        naechste_wartung=heute - timedelta(days=5),
    )
    _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="W-BALD",
        naechste_wartung=heute + timedelta(days=10),
    )
    _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="W-FERN",
        naechste_wartung=heute + timedelta(days=90),
    )

    r = client.get("/anlagen", params={"wartung": "faellig"})
    assert r.status_code == 200
    assert "W-UEBERFAELLIG" in r.text
    assert "W-BALD" in r.text
    assert "W-FERN" not in r.text
    assert "table-danger" in r.text  # überfällig rot
    assert "table-warning" in r.text  # bald fällig gelb

    r = client.get("/anlagen")
    assert "W-FERN" in r.text  # ohne Filter alles sichtbar


def test_serviceeinsatz_aktualisiert_historie_und_wartungstermin(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    anlage_id = _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="SRV-0001",
        naechste_wartung=date(2026, 8, 1),
    )

    r = client.get(f"/anlagen/{anlage_id}")
    assert r.status_code == 200
    assert "Noch keine Serviceeinsätze" in r.text

    r = client.post(
        f"/anlagen/{anlage_id}/serviceeinsatz",
        data={
            "datum": "2026-07-20",
            "beschreibung": "Brenner getauscht",
            "naechste_wartung": "2027-07-20",
        },
    )
    assert r.status_code == 200
    assert "Brenner getauscht" in r.text
    assert "Tester" in r.text  # erfassender Benutzer in der Historie

    with Session(engine) as session:
        anlage = session.get(Anlage, anlage_id)
        assert anlage.naechste_wartung == date(2027, 7, 20)
        einsatz = session.exec(select(Serviceeinsatz)).one()
        assert einsatz.datum == date(2026, 7, 20)
        assert einsatz.benutzer_id is not None

    # Ohne neuen Wartungstermin bleibt der bestehende erhalten
    r = client.post(
        f"/anlagen/{anlage_id}/serviceeinsatz",
        data={"datum": "2026-07-21", "beschreibung": "Nachkontrolle", "naechste_wartung": ""},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        assert session.get(Anlage, anlage_id).naechste_wartung == date(2027, 7, 20)
        assert len(session.exec(select(Serviceeinsatz)).all()) == 2


def test_serviceeinsatz_oob_aktualisiert_wartungsfeld(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    anlage_id = _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="OOB-0001",
        naechste_wartung=date(2026, 8, 1),
    )

    r = client.post(
        f"/anlagen/{anlage_id}/serviceeinsatz",
        data={
            "datum": "2026-07-20",
            "beschreibung": "Jahreswartung",
            "naechste_wartung": "2027-09-15",
        },
    )
    assert r.status_code == 200
    # Historie-Haupt-Swap enthält den Einsatz
    assert "Jahreswartung" in r.text
    # Out-of-Band-Fragment aktualisiert das Wartungsfeld im Stammdatenformular
    assert 'hx-swap-oob="true"' in r.text
    assert 'id="stamm-naechste-wartung"' in r.text
    assert "2027-09-15" in r.text

    with Session(engine) as session:
        assert session.get(Anlage, anlage_id).naechste_wartung == date(2027, 9, 15)


def test_anlagen_suche_filtert(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="SUCHE-A",
        standort="Heizraum",
    )
    _anlage_direkt(
        engine,
        adresse_id=daten["kunde_id"],
        artikel_id=daten["artikel_id"],
        sn_text="SUCHE-B",
        standort="Dachboden",
    )

    r = client.get("/anlagen/suche", params={"q": "Heizraum"})
    assert r.status_code == 200
    assert "SUCHE-A" in r.text
    assert "SUCHE-B" not in r.text

    r = client.get("/anlagen/suche", params={"q": "gibtsnicht"})
    assert r.status_code == 200
    assert "Keine Anlagen gefunden" in r.text
