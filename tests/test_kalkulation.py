"""Tests der Kplus-Kalkulationskette: Listenpreis − Rab1 − Rab2 = EK; EK + Aufschlag = VK."""

from decimal import Decimal

from app.models import Artikel, Belegart
from app.services import beleg_service, berechnung
from app.services.berechnung import (
    aufschlag_aus_vk,
    brutto_aus_netto,
    kalkuliere_ek,
    kalkuliere_vk,
    netto_aus_brutto,
)


def test_ek_aus_listenpreis_mit_zwei_rabatten():
    # 100 − 10 % − 5 % = 100 * 0,9 * 0,95 = 85,50
    assert kalkuliere_ek(Decimal("100"), Decimal("10"), Decimal("5")) == Decimal("85.50")


def test_ek_ohne_rabatt_ist_listenpreis():
    assert kalkuliere_ek(Decimal("42.00"), Decimal("0"), Decimal("0")) == Decimal("42.00")


def test_vk_aus_ek_und_aufschlag():
    # 85,50 + 40 % = 119,70
    assert kalkuliere_vk(Decimal("85.50"), Decimal("40")) == Decimal("119.70")


def test_aufschlag_rueckrechnung():
    # VK 119,70 auf EK 85,50 → 40 %
    assert aufschlag_aus_vk(Decimal("85.50"), Decimal("119.70")) == Decimal("40.00")


def test_aufschlag_bei_ek_null_ist_null():
    assert aufschlag_aus_vk(Decimal("0"), Decimal("50")) == Decimal("0.00")


def test_kette_ist_konsistent():
    ek = kalkuliere_ek(Decimal("200"), Decimal("25"), Decimal("10"))
    vk = kalkuliere_vk(ek, Decimal("50"))
    # Rückgerechneter Aufschlag ergibt wieder ~50 %
    assert aufschlag_aus_vk(ek, vk) == Decimal("50.00")


def test_brutto_netto_umrechnung():
    assert brutto_aus_netto(Decimal("100"), 20) == Decimal("120.00")
    assert brutto_aus_netto(Decimal("100"), 10) == Decimal("110.00")
    assert netto_aus_brutto(Decimal("120"), 20) == Decimal("100.00")


def test_position_uebernimmt_kalkulation_aus_artikel(session):
    artikel = Artikel(
        artikelnr="K-1", bezeichnung="Kalk-Artikel",
        listenpreis_netto=Decimal("100"), rabatt1_prozent=Decimal("10"),
        rabatt2_prozent=Decimal("5"), ek_preis=Decimal("85.50"),
        aufschlag_prozent=Decimal("40"), vk_preis=Decimal("119.70"), ust_satz=20,
    )
    session.add(artikel)
    session.commit()

    beleg = beleg_service.erstelle_beleg(
        session, Belegart.ANGEBOT, _adresse(session), benutzer_id=None
    )
    pos = beleg_service.fuege_position_hinzu(session, beleg, artikel)
    session.commit()

    assert pos.listenpreis == Decimal("100")
    assert pos.rabatt1_prozent == Decimal("10")
    assert pos.rabatt2_prozent == Decimal("5")
    assert pos.ek_preis == Decimal("85.50")
    assert pos.aufschlag_prozent == Decimal("40")
    assert pos.einzelpreis == Decimal("119.70")  # VK aus dem Artikel


def _adresse(session):
    from app.models import Adresse

    adr = Adresse(firma="Kalk Kunde")
    session.add(adr)
    session.commit()
    session.refresh(adr)
    return adr


def test_seed_artikel_haben_konsistente_kalkulation(session):
    """Nach dem Seed gilt für jeden Lagerartikel: kalkuliere_ek(listenpreis, r1, r2) == ek_preis."""
    from scripts import seed

    seed.run(session)
    for artikel in session.exec(_alle_artikel()).all():
        erwartet_ek = berechnung.kalkuliere_ek(
            artikel.listenpreis_netto, artikel.rabatt1_prozent, artikel.rabatt2_prozent
        )
        assert artikel.ek_preis == erwartet_ek


def _alle_artikel():
    from sqlmodel import select

    from app.models import Artikel

    return select(Artikel)
