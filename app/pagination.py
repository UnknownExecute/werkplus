"""Serverseitige Pagination für die langen Listen (Belege, Artikel, Adressen).

Statt sämtliche Datensätze zu laden wird nur die angeforderte Seite geholt
(``limit``/``offset``); die Gesamtanzahl kommt aus einer eigenen ``count``-Abfrage
— es wird also nie die ganze Tabelle nur zum Zählen materialisiert.

``Blaetterwerk`` bündelt alles, was ``partials/pagination.html`` zum Rendern der
Blätter-Navigation braucht (inkl. der Links mit erhaltenen Filtern).
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from sqlmodel import Session, func, select

# Anzahl der direkt anklickbaren Seitenzahlen rund um die aktuelle Seite
FENSTER = 5


@dataclass(frozen=True)
class Blaetterwerk:
    """Zustand der Blätter-Navigation einer Liste.

    ``basis`` ist ein URL-Präfix, an das das Template nur noch ``seite=N``
    anhängt (die aktiven Filter sind bereits enthalten). ``htmx_basis`` ist das
    Gegenstück für den HTMX-Partial-Endpunkt; ist es leer, blättern normale Links.
    """

    seite: int
    seiten: int
    gesamt: int
    groesse: int
    basis: str
    htmx_basis: str = ""
    htmx_target: str = ""
    fenster: tuple[int, ...] = ()

    @property
    def hat_zurueck(self) -> bool:
        return self.seite > 1

    @property
    def hat_weiter(self) -> bool:
        return self.seite < self.seiten

    @property
    def von(self) -> int:
        """1-basierte Nummer des ersten Datensatzes dieser Seite (0 bei leerer Liste)."""
        if self.gesamt == 0:
            return 0
        return (self.seite - 1) * self.groesse + 1

    @property
    def bis(self) -> int:
        """1-basierte Nummer des letzten Datensatzes dieser Seite."""
        return min(self.seite * self.groesse, self.gesamt)


def _basis_url(url: str, params: dict[str, Any]) -> str:
    """URL-Präfix mit allen aktiven Filtern; ``seite=N`` wird direkt angehängt.

    Leere Werte fallen weg, damit die Links nicht mit ``q=&typ=`` zugemüllt werden.
    """
    paare = [(name, str(wert)) for name, wert in params.items() if wert not in (None, "")]
    query = urlencode(paare)
    return f"{url}?{query}&" if query else f"{url}?"


def _fenster(seite: int, seiten: int, breite: int = FENSTER) -> tuple[int, ...]:
    """Seitenzahlen rund um die aktuelle Seite (höchstens ``breite`` Stück)."""
    if seiten <= breite:
        return tuple(range(1, seiten + 1))
    start = min(max(1, seite - breite // 2), seiten - breite + 1)
    return tuple(range(start, start + breite))


def gesamtanzahl(session: Session, stmt: Any) -> int:
    """Trefferanzahl eines Statements — ohne die Datensätze selbst zu laden."""
    zaehl_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    return int(session.exec(zaehl_stmt).one())


def blaettere(
    session: Session,
    stmt: Any,
    seite: int,
    *,
    groesse: int,
    basis_url: str,
    params: dict[str, Any] | None = None,
    htmx_url: str = "",
    htmx_target: str = "",
) -> tuple[list, Blaetterwerk]:
    """Lädt genau eine Seite des Statements und baut das passende Blätterwerk.

    ``params`` sind die aktiven Filter (q, status_filter, typ …). Sie landen in
    den Blätter-Links, damit die Auswahl beim Blättern erhalten bleibt.
    Eine zu große ``seite`` wird auf die letzte vorhandene Seite geklemmt.
    """
    params = params or {}
    gesamt = gesamtanzahl(session, stmt)
    seiten = max(1, -(-gesamt // groesse))  # Aufrunden ohne float
    seite = min(max(1, seite), seiten)
    zeilen = list(session.exec(stmt.limit(groesse).offset((seite - 1) * groesse)).all())
    return zeilen, Blaetterwerk(
        seite=seite,
        seiten=seiten,
        gesamt=gesamt,
        groesse=groesse,
        basis=_basis_url(basis_url, params),
        htmx_basis=_basis_url(htmx_url, params) if htmx_url else "",
        htmx_target=htmx_target,
        fenster=_fenster(seite, seiten),
    )
