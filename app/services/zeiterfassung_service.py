"""Arbeitszeit-/Regieerfassung: Zeilen pflegen, summieren, in Belege übernehmen.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed
(gleiches Muster wie ``app.services.beleg_service``).

Berechtigungsvertrag (WICHTIG, wird hier durchgesetzt, nicht erst im Router):

* Die Bereichsstufe (``recht_zeiterfassung``) prüft die Router-Wache
  (``bereich_wache("zeiterfassung")``): GET ⇒ ansehen, POST/DELETE ⇒ bearbeiten.
* Die **Eigentümerprüfung je Datensatz** liegt hier: :func:`ist_sichtbar_fuer`
  bzw. :func:`lade_zeile_fuer`. Eigene Zeilen darf jeder Berechtigte sehen und
  ändern; **fremde Zeilen nur ein Admin** (``ist_admin`` aus dem Rollenprofil).
  Ohne diese Prüfung wäre jede Zeile über ihre ID erreichbar (IDOR).
* Die Rechte werden dabei stets **frisch aus der Datenbank** gelesen (``ist_admin``
  liest das Rollenprofil), niemals aus der Session — ein Rechteentzug wirkt
  damit sofort und nicht erst nach dem nächsten Login.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlmodel import Session, col, select

from app.dependencies import ist_admin
from app.models.beleg import Beleg, BelegPosition
from app.models.benutzer import Benutzer
from app.models.zeiterfassung import Zeiterfassung
from app.services import beleg_service, berechnung
from app.services.fehler import WerkplusFehler, ZeitBereitsVerrechnet

# Einheit der aus Zeiten erzeugten Belegpositionen
EINHEIT_STUNDE = "Std"


@dataclass(frozen=True)
class Zeitsumme:
    """Summe einer Menge von Zeitzeilen — durchgehend ``Decimal``, nie ``float``."""

    stunden: Decimal = Decimal("0")
    betrag: Decimal = Decimal("0.00")
    anzahl: int = 0
    offene_anzahl: int = 0


# --------------------------------------------------------------- Eigentümerprüfung


def ist_sichtbar_fuer(session: Session, zeile: Zeiterfassung, benutzer: Benutzer) -> bool:
    """Darf ``benutzer`` diese Zeile sehen/ändern? Eigene ja, fremde nur als Admin."""
    if zeile.benutzer_id == benutzer.id:
        return True
    return ist_admin(session, benutzer)


def lade_zeile_fuer(
    session: Session, zeile_id: int, benutzer: Benutzer
) -> Zeiterfassung | None:
    """Zeile laden, aber nur wenn ``benutzer`` sie sehen darf — sonst ``None``.

    Bewusst dieselbe Antwort für „existiert nicht" und „gehört jemand anderem":
    der Aufrufer macht daraus ein 404 und verrät damit nicht, ob die ID vergeben
    ist (IDOR-Schutz ohne Informationsleck).
    """
    zeile = session.get(Zeiterfassung, zeile_id)
    if zeile is None or not ist_sichtbar_fuer(session, zeile, benutzer):
        return None
    return zeile


# ------------------------------------------------------------------- Lesen/Suchen


def _basis_stmt() -> Any:
    """Zeilen samt erfassendem Mitarbeiter, neueste zuerst."""
    return (
        select(Zeiterfassung, Benutzer)
        .join(Benutzer, col(Benutzer.id) == col(Zeiterfassung.benutzer_id), isouter=True)
        .order_by(col(Zeiterfassung.datum).desc(), col(Zeiterfassung.id).desc())
    )


def zeilen_stmt(
    *,
    benutzer_id: int | None = None,
    beleg_id: int | None = None,
    nur_offen: bool = False,
) -> Any:
    """Statement für die Übersicht (paginierbar über ``app.pagination.blaettere``).

    ``benutzer_id=None`` liefert die Zeilen ALLER Mitarbeiter — das darf nur ein
    Admin aufrufen, siehe Berechtigungsvertrag im Modul-Docstring.
    """
    stmt = _basis_stmt()
    if benutzer_id is not None:
        stmt = stmt.where(Zeiterfassung.benutzer_id == benutzer_id)
    if beleg_id is not None:
        stmt = stmt.where(Zeiterfassung.beleg_id == beleg_id)
    if nur_offen:
        stmt = stmt.where(Zeiterfassung.verrechnet == False)  # noqa: E712
    return stmt


def zeilen_je_beleg(
    session: Session, beleg_id: int, *, nur_offen: bool = False
) -> list[Zeiterfassung]:
    """Alle Zeitzeilen eines Belegs (älteste zuerst — Reihenfolge der Übernahme)."""
    stmt = (
        select(Zeiterfassung)
        .where(Zeiterfassung.beleg_id == beleg_id)
        .order_by(col(Zeiterfassung.datum), col(Zeiterfassung.id))
    )
    if nur_offen:
        stmt = stmt.where(Zeiterfassung.verrechnet == False)  # noqa: E712
    return list(session.exec(stmt).all())


def zeilen_je_beleg_mit_benutzer(
    session: Session, beleg_id: int
) -> list[tuple[Zeiterfassung, Benutzer | None]]:
    """Zeitzeilen eines Belegs samt Mitarbeiter — für die Anzeige im Belegeditor."""
    stmt = (
        select(Zeiterfassung, Benutzer)
        .join(Benutzer, col(Benutzer.id) == col(Zeiterfassung.benutzer_id), isouter=True)
        .where(Zeiterfassung.beleg_id == beleg_id)
        .order_by(col(Zeiterfassung.datum), col(Zeiterfassung.id))
    )
    return list(session.exec(stmt).all())


def zeilen_je_benutzer(
    session: Session, benutzer_id: int, *, nur_offen: bool = False
) -> list[Zeiterfassung]:
    """Alle Zeitzeilen eines Mitarbeiters, neueste zuerst."""
    stmt = (
        select(Zeiterfassung)
        .where(Zeiterfassung.benutzer_id == benutzer_id)
        .order_by(col(Zeiterfassung.datum).desc(), col(Zeiterfassung.id).desc())
    )
    if nur_offen:
        stmt = stmt.where(Zeiterfassung.verrechnet == False)  # noqa: E712
    return list(session.exec(stmt).all())


def zeilen_betrag(zeile: Zeiterfassung) -> Decimal:
    """Betrag einer Zeile: Stunden × Stundensatz, kaufmännisch gerundet.

    Rundet genau wie ``berechnung.positions_netto`` (ohne Rabatt) — die Summe der
    Erfassung stimmt damit auf den Cent mit den erzeugten Belegpositionen überein.
    """
    return berechnung.positions_netto(zeile.stunden, zeile.stundensatz, Decimal("0"))


def summe_je_filter(
    session: Session,
    *,
    benutzer_id: int | None = None,
    beleg_id: int | None = None,
    nur_offen: bool = False,
) -> Zeitsumme:
    """Summe über ALLE Treffer eines Filters (nicht nur eine Listenseite).

    Für die Übersicht: eine Seitensumme wäre für Lohn-/Regieauswertungen wertlos.
    """
    stmt = select(Zeiterfassung)
    if benutzer_id is not None:
        stmt = stmt.where(Zeiterfassung.benutzer_id == benutzer_id)
    if beleg_id is not None:
        stmt = stmt.where(Zeiterfassung.beleg_id == beleg_id)
    if nur_offen:
        stmt = stmt.where(Zeiterfassung.verrechnet == False)  # noqa: E712
    return summiere(list(session.exec(stmt).all()))


def summiere(zeilen: list[Zeiterfassung]) -> Zeitsumme:
    """Stunden- und Betragssumme über die Zeilen — ausschließlich ``Decimal``."""
    stunden = sum((z.stunden for z in zeilen), Decimal("0"))
    betrag = sum((zeilen_betrag(z) for z in zeilen), Decimal("0"))
    return Zeitsumme(
        stunden=stunden,
        betrag=berechnung.runde(betrag),
        anzahl=len(zeilen),
        offene_anzahl=sum(1 for z in zeilen if not z.verrechnet),
    )


# ------------------------------------------------------------------- Schreiben


def _pruefe_werte(stunden: Decimal, stundensatz: Decimal) -> None:
    """Fachliche Mindestprüfung: Stunden > 0, Stundensatz nicht negativ.

    Die Formatprüfung/Obergrenzen macht ``app.formparse`` (parse_menge/parse_betrag);
    hier stehen nur die fachlichen Regeln, damit sie auch bei Service-Aufrufen
    ohne HTTP greifen.
    """
    if stunden <= Decimal("0"):
        raise WerkplusFehler("Bitte eine Stundenzahl größer als 0 erfassen.")
    if stundensatz < Decimal("0"):
        raise WerkplusFehler("Der Stundensatz darf nicht negativ sein.")


def _pruefe_aenderbar(zeile: Zeiterfassung) -> None:
    if zeile.verrechnet:
        raise ZeitBereitsVerrechnet(
            "Diese Arbeitszeit ist bereits als Belegposition verrechnet und "
            "kann nicht mehr geändert oder gelöscht werden."
        )


def erfasse_zeit(  # noqa: PLR0913
    session: Session,
    *,
    benutzer_id: int,
    stunden: Decimal,
    stundensatz: Decimal,
    datum: date | None = None,
    taetigkeit: str = "",
    beleg_id: int | None = None,
) -> Zeiterfassung:
    """Legt eine neue Zeitzeile an (noch nicht verrechnet)."""
    _pruefe_werte(stunden, stundensatz)
    zeile = Zeiterfassung(
        beleg_id=beleg_id,
        benutzer_id=benutzer_id,
        datum=datum or date.today(),
        stunden=stunden,
        stundensatz=stundensatz,
        taetigkeit=taetigkeit.strip(),
        verrechnet=False,
    )
    session.add(zeile)
    session.flush()
    return zeile


def aendere_zeit(  # noqa: PLR0913
    session: Session,
    zeile: Zeiterfassung,
    *,
    stunden: Decimal,
    stundensatz: Decimal,
    datum: date | None = None,
    taetigkeit: str = "",
    beleg_id: int | None = None,
) -> Zeiterfassung:
    """Übernimmt geänderte Werte einer noch nicht verrechneten Zeile.

    Der Aufrufer MUSS vorher :func:`lade_zeile_fuer` benutzt haben — sonst ist die
    Eigentümerprüfung umgangen.
    """
    _pruefe_aenderbar(zeile)
    _pruefe_werte(stunden, stundensatz)
    zeile.datum = datum or zeile.datum
    zeile.stunden = stunden
    zeile.stundensatz = stundensatz
    zeile.taetigkeit = taetigkeit.strip()
    zeile.beleg_id = beleg_id
    session.add(zeile)
    session.flush()
    return zeile


def loesche_zeit(session: Session, zeile: Zeiterfassung) -> None:
    """Löscht eine noch nicht verrechnete Zeile (verrechnete sind gesperrt)."""
    _pruefe_aenderbar(zeile)
    session.delete(zeile)
    session.flush()


# ------------------------------------------------------------------- Verrechnung


def _bezeichnung(zeile: Zeiterfassung, mitarbeiter: Benutzer | None) -> str:
    """Sprechende Positionsbezeichnung: Tätigkeit, Mitarbeiter und Datum."""
    teile = [zeile.taetigkeit.strip() or "Arbeitszeit"]
    if mitarbeiter is not None and mitarbeiter.name.strip():
        teile.append(mitarbeiter.name.strip())
    teile.append(zeile.datum.strftime("%d.%m.%Y"))
    return " – ".join(teile)


def uebernehme_in_beleg(
    session: Session, beleg: Beleg, *, ust_satz: int | None = None
) -> list[BelegPosition]:
    """Erzeugt aus den offenen Zeiten des Belegs Positionen und markiert sie verrechnet.

    **Je Zeile eine Position** (bewusst NICHT je Tätigkeit/Mitarbeiter summiert):
    Datum, Mitarbeiter und Tätigkeit stehen dann einzeln auf der Rechnung. Genau
    das braucht der Handwerker bei einer Regierechnung, wenn der Kunde nachfragt
    („welcher Monteur war wann wie lange da?"). Eine Gruppierung würde diese
    Nachvollziehbarkeit einbüßen, ohne einen Vorteil zu bringen — die Zeilenzahl
    ist durch die tatsächlich geleisteten Tage begrenzt.

    Menge = Stunden, Einzelpreis = Stundensatz der Zeile, Einheit „Std",
    Rabatt bewusst 0 % (nicht der Kundenrabatt), damit der Positionsbetrag exakt
    der in der Erfassung angezeigten Summe entspricht.

    Doppelte Verrechnung ist ausgeschlossen: übernommene Zeilen bekommen
    ``verrechnet = True`` und werden beim nächsten Aufruf nicht mehr gefunden
    (ein zweiter Klick legt also nichts mehr an, sondern meldet „keine offenen
    Zeiten").

    Wirft ``BelegGesperrt``, wenn der Beleg nicht mehr im Entwurf ist (gedruckte
    Belege sind unveränderlich), und ``WerkplusFehler``, wenn es nichts zu
    übernehmen gibt.
    """
    beleg_service.pruefe_bearbeitbar(beleg)
    offene = zeilen_je_beleg(session, beleg.id, nur_offen=True)
    if not offene:
        raise WerkplusFehler(
            f"Zu Beleg {beleg.belegnr} gibt es keine offenen (noch nicht "
            f"verrechneten) Arbeitszeiten."
        )
    namen = {
        b.id: b
        for b in session.exec(
            select(Benutzer).where(
                col(Benutzer.id).in_([z.benutzer_id for z in offene])
            )
        ).all()
    }
    positionen: list[BelegPosition] = []
    for zeile in offene:
        positionen.append(
            beleg_service.fuege_position_hinzu(
                session,
                beleg,
                None,
                bezeichnung=_bezeichnung(zeile, namen.get(zeile.benutzer_id)),
                langtext="",
                menge=zeile.stunden,
                einzelpreis=zeile.stundensatz,
                rabatt_prozent=Decimal("0"),
                einheit=EINHEIT_STUNDE,
                ust_satz=ust_satz,
            )
        )
        zeile.verrechnet = True
        session.add(zeile)
    session.flush()
    return positionen
