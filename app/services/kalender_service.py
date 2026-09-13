"""Kplus-Desktop-Kalender: Wiedervorlagen, Belegtermine und Wartungen je Tag.

Reine Leselogik ohne FastAPI. Liefert einheitliche Einträge, damit Dashboard
(Monatsminiatur) und Kalenderseite dieselbe Quelle nutzen.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models.adresse import Adresse
from app.models.aktivitaet import Aktivitaet
from app.models.anlage import Anlage
from app.models.beleg import Beleg, BelegStatus

ARTEN = {
    "wiedervorlage": "Wiedervorlage",
    "termin": "Belegtermin",
    "wartung": "Wartung",
}


@dataclass(frozen=True)
class Eintrag:
    datum: date
    art: str  # wiedervorlage | termin | wartung
    titel: str
    url: str
    kunde: str = ""
    benutzer_id: int | None = None

    @property
    def art_label(self) -> str:
        return ARTEN.get(self.art, self.art)


def monatsraster(jahr: int, monat: int) -> list[list[date]]:
    """Wochenzeilen (Mo–So) eines Monats inkl. Randtagen der Nachbarmonate."""
    return calendar.Calendar(firstweekday=0).monthdatescalendar(jahr, monat)


def monat_verschieben(jahr: int, monat: int, schritt: int) -> tuple[int, int]:
    index = jahr * 12 + (monat - 1) + schritt
    return index // 12, index % 12 + 1


def eintraege(session: Session, von: date, bis: date) -> list[Eintrag]:
    """Alle Kalendereinträge im Zeitraum (beide Grenzen inklusive), sortiert."""
    ergebnis: list[Eintrag] = []

    for akt, adresse in session.exec(
        select(Aktivitaet, Adresse)
        .join(Adresse, col(Aktivitaet.adresse_id) == col(Adresse.id))
        .where(col(Aktivitaet.erledigt).is_(False))
        .where(col(Aktivitaet.wiedervorlage_am).is_not(None))
        .where(col(Aktivitaet.wiedervorlage_am) >= von, col(Aktivitaet.wiedervorlage_am) <= bis)
    ).all():
        ergebnis.append(
            Eintrag(
                datum=akt.wiedervorlage_am,
                art="wiedervorlage",
                titel=akt.betreff or "Wiedervorlage",
                url=f"/adressen/{adresse.id}?tab=aktivitaeten",
                kunde=adresse.firma,
                benutzer_id=akt.benutzer_id,
            )
        )

    for beleg, adresse in session.exec(
        select(Beleg, Adresse)
        .join(Adresse, col(Beleg.adresse_id) == col(Adresse.id))
        .where(col(Beleg.termin).is_not(None))
        .where(col(Beleg.termin) >= von, col(Beleg.termin) <= bis)
        .where(col(Beleg.status).in_([BelegStatus.ENTWURF, BelegStatus.GEDRUCKT]))
        .where(col(Beleg.ist_muster).is_(False))
    ).all():
        ergebnis.append(
            Eintrag(
                datum=beleg.termin,
                art="termin",
                titel=f"{beleg.belegnr} {beleg.betreff}".strip(),
                url=f"/belege/detail/{beleg.id}",
                kunde=adresse.firma,
                benutzer_id=beleg.erstellt_von,
            )
        )

    for anlage, adresse in session.exec(
        select(Anlage, Adresse)
        .join(Adresse, col(Anlage.adresse_id) == col(Adresse.id))
        .where(col(Anlage.aktiv).is_(True))
        .where(col(Anlage.naechste_wartung).is_not(None))
        .where(col(Anlage.naechste_wartung) >= von, col(Anlage.naechste_wartung) <= bis)
    ).all():
        ergebnis.append(
            Eintrag(
                datum=anlage.naechste_wartung,
                art="wartung",
                titel=f"Wartung {anlage.kurz}",
                url=f"/anlagen/{anlage.id}?tab=wartung",
                kunde=adresse.firma,
            )
        )

    ergebnis.sort(key=lambda e: (e.datum, e.art, e.titel))
    return ergebnis


def je_tag(eintraege_liste: list[Eintrag]) -> dict[date, list[Eintrag]]:
    tage: dict[date, list[Eintrag]] = {}
    for e in eintraege_liste:
        tage.setdefault(e.datum, []).append(e)
    return tage


def heute_und_demnaechst(session: Session, heute: date, tage: int = 7) -> list[Eintrag]:
    """Für das Dashboard: überfällige + Einträge bis heute+tage (überfällige zuerst)."""
    return eintraege(session, heute - timedelta(days=365), heute + timedelta(days=tage))
