"""Kompletter Abnahme-Durchlauf als Service-Level-Test.

Angebot (exakte Summen) → Auftrag → Lieferschein (SN-Pflicht, Buchung) →
Rechnung (SN-Übernahme, Unveränderlichkeit) → Gutschrift; dazu ein separater
Mini-Lieferschein für den Storno-Fall (Gegenbuchung im Journal).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from app.models import (
    Adresse,
    Artikel,
    Beleg,
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
    BelegStatus,
    Buchungsart,
    Lagerbewegung,
    Seriennummer,
    SnStatus,
)
from app.services import beleg_service, berechnung, lager_service, seriennummer_service
from app.services.buchung import registriere_lager_hooks
from app.services.fehler import BelegGesperrt, SnPflichtVerletzt
from scripts import seed


@pytest.fixture(autouse=True)
def _lager_hooks():
    """Lager-Hooks je Test frisch registrieren und danach aufräumen."""
    registriere_lager_hooks()
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


def _artikel(session, artikelnr: str) -> Artikel:
    return session.exec(select(Artikel).where(Artikel.artikelnr == artikelnr)).one()


def _positionen(session, beleg: Beleg) -> list[BelegPosition]:
    return list(
        session.exec(
            select(BelegPosition)
            .where(BelegPosition.beleg_id == beleg.id)
            .order_by(BelegPosition.pos_nr)  # type: ignore[arg-type]
        ).all()
    )


@pytest.mark.integration
def test_kompletter_durchlauf(session):
    jahr = date.today().year

    # Stammdaten über die Seed-Funktionen (ohne Demo-Durchlauf)
    lager = seed.seed_lager(session)
    seed.seed_adressen(session)
    seed.seed_artikel(session, lager)
    session.commit()

    kunde = session.exec(
        select(Adresse).where(Adresse.firma == "Hotel Alpenblick GmbH")
    ).one()
    therme = _artikel(session, "HZ-1001")  # SN-pflichtig, Bestand 3
    ventil = _artikel(session, "HZ-1102")  # Lagerartikel, Bestand 25
    stunde = _artikel(session, "DL-9001")  # Dienstleistung, keine Lagerwirkung

    # ---------------------------------------------------------------- Angebot
    angebot = beleg_service.erstelle_beleg(session, Belegart.ANGEBOT, kunde)
    beleg_service.fuege_position_hinzu(session, angebot, therme, menge=Decimal("1"))
    beleg_service.fuege_position_hinzu(session, angebot, ventil, menge=Decimal("8"))
    beleg_service.fuege_position_hinzu(session, angebot, stunde, menge=Decimal("6"))

    assert angebot.belegnr == f"AN-{jahr}-0001"
    # 1×2190.00 + 8×24.90 + 6×78.00 = 2857.20 netto, alles 20 % USt
    assert angebot.summe_netto == Decimal("2857.20")
    assert angebot.summe_ust == Decimal("571.44")
    assert angebot.summe_brutto == Decimal("3428.64")
    details = berechnung.parse_steuer_details(angebot.steuer_details)
    assert set(details) == {20}
    assert details[20].netto == Decimal("2857.20")
    assert details[20].ust == Decimal("571.44")

    beleg_service.setze_status(session, angebot, BelegStatus.GEDRUCKT)
    assert angebot.status == BelegStatus.GEDRUCKT

    # ---------------------------------------------------------------- Auftrag
    auftrag = beleg_service.erzeuge_folgebeleg(session, angebot)
    assert auftrag.belegart == Belegart.AUFTRAG
    assert auftrag.belegnr == f"AB-{jahr}-0001"
    assert angebot.status == BelegStatus.ERLEDIGT

    angebot_positionen = _positionen(session, angebot)
    auftrag_positionen = _positionen(session, auftrag)
    assert len(auftrag_positionen) == 3
    for alt, neu in zip(angebot_positionen, auftrag_positionen, strict=True):
        assert neu.ursprungs_position_id == alt.id
        assert neu.menge == alt.menge
        assert neu.einzelpreis == alt.einzelpreis
    assert auftrag.summe_brutto == Decimal("3428.64")

    # ------------------------------------------------------------ Lieferschein
    beleg_service.setze_status(session, auftrag, BelegStatus.GEDRUCKT)
    lieferschein = beleg_service.erzeuge_folgebeleg(session, auftrag)
    assert lieferschein.belegnr == f"LS-{jahr}-0001"
    assert auftrag.status == BelegStatus.ERLEDIGT
    ls_id = lieferschein.id
    session.commit()  # Checkpoint vor dem fehlschlagenden Buchungsversuch

    # Ohne SN-Zuweisung schlägt das Buchen fehl — Rollback, Status bleibt Entwurf
    with pytest.raises(SnPflichtVerletzt):
        beleg_service.setze_status(session, lieferschein, BelegStatus.GEDRUCKT)
    session.rollback()

    lieferschein = session.get(Beleg, ls_id)
    assert lieferschein.status == BelegStatus.ENTWURF
    assert lager_service.bestand(session, therme.id) == Decimal("3")
    assert lager_service.bestand(session, ventil.id) == Decimal("25")

    # SN zuweisen und buchen
    ls_pos_therme = session.exec(
        select(BelegPosition).where(
            BelegPosition.beleg_id == ls_id, BelegPosition.artikel_id == therme.id
        )
    ).one()
    sns = seriennummer_service.verfuegbare_sns(session, therme.id)
    assert len(sns) == 3
    seriennummer_service.weise_sns_zu(session, ls_pos_therme, [sns[0].id])
    beleg_service.setze_status(session, lieferschein, BelegStatus.GEDRUCKT)
    session.commit()

    assert lager_service.bestand(session, therme.id) == Decimal("2")
    assert lager_service.bestand(session, ventil.id) == Decimal("17")
    sn = session.get(Seriennummer, sns[0].id)
    assert sn.status == SnStatus.VERKAUFT
    assert sn.lieferschein_position_id == ls_pos_therme.id

    bewegungen = session.exec(
        select(Lagerbewegung).where(Lagerbewegung.beleg_id == ls_id)
    ).all()
    # genau 2 Abgänge: 1× Therme (mit SN), 1× Ventile — KEINE für die Dienstleistung
    assert len(bewegungen) == 2
    assert all(b.buchungsart == Buchungsart.LIEFERSCHEIN for b in bewegungen)
    assert not any(b.artikel_id == stunde.id for b in bewegungen)
    sn_bewegung = next(b for b in bewegungen if b.artikel_id == therme.id)
    assert sn_bewegung.menge == Decimal("-1")
    assert sn_bewegung.seriennummer_id == sn.id
    ventil_bewegung = next(b for b in bewegungen if b.artikel_id == ventil.id)
    assert ventil_bewegung.menge == Decimal("-8")

    # ---------------------------------------------------------------- Rechnung
    rechnung = beleg_service.erzeuge_folgebeleg(session, lieferschein)
    assert rechnung.belegnr == f"RE-{jahr}-0001"
    assert rechnung.leistungsdatum_von == lieferschein.datum
    assert rechnung.leistungsdatum_bis == lieferschein.datum
    assert lieferschein.status == BelegStatus.ERLEDIGT

    re_pos_therme = session.exec(
        select(BelegPosition).where(
            BelegPosition.beleg_id == rechnung.id,
            BelegPosition.artikel_id == therme.id,
        )
    ).one()
    assert re_pos_therme.ursprungs_position_id == ls_pos_therme.id
    zuordnung = session.exec(
        select(BelegPositionSeriennummer).where(
            BelegPositionSeriennummer.position_id == re_pos_therme.id
        )
    ).one()
    assert zuordnung.seriennummer_id == sn.id
    assert rechnung.summe_brutto == Decimal("3428.64")

    beleg_service.setze_status(session, rechnung, BelegStatus.GEDRUCKT)
    # Gedruckte Rechnung ist unveränderlich
    with pytest.raises(BelegGesperrt):
        beleg_service.fuege_position_hinzu(session, rechnung, ventil, menge=Decimal("1"))

    # --------------------------------------------------------------- Gutschrift
    gutschrift = beleg_service.erzeuge_folgebeleg(session, rechnung)
    assert gutschrift.belegart == Belegart.GUTSCHRIFT
    assert gutschrift.belegnr == f"GS-{jahr}-0001"
    assert len(_positionen(session, gutschrift)) == 3
    assert rechnung.status == BelegStatus.ERLEDIGT
    session.commit()

    # ------------------------------------------- Mini-Lieferschein: Storno-Fall
    bestand_vorher = lager_service.bestand(session, ventil.id)
    mini = beleg_service.erstelle_beleg(session, Belegart.LIEFERSCHEIN, kunde)
    assert mini.belegnr == f"LS-{jahr}-0002"
    beleg_service.fuege_position_hinzu(session, mini, ventil, menge=Decimal("1"))
    beleg_service.setze_status(session, mini, BelegStatus.GEDRUCKT)
    assert lager_service.bestand(session, ventil.id) == bestand_vorher - Decimal("1")

    beleg_service.setze_status(session, mini, BelegStatus.STORNIERT)
    session.commit()
    assert mini.status == BelegStatus.STORNIERT
    assert lager_service.bestand(session, ventil.id) == bestand_vorher

    storno = session.exec(
        select(Lagerbewegung).where(
            Lagerbewegung.beleg_id == mini.id,
            Lagerbewegung.buchungsart == Buchungsart.STORNO,
        )
    ).one()
    assert storno.artikel_id == ventil.id
    assert storno.menge == Decimal("1")
