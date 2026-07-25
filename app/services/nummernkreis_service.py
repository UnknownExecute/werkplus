"""Vergabe fortlaufender Belegnummern je Belegart und Jahr (z. B. RE-2026-0001).

Atomik: Innerhalb der offenen Transaktion des Aufrufers wird der Zähler per
``INSERT ... ON CONFLICT DO NOTHING`` angelegt und per relativem
``UPDATE letzte_nummer = letzte_nummer + 1`` erhöht — das UPDATE serialisiert
konkurrierende Schreiber über den SQLite-Write-Lock (busy_timeout=30s).
Erst danach wird der neue Stand gelesen. Kein Read-Modify-Write im Python-Code,
daher keine Lost Updates. Die Nummer ist erst mit dem Commit des Aufrufers fix.
"""

import time
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.models.beleg import Belegart
from app.services.fehler import UngueltigeBelegart

PRAEFIXE: dict[Belegart, str] = {
    Belegart.ANGEBOT: "AN",
    Belegart.AUFTRAG: "AB",
    Belegart.LIEFERSCHEIN: "LS",
    Belegart.RECHNUNG: "RE",
    Belegart.GUTSCHRIFT: "GS",
    Belegart.BESTELLUNG: "BE",
    Belegart.EK_LIEFERSCHEIN: "WE",
    Belegart.EK_RECHNUNG: "ER",
}

_MAX_VERSUCHE = 10
_BACKOFF_BASIS_SEK = 0.05

_INSERT = text(
    "INSERT INTO nummernkreis (belegart, jahr, praefix, letzte_nummer) "
    "VALUES (:belegart, :jahr, :praefix, 0) "
    "ON CONFLICT (belegart, jahr) DO NOTHING"
)
_UPDATE = text(
    "UPDATE nummernkreis SET letzte_nummer = letzte_nummer + 1 "
    "WHERE belegart = :belegart AND jahr = :jahr"
)
_SELECT = text(
    "SELECT letzte_nummer FROM nummernkreis WHERE belegart = :belegart AND jahr = :jahr"
)


def naechste_belegnr(session: Session, belegart: Belegart, jahr: int | None = None) -> str:
    """Zieht atomar die nächste Belegnummer, z. B. ``RE-2026-0001``.

    Der Aufrufer hält die Transaktion — die Nummer ist erst mit seinem Commit
    endgültig vergeben. Bei "database is locked" wird mit kurzem Backoff
    erneut versucht (max. ~10 Versuche); die Engine hat busy_timeout=30s.
    """
    if belegart not in PRAEFIXE:
        raise UngueltigeBelegart(f"Kein Nummernkreis-Präfix für Belegart {belegart!r}")
    if jahr is None:
        jahr = date.today().year
    params = {"belegart": str(belegart), "jahr": jahr, "praefix": PRAEFIXE[belegart]}

    letzter_fehler: OperationalError | None = None
    for versuch in range(_MAX_VERSUCHE):
        try:
            session.execute(_INSERT, params)
            session.execute(_UPDATE, params)
            nummer = session.execute(_SELECT, params).scalar_one()
            return f"{PRAEFIXE[belegart]}-{jahr}-{nummer:04d}"
        except OperationalError as fehler:
            if "database is locked" not in str(fehler):
                raise
            letzter_fehler = fehler
            time.sleep(_BACKOFF_BASIS_SEK * (versuch + 1))
    raise letzter_fehler  # type: ignore[misc]  # nach _MAX_VERSUCHE immer gesetzt
