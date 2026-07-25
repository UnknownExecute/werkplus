"""Tests für die M4-Lager-UI: SN-Dialog/-Erfassung, Buchung mit Lagerwirkung, Journal."""

from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Artikel,
    Beleg,
    BelegPosition,
    BelegStatus,
    Buchungsart,
    Lager,
    Lagerbestand,
    Lagerbewegung,
    Seriennummer,
    SnStatus,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import beleg_service
from app.services.auth_service import hash_passwort
from app.services.buchung import registriere_lager_hooks


@pytest.fixture(autouse=True)
def _lager_hooks():
    """Globale Buchungs-Hooks je Test frisch registrieren und danach aufräumen.

    Andere Testmodule (test_beleg_service, test_buchung) leeren die Hook-Registry
    in ihrem Teardown — Registrierung aus app.main ist daher nicht verlässlich.
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
    """Standardlager, Kunde, Lieferant und ein SN-pflichtiger Lagerartikel (Bestand 3, 3 SNs)."""
    with Session(engine) as session:
        lager = Lager(name="Hauptlager", ist_standard=True)
        kunde = Adresse(typ=AdressTyp.KUNDE, firma="Muster GmbH", ort="Linz")
        lieferant = Adresse(typ=AdressTyp.LIEFERANT, firma="Liefer AG", ort="Wels")
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
        session.add(lieferant)
        session.add(artikel)
        session.flush()
        session.add(
            Lagerbestand(artikel_id=artikel.id, lager_id=lager.id, menge=Decimal("3"))
        )
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
            "lieferant_id": lieferant.id,
            "artikel_id": artikel.id,
            "sn_ids": sn_ids,
        }


def _beleg_anlegen(client, adresse_id: int, belegart: str) -> int:
    r = client.post(
        f"/belege/{belegart}/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    assert r.status_code == 302
    return int(r.headers["location"].rsplit("/", 1)[-1])


def _position_anlegen(client, engine, beleg_id: int, artikel_id: int, menge: str = "1") -> int:
    """Fügt eine Artikelposition hinzu und setzt die Menge; liefert die Positions-ID."""
    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    assert r.status_code == 200
    with Session(engine) as session:
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).one()
        position_id = position.id
        bezeichnung = position.bezeichnung
    if menge != "1":
        r = client.post(
            f"/belege/detail/{beleg_id}/positionen/{position_id}",
            data={
                "bezeichnung": bezeichnung,
                "menge": menge,
                "einheit": "Stk",
                "einzelpreis": "100",
                "rabatt_prozent": "0",
                "ust_satz": "20",
            },
        )
        assert r.status_code == 200
    return position_id


def _bestand(engine, artikel_id: int) -> Decimal:
    with Session(engine) as session:
        summe = Decimal("0")
        for bestand in session.exec(
            select(Lagerbestand).where(Lagerbestand.artikel_id == artikel_id)
        ).all():
            summe += bestand.menge
        return summe


def test_sn_dialog_liefert_verfuegbare_sns(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    position_id = _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="2")

    r = client.get(f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-dialog")
    assert r.status_code == 200
    assert "SN-0001" in r.text
    assert "SN-0002" in r.text
    assert "SN-0003" in r.text
    assert "Übernehmen" in r.text


def test_sn_zuweisen_zeigt_badges_im_grid(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    position_id = _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="2")

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-zuweisen",
        data={"seriennummer_ids": daten["sn_ids"][:2]},
    )
    assert r.status_code == 200
    assert "SN-0001" in r.text
    assert "SN-0002" in r.text
    assert "SN-0003" not in r.text
    assert 'id="positionsbereich"' in r.text


def test_buchen_ohne_sn_zuweisung_bleibt_entwurf(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="2")

    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt", follow_redirects=False)
    assert r.status_code == 302
    assert "fehler=" in r.headers["location"]

    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).status == BelegStatus.ENTWURF
    assert _bestand(engine, daten["artikel_id"]) == Decimal("3")


def test_buchen_mit_zuweisung_senkt_bestand_und_verkauft_sns(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    position_id = _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="2")
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-zuweisen",
        data={"seriennummer_ids": daten["sn_ids"][:2]},
    )
    assert r.status_code == 200

    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt", follow_redirects=False)
    assert r.status_code == 302
    assert "fehler=" not in r.headers["location"]

    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).status == BelegStatus.GEDRUCKT
        for sn_id in daten["sn_ids"][:2]:
            assert session.get(Seriennummer, sn_id).status == SnStatus.VERKAUFT
        assert session.get(Seriennummer, daten["sn_ids"][2]).status == SnStatus.AUF_LAGER
    assert _bestand(engine, daten["artikel_id"]) == Decimal("1")


def test_wareneingang_sn_erfassung_und_buchen(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["lieferant_id"], "ek_lieferschein")
    position_id = _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="2")

    r = client.get(f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-erfassung")
    assert r.status_code == 200
    assert r.text.count('name="sns"') == 2

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-erfassen",
        data={"sns": ["NEU-0001", "NEU-0002", ""]},
    )
    assert r.status_code == 200
    assert "NEU-0001" in r.text
    assert "NEU-0002" in r.text

    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt", follow_redirects=False)
    assert r.status_code == 302
    assert "fehler=" not in r.headers["location"]

    assert _bestand(engine, daten["artikel_id"]) == Decimal("5")
    with Session(engine) as session:
        for sn_wert in ("NEU-0001", "NEU-0002"):
            sn = session.exec(
                select(Seriennummer).where(Seriennummer.sn == sn_wert)
            ).one()
            assert sn.status == SnStatus.AUF_LAGER


def test_bestandswarnung_bei_menge_ueber_bestand(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="5")

    r = client.get(f"/belege/detail/{beleg_id}")
    assert r.status_code == 200
    assert "Bestand: 3" in r.text
    assert "Bestand reicht nicht" in r.text


def test_lagerkorrektur_absolut_bucht_differenz(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)  # Bestand 3
    r = client.post(
        "/lager/korrektur",
        data={
            "artikel_id": daten["artikel_id"],
            "lager_id": daten["lager_id"],
            "korrekturart": "absolut",
            "menge": "10",
            "grund": "Inventur",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/lager/bewegungen"
    assert _bestand(engine, daten["artikel_id"]) == Decimal("10")

    with Session(engine) as session:
        bewegungen = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.buchungsart == Buchungsart.KORREKTUR)
        ).all()
        assert len(bewegungen) == 1
        assert bewegungen[0].menge == Decimal("7")  # 10 − 3

    # Journal zeigt die Korrektur-Bewegung
    r = client.get("/lager/bewegungen")
    assert r.status_code == 200
    assert "Korrektur" in r.text


def test_lagerkorrektur_notiz_landet_im_journal(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)  # Bestand 3
    r = client.post(
        "/lager/korrektur",
        data={
            "artikel_id": daten["artikel_id"],
            "lager_id": daten["lager_id"],
            "korrekturart": "differenz",
            "menge": "-1",
            "grund": "Bruch bei Transport",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    with Session(engine) as session:
        bewegung = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.buchungsart == Buchungsart.KORREKTUR)
        ).one()
        assert bewegung.notiz == "Bruch bei Transport"

    # Notiz ist im Journal-HTML sichtbar
    r = client.get("/lager/bewegungen")
    assert r.status_code == 200
    assert "Bruch bei Transport" in r.text


def test_lagerkorrektur_differenz_bucht_relativ(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)  # Bestand 3
    r = client.post(
        "/lager/korrektur",
        data={
            "artikel_id": daten["artikel_id"],
            "lager_id": daten["lager_id"],
            "korrekturart": "differenz",
            "menge": "-2",
            "grund": "Schwund",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert _bestand(engine, daten["artikel_id"]) == Decimal("1")

    with Session(engine) as session:
        bewegung = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.buchungsart == Buchungsart.KORREKTUR)
        ).one()
        assert bewegung.menge == Decimal("-2")


def test_lagerkorrektur_formular_vorbelegt_standardlager(engine, client):
    _login(engine, client)
    _stammdaten(engine)
    r = client.get("/lager/korrektur")
    assert r.status_code == 200
    assert "Bestandskorrektur" in r.text
    assert "Gastherme Testino" in r.text


def test_bewegungen_journal_zeigt_buchungen_und_suche_filtert(engine, client):
    _login(engine, client)
    daten = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, daten["kunde_id"], "lieferschein")
    position_id = _position_anlegen(client, engine, beleg_id, daten["artikel_id"], menge="2")
    client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}/sn-zuweisen",
        data={"seriennummer_ids": daten["sn_ids"][:2]},
    )
    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt", follow_redirects=False)
    assert "fehler=" not in r.headers["location"]

    with Session(engine) as session:
        belegnr = session.get(Beleg, beleg_id).belegnr

    r = client.get("/lager/bewegungen")
    assert r.status_code == 200
    assert "HZ-1" in r.text
    assert "Gastherme Testino" in r.text
    assert belegnr in r.text
    assert "Tester" in r.text

    r = client.get("/lager/bewegungen/suche", params={"q": "Gastherme"})
    assert r.status_code == 200
    assert "HZ-1" in r.text

    r = client.get("/lager/bewegungen/suche", params={"q": "gibtsnicht"})
    assert r.status_code == 200
    assert "HZ-1" not in r.text
    assert "Keine Bewegungen" in r.text
