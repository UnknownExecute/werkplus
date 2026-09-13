"""Artikelstamm (Kplus): Staffelpreise, Lieferanten je Artikel, reservierte und
bestellte Mengen, Fortschreibung „letzter EK / letzter Lieferant".

Keine FastAPI-Abhängigkeit. Funktionen flushen nur — der Aufrufer committed.
"""

from collections import defaultdict
from decimal import Decimal

from sqlmodel import Session, col, select

from app.models.artikel import Artikel, ArtikelLieferant, ArtikelStaffel
from app.models.beleg import Beleg, Belegart, BelegPosition, BelegStatus
from app.services.fehler import WerkplusFehler

# --------------------------------------------------------------- Staffelpreise


def staffeln(session: Session, artikel_id: int) -> list[ArtikelStaffel]:
    """Staffeln aufsteigend nach Menge — in Python sortiert, weil ab_menge als
    TEXT gespeichert ist (DecimalStr) und SQL sonst "100" vor "50" einordnet."""
    zeilen = session.exec(
        select(ArtikelStaffel).where(ArtikelStaffel.artikel_id == artikel_id)
    ).all()
    return sorted(zeilen, key=lambda st: st.ab_menge)


def staffelpreis(session: Session, artikel: Artikel, menge: Decimal) -> Decimal:
    """VK für ``menge``: höchste Staffel mit ab_menge ≤ menge, sonst der Stamm-VK."""
    preis = artikel.vk_preis
    for staffel in staffeln(session, artikel.id):
        if menge >= staffel.ab_menge:
            preis = staffel.vk_preis
    return preis


def setze_staffel(
    session: Session, artikel: Artikel, ab_menge: Decimal, vk_preis: Decimal
) -> ArtikelStaffel:
    """Staffel anlegen oder (gleiche ab_menge) überschreiben."""
    if ab_menge <= 0:
        raise WerkplusFehler("Die Staffelmenge muss größer als 0 sein")
    if vk_preis < 0:
        raise WerkplusFehler("Der Staffelpreis darf nicht negativ sein")
    vorhanden = session.exec(
        select(ArtikelStaffel).where(
            ArtikelStaffel.artikel_id == artikel.id, ArtikelStaffel.ab_menge == ab_menge
        )
    ).first()
    staffel = vorhanden or ArtikelStaffel(artikel_id=artikel.id, ab_menge=ab_menge)
    staffel.vk_preis = vk_preis
    session.add(staffel)
    session.flush()
    return staffel


# ------------------------------------------------------------------ Lieferanten


def lieferanten(session: Session, artikel_id: int) -> list[ArtikelLieferant]:
    return list(
        session.exec(
            select(ArtikelLieferant)
            .where(ArtikelLieferant.artikel_id == artikel_id)
            .order_by(col(ArtikelLieferant.ist_hauptlieferant).desc(), ArtikelLieferant.id)
        ).all()
    )


def lieferant_eintrag(
    session: Session, artikel_id: int, adresse_id: int
) -> ArtikelLieferant | None:
    return session.exec(
        select(ArtikelLieferant).where(
            ArtikelLieferant.artikel_id == artikel_id, ArtikelLieferant.adresse_id == adresse_id
        )
    ).first()


def hauptlieferant(session: Session, artikel_id: int) -> ArtikelLieferant | None:
    eintraege = lieferanten(session, artikel_id)
    return next((e for e in eintraege if e.ist_hauptlieferant), eintraege[0] if eintraege else None)


def ek_fuer_lieferant(session: Session, artikel: Artikel, adresse_id: int) -> Decimal:
    """EK beim konkreten Lieferanten, sonst Stamm-EK (für Preisanfrage/Bestellung)."""
    eintrag = lieferant_eintrag(session, artikel.id, adresse_id)
    if eintrag is not None and eintrag.ek_preis > 0:
        return eintrag.ek_preis
    return artikel.ek_preis


def setze_lieferant(  # noqa: PLR0913
    session: Session,
    artikel: Artikel,
    adresse_id: int,
    lieferanten_artikelnr: str = "",
    ek_preis: Decimal = Decimal("0"),
    lieferzeit_tage: int = 0,
    notiz: str = "",
    ist_hauptlieferant: bool = False,
) -> ArtikelLieferant:
    """Bezugsquelle anlegen oder aktualisieren; der erste Eintrag wird Hauptlieferant."""
    eintrag = lieferant_eintrag(session, artikel.id, adresse_id) or ArtikelLieferant(
        artikel_id=artikel.id, adresse_id=adresse_id
    )
    eintrag.lieferanten_artikelnr = lieferanten_artikelnr.strip()
    eintrag.ek_preis = ek_preis
    eintrag.lieferzeit_tage = max(0, lieferzeit_tage)
    eintrag.notiz = notiz.strip()
    session.add(eintrag)
    session.flush()
    if ist_hauptlieferant or hauptlieferant(session, artikel.id) is None:
        setze_hauptlieferant(session, artikel, eintrag)
    return eintrag


def setze_hauptlieferant(session: Session, artikel: Artikel, eintrag: ArtikelLieferant) -> None:
    if eintrag.artikel_id != artikel.id:
        raise WerkplusFehler("Lieferant gehört nicht zu diesem Artikel")
    for e in lieferanten(session, artikel.id):
        e.ist_hauptlieferant = e.id == eintrag.id
        session.add(e)
    session.flush()


def loesche_lieferant(session: Session, artikel: Artikel, eintrag: ArtikelLieferant) -> None:
    if eintrag.artikel_id != artikel.id:
        raise WerkplusFehler("Lieferant gehört nicht zu diesem Artikel")
    war_haupt = eintrag.ist_hauptlieferant
    session.delete(eintrag)
    session.flush()
    if war_haupt:
        rest = lieferanten(session, artikel.id)
        if rest:
            setze_hauptlieferant(session, artikel, rest[0])


def aktualisiere_letzten_ek(session: Session, beleg: Beleg, benutzer_id: int | None = None) -> None:
    """Buchungs-Hook der EK-Rechnung: letzter EK / letzter Lieferant je Artikel
    fortschreiben und die Bezugsquelle (Lieferant je Artikel) mit dem EK pflegen."""
    positionen = session.exec(
        select(BelegPosition).where(BelegPosition.beleg_id == beleg.id)
    ).all()
    for position in positionen:
        if position.artikel_id is None:
            continue
        artikel = session.get(Artikel, position.artikel_id)
        if artikel is None:
            continue
        artikel.letzter_ek = position.einzelpreis
        artikel.letzter_lieferant_id = beleg.adresse_id
        artikel.letzter_ek_am = beleg.datum
        session.add(artikel)
        eintrag = lieferant_eintrag(session, artikel.id, beleg.adresse_id) or ArtikelLieferant(
            artikel_id=artikel.id, adresse_id=beleg.adresse_id
        )
        eintrag.ek_preis = position.einzelpreis
        session.add(eintrag)
        session.flush()
        if hauptlieferant(session, artikel.id) is None or not any(
            e.ist_hauptlieferant for e in lieferanten(session, artikel.id)
        ):
            setze_hauptlieferant(session, artikel, eintrag)
    session.flush()


# --------------------------------------------------- Reserviert / Bestellt (Kplus)


def _offene_mengen(
    session: Session, belegart: Belegart, artikel_ids: list[int] | None
) -> dict[int, Decimal]:
    """Noch nicht in den Folgebeleg übernommene Mengen gebuchter Belege je Artikel.

    Reserviert = offene Mengen gedruckter Aufträge (noch nicht geliefert),
    Bestellt = offene Mengen gedruckter Bestellungen (noch nicht eingegangen).
    Die Restmenge je Position kommt aus der Übernahmekette (ursprungs_position_id).
    """
    from app.services import beleg_service  # zirkulären Import vermeiden

    stmt = (
        select(BelegPosition)
        .join(Beleg, col(BelegPosition.beleg_id) == col(Beleg.id))
        .where(Beleg.belegart == belegart, Beleg.status == BelegStatus.GEDRUCKT)
        .where(col(Beleg.ist_muster).is_(False))
        .where(col(BelegPosition.artikel_id).is_not(None))
    )
    if artikel_ids is not None:
        if not artikel_ids:
            return {}
        stmt = stmt.where(col(BelegPosition.artikel_id).in_(artikel_ids))
    summen: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for position in session.exec(stmt).all():
        rest = beleg_service.restmenge(session, position)
        if rest > 0:
            summen[position.artikel_id] += rest
    return dict(summen)


def reserviert_je_artikel(
    session: Session, artikel_ids: list[int] | None = None
) -> dict[int, Decimal]:
    return _offene_mengen(session, Belegart.AUFTRAG, artikel_ids)


def bestellt_je_artikel(
    session: Session, artikel_ids: list[int] | None = None
) -> dict[int, Decimal]:
    return _offene_mengen(session, Belegart.BESTELLUNG, artikel_ids)


def reserviert(session: Session, artikel_id: int) -> Decimal:
    return reserviert_je_artikel(session, [artikel_id]).get(artikel_id, Decimal("0"))


def bestellt(session: Session, artikel_id: int) -> Decimal:
    return bestellt_je_artikel(session, [artikel_id]).get(artikel_id, Decimal("0"))
