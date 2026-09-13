"""Vergabe fortlaufender Belegnummern je Belegart und Jahr (Kplus-Stil: ``260001``).

Format (Anwenderwunsch, Sept. 2026): **zweistelliges Jahr + laufende Nummer**,
rein numerisch, ohne Präfix — jede Belegart hat ihren eigenen Zähler, der mit
dem Jahr neu beginnt. Angebot ``260001`` und Rechnung ``260001`` dürfen daher
nebeneinander existieren (Eindeutigkeit gilt je Belegart, siehe
``Beleg.__table_args__``). Der Startwert je Belegart/Jahr ist in den
Einstellungen setzbar, damit nahtlos an die alte Kplus-Nummerierung
angeschlossen werden kann.

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
from sqlmodel import Session, select

from app.models.beleg import Belegart
from app.models.nummernkreis import Nummernkreis
from app.services.fehler import UngueltigeBelegart, WerkplusFehler

# Mindestbreite der laufenden Nummer (wächst automatisch über 9999 hinaus)
STELLEN = 4

_MAX_VERSUCHE = 10
_BACKOFF_BASIS_SEK = 0.05

_INSERT = text(
    "INSERT INTO nummernkreis (belegart, jahr, praefix, letzte_nummer) "
    "VALUES (:belegart, :jahr, '', 0) "
    "ON CONFLICT (belegart, jahr) DO NOTHING"
)
_UPDATE = text(
    "UPDATE nummernkreis SET letzte_nummer = letzte_nummer + 1 "
    "WHERE belegart = :belegart AND jahr = :jahr"
)
_SELECT = text(
    "SELECT letzte_nummer FROM nummernkreis WHERE belegart = :belegart AND jahr = :jahr"
)


def formatiere_belegnr(jahr: int, nummer: int) -> str:
    """``(2026, 1)`` → ``"260001"``: zweistelliges Jahr + laufende Nummer."""
    return f"{jahr % 100:02d}{nummer:0{STELLEN}d}"


def naechste_nummer(session: Session, schluessel: str, jahr: int) -> int:
    """Zieht atomar die nächste laufende Nummer eines Zählers (Belegart oder
    ein anderer Schlüssel wie ``"adresse"``, dort mit Jahr 0).

    Der Aufrufer hält die Transaktion — die Nummer ist erst mit seinem Commit
    endgültig vergeben. Bei "database is locked" wird mit kurzem Backoff
    erneut versucht (max. ~10 Versuche); die Engine hat busy_timeout=30s.
    """
    params = {"belegart": schluessel, "jahr": jahr}
    letzter_fehler: OperationalError | None = None
    for versuch in range(_MAX_VERSUCHE):
        try:
            session.execute(_INSERT, params)
            session.execute(_UPDATE, params)
            return int(session.execute(_SELECT, params).scalar_one())
        except OperationalError as fehler:
            if "database is locked" not in str(fehler):
                raise
            letzter_fehler = fehler
            time.sleep(_BACKOFF_BASIS_SEK * (versuch + 1))
    raise letzter_fehler  # type: ignore[misc]  # nach _MAX_VERSUCHE immer gesetzt


def naechste_belegnr(session: Session, belegart: Belegart, jahr: int | None = None) -> str:
    """Zieht atomar die nächste Belegnummer, z. B. ``260001`` (siehe naechste_nummer)."""
    if belegart not in set(Belegart):
        raise UngueltigeBelegart(f"Kein Nummernkreis für Belegart {belegart!r}")
    if jahr is None:
        jahr = date.today().year
    return formatiere_belegnr(jahr, naechste_nummer(session, str(belegart), jahr))


def stand(session: Session, schluessel: str, jahr: int) -> int:
    """Letzte vergebene Nummer eines Zählers (0 = noch nichts vergeben)."""
    kreis = session.exec(
        select(Nummernkreis).where(Nummernkreis.belegart == schluessel, Nummernkreis.jahr == jahr)
    ).first()
    return kreis.letzte_nummer if kreis else 0


# ------------------------------------------------------------- Einstellungen


def staende(session: Session, jahr: int | None = None) -> dict[Belegart, int]:
    """Letzte vergebene Nummer je Belegart für ``jahr`` (0 = noch nichts vergeben)."""
    if jahr is None:
        jahr = date.today().year
    kreise = session.exec(select(Nummernkreis).where(Nummernkreis.jahr == jahr)).all()
    vorhanden = {k.belegart: k.letzte_nummer for k in kreise}
    return {art: vorhanden.get(str(art), 0) for art in Belegart}


def setze_stand(session: Session, belegart: str, jahr: int, letzte_nummer: int) -> None:
    """Setzt den Zähler (die nächste Nummer ist ``letzte_nummer + 1``).

    Nur nach oben erlaubt: ein Zurücksetzen würde bereits vergebene Nummern
    erneut ausgeben und an der Eindeutigkeit je Belegart scheitern.
    """
    if letzte_nummer < 0:
        raise WerkplusFehler("Der Nummernstand darf nicht negativ sein")
    kreis = session.exec(
        select(Nummernkreis).where(
            Nummernkreis.belegart == str(belegart), Nummernkreis.jahr == jahr
        )
    ).first()
    if kreis is None:
        kreis = Nummernkreis(belegart=str(belegart), jahr=jahr, praefix="", letzte_nummer=0)
    if letzte_nummer < kreis.letzte_nummer:
        raise WerkplusFehler(
            f"Der Nummernstand kann nicht unter die letzte vergebene Nummer "
            f"({kreis.letzte_nummer}) gesetzt werden"
        )
    kreis.letzte_nummer = letzte_nummer
    session.add(kreis)
    session.flush()
