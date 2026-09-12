"""Tests für das M5-Dashboard: Karten nach Seed-Demo-Durchlauf + offener Angebots-Entwurf."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models import Adresse, AdressTyp, Artikel, Beleg, Belegart, BelegStatus
from app.services import beleg_service
from app.services.buchung import registriere_lager_hooks
from scripts import seed


@pytest.fixture(autouse=True)
def _lager_hooks():
    """Lager-Hooks je Test frisch registrieren (seed_demo_durchlauf bucht) und aufräumen."""
    registriere_lager_hooks()
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


def _seed_und_login(engine, client) -> None:
    with Session(engine) as session:
        seed.run(session)
    r = client.post(
        "/login", data={"login": "admin", "passwort": "admin"}, follow_redirects=False
    )
    assert r.status_code == 302


def _demo_rechnungsnr(engine) -> str:
    with Session(engine) as session:
        rechnung = session.exec(
            select(Beleg).where(Beleg.belegart == Belegart.RECHNUNG)
        ).one()
        return rechnung.belegnr


def test_dashboard_nach_seed(engine, client):
    _seed_und_login(engine, client)

    r = client.get("/")
    assert r.status_code == 200
    # Die Demo-Kette ist komplett durchgelaufen — kein Angebot bleibt offen
    assert "Keine offenen Angebote" in r.text
    # Der Regie-Auftrag der Zeiterfassungs-Demo bleibt bewusst im Entwurf offen
    # (nur dort lässt sich „Zeiten übernehmen" ausprobieren) und erscheint daher
    # in der Auftragskachel.
    assert "Keine unfertigen Aufträge" not in r.text
    # Fällige Wartungen: beide Demo-Anlagen, die überfällige rot markiert
    assert "Bäckerei Brotherz e.U." in r.text
    assert "Gasthof Post KG" in r.text
    assert "table-danger" in r.text
    assert "überfällig" in r.text
    # Kachel-Link auf die gefilterte Anlagenliste (Vertrag mit /anlagen)
    assert "/anlagen?wartung=faellig" in r.text
    # Letzte Belege enthalten die Demo-Rechnung
    rechnungsnr = _demo_rechnungsnr(engine)
    assert rechnungsnr == f"{date.today().year % 100:02d}0001"  # Kplus-Format JJNNNN
    assert rechnungsnr in r.text


def _login_ohne_seed(engine, client) -> None:
    from app.models.benutzer import Benutzer, Rolle
    from app.services.auth_service import hash_passwort

    with Session(engine) as session:
        session.add(
            Benutzer(
                login="chef",
                name="Chef",
                passwort_hash=hash_passwort("geheim"),
                rolle=Rolle.ADMIN,
                aktiv=True,
            )
        )
        session.commit()
    r = client.post(
        "/login", data={"login": "chef", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


def _gebuchte_rechnung(engine, *, bezahlt: bool) -> str:
    """Erstellt eine gedruckte Rechnung (optional bezahlt) und liefert deren Belegnr."""
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Kunde AG", zahlungsziel_tage=14)
        artikel = Artikel(
            artikelnr="A-1", bezeichnung="Ware", vk_preis=Decimal("100.00"), ust_satz=20
        )
        session.add(adresse)
        session.add(artikel)
        session.flush()
        rechnung = beleg_service.erstelle_beleg(session, Belegart.RECHNUNG, adresse)
        beleg_service.fuege_position_hinzu(session, rechnung, artikel, rabatt_prozent=Decimal("0"))
        beleg_service.setze_status(session, rechnung, BelegStatus.GEDRUCKT)
        if bezahlt:
            rechnung.bezahlt_am = date.today()
            session.add(rechnung)
        session.commit()
        return rechnung.belegnr


def test_offene_posten_kachel_zeigt_unbezahlte_rechnung(engine, client):
    _login_ohne_seed(engine, client)
    belegnr = _gebuchte_rechnung(engine, bezahlt=False)

    r = client.get("/")
    assert r.status_code == 200
    assert "Offene Posten" in r.text
    assert belegnr in r.text
    assert "Offen gesamt" in r.text  # Summenzeile nur bei offenen Posten


def test_offene_posten_kachel_ohne_bezahlte_rechnung(engine, client):
    _login_ohne_seed(engine, client)
    _gebuchte_rechnung(engine, bezahlt=True)

    r = client.get("/")
    assert r.status_code == 200
    # Bezahlte Rechnung taucht in Offenen Posten NICHT auf -> Kachel leer
    assert "Keine offenen Posten." in r.text


def test_dashboard_zeigt_offenen_angebots_entwurf(engine, client):
    _seed_und_login(engine, client)

    with Session(engine) as session:
        kunde = session.exec(
            select(Adresse).where(Adresse.firma == "Wohnbau Steiner GmbH")
        ).one()
        angebot = beleg_service.erstelle_beleg(session, Belegart.ANGEBOT, kunde)
        session.commit()
        belegnr = angebot.belegnr

    r = client.get("/")
    assert r.status_code == 200
    assert "Keine offenen Angebote" not in r.text
    assert belegnr in r.text
    assert "Wohnbau Steiner GmbH" in r.text
