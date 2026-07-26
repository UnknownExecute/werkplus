"""Integrationstests: die Router nutzen die geprüften Parser wirklich.

tests/test_formparse_grenzen.py prüft die Helfer als Einheiten — hier wird
belegt, dass Positionseditor, Artikelstamm und Adressstamm sie auch einsetzen
und unsinnige Eingaben mit 422 abweisen.
"""

from decimal import Decimal

from sqlmodel import Session

from app.models import Adresse, AdressTyp, Artikel, Rolle
from app.models.benutzer import Benutzer
from app.services.auth_service import hash_passwort


def _login(engine, client):
    with Session(engine) as session:
        session.add(
            Benutzer(
                login="tester",
                name="Tester",
                passwort_hash=hash_passwort("geheim"),
                rolle=Rolle.ADMIN,
                aktiv=True,
            )
        )
        session.commit()
    r = client.post(
        "/login", data={"login": "tester", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


def _angebot_mit_position(engine, client) -> tuple[int, int]:
    """Kunde + Artikel + Angebot mit einer Position; liefert (beleg_id, position_id)."""
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Beispiel GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="V-1",
            bezeichnung="Testartikel",
            einheit="Stk",
            listenpreis_netto=Decimal("100.00"),
            ek_preis=Decimal("60.00"),
            vk_preis=Decimal("100.00"),
            ust_satz=20,
        )
        session.add(adresse)
        session.add(artikel)
        session.commit()
        adresse_id, artikel_id = adresse.id, artikel.id

    r = client.post(
        "/belege/angebot/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    assert r.status_code == 200

    from app.models import BelegPosition

    with Session(engine) as session:
        from sqlmodel import select

        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).first()
        return beleg_id, position.id


def _positionsdaten(**abweichungen) -> dict:
    daten = {
        "bezeichnung": "Testartikel",
        "langtext": "",
        "menge": "1",
        "einheit": "Stk",
        "listenpreis": "100",
        "rabatt1_prozent": "0",
        "rabatt2_prozent": "0",
        "aufschlag_prozent": "20",
        "einzelpreis": "100",
        "rabatt_prozent": "0",
        "ust_satz": "20",
    }
    daten.update(abweichungen)
    return daten


# --- Positionseditor ---------------------------------------------------------


def test_position_rabatt_ueber_100_abgelehnt(engine, client):
    _login(engine, client)
    beleg_id, position_id = _angebot_mit_position(engine, client)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}",
        data=_positionsdaten(rabatt1_prozent="150"),
    )
    assert r.status_code == 422


def test_position_negativer_rabatt_abgelehnt(engine, client):
    _login(engine, client)
    beleg_id, position_id = _angebot_mit_position(engine, client)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}",
        data=_positionsdaten(rabatt_prozent="-10"),
    )
    assert r.status_code == 422


def test_position_nan_menge_abgelehnt(engine, client):
    _login(engine, client)
    beleg_id, position_id = _angebot_mit_position(engine, client)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}",
        data=_positionsdaten(menge="nan"),
    )
    assert r.status_code == 422


def test_position_unplausibler_preis_abgelehnt(engine, client):
    _login(engine, client)
    beleg_id, position_id = _angebot_mit_position(engine, client)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}",
        data=_positionsdaten(einzelpreis="99999999999999"),
    )
    assert r.status_code == 422


def test_position_negativer_preis_bleibt_erlaubt(engine, client):
    """Abzugspositionen (Schlussrechnung) haben fachlich einen negativen Preis."""
    _login(engine, client)
    beleg_id, position_id = _angebot_mit_position(engine, client)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}",
        data=_positionsdaten(einzelpreis="-500"),
    )
    assert r.status_code == 200


def test_position_gueltige_werte_ok(engine, client):
    """Gegenprobe: normale Eingaben werden unverändert gespeichert."""
    _login(engine, client)
    beleg_id, position_id = _angebot_mit_position(engine, client)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position_id}",
        data=_positionsdaten(rabatt1_prozent="10", rabatt2_prozent="5", menge="2,5"),
    )
    assert r.status_code == 200


# --- Artikelstamm ------------------------------------------------------------


def test_artikel_rabatt_ueber_100_abgelehnt(engine, client):
    _login(engine, client)
    r = client.post(
        "/artikel/neu",
        data={
            "artikelnr": "X-1",
            "bezeichnung": "Test",
            "einheit": "Stk",
            "ust_satz": "20",
            "listenpreis_netto": "100",
            "rabatt1_prozent": "500",
            "rabatt2_prozent": "0",
            "aufschlag_prozent": "20",
            "vk_preis": "0",
        },
    )
    assert r.status_code == 422


# --- Adressstamm -------------------------------------------------------------


def test_adresse_standardrabatt_ueber_100_abgelehnt(engine, client):
    """Der Kundenrabatt wird in Positionen vorbelegt — Unsinn darf nicht durch."""
    _login(engine, client)
    r = client.post(
        "/adressen/neu",
        data={"firma": "Rabatt GmbH", "standard_rabatt_prozent": "120"},
    )
    assert r.status_code == 422
