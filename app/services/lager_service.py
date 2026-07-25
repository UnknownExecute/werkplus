"""Lagerführung: Bestände (Upsert je Artikel+Lager) und lückenloses Bewegungsjournal.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed.
Negative Bestände sind fachlich erlaubt (Lieferschein bucht auch bei zu wenig Bestand).
"""

from decimal import Decimal

from sqlmodel import Session, select

from app.models.lager import Buchungsart, Lager, Lagerbestand, Lagerbewegung
from app.services.fehler import WerkplusFehler


def standard_lager(session: Session) -> Lager:
    """Liefert das Standardlager; wirft WerkplusFehler, wenn keines existiert."""
    lager = session.exec(select(Lager).where(Lager.ist_standard)).first()
    if lager is None:
        raise WerkplusFehler("Kein Standardlager vorhanden (Lager mit ist_standard=True fehlt)")
    return lager


def bestand(session: Session, artikel_id: int, lager_id: int | None = None) -> Decimal:
    """Aktueller Bestand eines Artikels — je Lager oder (Default) über alle Läger summiert."""
    abfrage = select(Lagerbestand).where(Lagerbestand.artikel_id == artikel_id)
    if lager_id is not None:
        abfrage = abfrage.where(Lagerbestand.lager_id == lager_id)
    zeilen = session.exec(abfrage).all()
    return sum((zeile.menge for zeile in zeilen), Decimal("0"))


def buche(
    session: Session,
    *,
    artikel_id: int,
    lager_id: int,
    menge: Decimal,
    buchungsart: Buchungsart,
    beleg_id: int | None = None,
    position_id: int | None = None,
    seriennummer_id: int | None = None,
    benutzer_id: int | None = None,
    notiz: str = "",
) -> Lagerbewegung:
    """Bucht eine Menge (positiv = Zugang, negativ = Abgang) auf ein Lager.

    Upsertet den Lagerbestand (menge += ...) und schreibt IMMER einen
    Journal-Eintrag (Lagerbewegung) mit Belegreferenz und Zeitstempel.
    """
    bestandszeile = session.exec(
        select(Lagerbestand).where(
            Lagerbestand.artikel_id == artikel_id,
            Lagerbestand.lager_id == lager_id,
        )
    ).one_or_none()
    if bestandszeile is None:
        bestandszeile = Lagerbestand(artikel_id=artikel_id, lager_id=lager_id, menge=menge)
    else:
        bestandszeile.menge = bestandszeile.menge + menge
    session.add(bestandszeile)

    bewegung = Lagerbewegung(
        artikel_id=artikel_id,
        lager_id=lager_id,
        menge=menge,
        buchungsart=buchungsart,
        beleg_id=beleg_id,
        position_id=position_id,
        seriennummer_id=seriennummer_id,
        benutzer_id=benutzer_id,
        notiz=notiz,
    )
    session.add(bewegung)
    session.flush()
    return bewegung
