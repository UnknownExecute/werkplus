"""Artikel-Sets / Stücklisten: Komponenten eines Set-Artikels in Belege einfügen.

Keine FastAPI-Abhängigkeit. Ruft nur ``beleg_service.fuege_position_hinzu`` auf;
der Aufrufer hält die Transaktion und committed.
"""

from decimal import Decimal

from sqlmodel import Session, select

from app.models import Artikel, SetKomponente
from app.models.beleg import Beleg, BelegPosition
from app.services import beleg_service


def ist_set(artikel: Artikel) -> bool:
    """True, wenn der Artikel ein Set-/Stücklisten-Artikel ist."""
    return bool(artikel.ist_set)


def komponenten(session: Session, set_artikel_id: int) -> list[SetKomponente]:
    """Alle Komponenten eines Sets, nach ``sort`` sortiert."""
    return list(
        session.exec(
            select(SetKomponente)
            .where(SetKomponente.set_artikel_id == set_artikel_id)
            .order_by(SetKomponente.sort)  # type: ignore[arg-type]
        ).all()
    )


def fuege_set_positionen_ein(
    session: Session,
    beleg: Beleg,
    set_artikel: Artikel,
    menge_faktor: Decimal = Decimal("1"),
    benutzer_id: int | None = None,
) -> list[BelegPosition]:
    """Fügt für jede Komponente des Sets eine Belegposition in ``beleg`` ein.

    Menge je Position = ``komponente.menge * menge_faktor``. Preis, USt und die
    Kalkulationsfelder kommen aus dem jeweiligen Komponentenartikel (wie eine
    normale Artikelposition — siehe ``beleg_service.fuege_position_hinzu``).
    Gibt die erzeugten Positionen in Reihenfolge der Komponenten (``sort``) zurück.
    """
    positionen: list[BelegPosition] = []
    for komp in komponenten(session, set_artikel.id):
        komponente_artikel = session.get(Artikel, komp.komponente_artikel_id)
        if komponente_artikel is None:
            continue
        position = beleg_service.fuege_position_hinzu(
            session,
            beleg,
            komponente_artikel,
            menge=komp.menge * menge_faktor,
        )
        positionen.append(position)
    return positionen
