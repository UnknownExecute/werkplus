"""Lagerführung: Bestände (Upsert je Artikel+Lager) und lückenloses Bewegungsjournal.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed.
Negative Bestände sind fachlich erlaubt (Lieferschein bucht auch bei zu wenig Bestand).

Nebenläufigkeit: Die Bestandsfortschreibung ist gegen Lost Updates gesichert —
siehe ``_bestand_fortschreiben`` für Verfahren und Begründung.
"""

import logging
import time
from decimal import Decimal

from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.models.lager import Buchungsart, Lager, Lagerbestand, Lagerbewegung
from app.services.fehler import LagerbuchungFehlgeschlagen, WerkplusFehler

logger = logging.getLogger(__name__)

# Retry-Parameter analog app/services/nummernkreis_service.py (linearer Backoff,
# in Summe ~2,75 s). Die Engine hat zusätzlich busy_timeout=30 s.
_MAX_VERSUCHE = 10
_BACKOFF_BASIS_SEK = 0.05

# Bestandszeile anlegen, falls sie noch fehlt — ohne Read-Modify-Write und ohne
# Unique-Verletzung, wenn zwei Buchungen denselben Artikel/Lager zuerst anfassen.
# Startwert '0' ist genau die Textform, die DecimalStr für Decimal("0") schreibt.
# Dieses (ggf. wirkungslose) INSERT eröffnet zugleich die Schreibtransaktion und
# serialisiert damit konkurrierende Buchungen über den SQLite-Write-Lock — genau
# wie das relative UPDATE im Nummernkreis.
_INSERT_BESTAND = text(
    "INSERT INTO lagerbestand (artikel_id, lager_id, menge) "
    "VALUES (:artikel_id, :lager_id, '0') "
    "ON CONFLICT (artikel_id, lager_id) DO NOTHING"
)


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


def _bestand_fortschreiben(
    session: Session, *, artikel_id: int, lager_id: int, delta: Decimal
) -> Decimal:
    """Schreibt den Bestand nebenläufigkeitssicher um ``delta`` fort (neuer Wert als Rückgabe).

    WARUM hier KEIN relatives SQL-UPDATE (``SET menge = menge + :delta``) wie in
    ``nummernkreis_service``: ``Lagerbestand.menge`` liegt über den
    ``DecimalStr``-TypeDecorator als TEXT in der Datenbank (SQLite kennt keinen
    Decimal-Typ). SQLite würde bei ``menge + :delta`` beide Operanden numerisch
    casten, in IEEE-754-Fließkomma rechnen und das Ergebnis als Float-Text in die
    TEXT-Spalte zurückschreiben ('0.30000000000000004' statt '0.3'). Damit wäre
    die geforderte Decimal-Genauigkeit still und leise zerstört.

    Deshalb: **optimistisches Sperren (Compare-and-Swap)**. Gerechnet wird mit
    ``Decimal`` in Python, geschrieben wird nur, wenn der Ist-Wert seit dem Lesen
    unverändert ist (``UPDATE ... WHERE menge = :alter_wert``). Betrifft das
    UPDATE 0 Zeilen, hat ein anderer Schreiber dazwischen gefunkt — dann wird neu
    gelesen und erneut versucht, statt einen fremden Wert zu überschreiben.
    Der Alt-Wert geht durch denselben ``DecimalStr``-Bind wie beim Schreiben, der
    Textvergleich trifft also exakt die gespeicherte Darstellung.

    Zusätzlich Retry mit Backoff gegen ``OperationalError``/"database is locked".
    Hinweis: Hat die Transaktion des Aufrufers vor dem Buchen schon gelesen, kann
    SQLite den Schreibzugriff mit SQLITE_BUSY_SNAPSHOT abweisen; das ist ohne
    Rollback der (fremden) Transaktion nicht behebbar. Nach ``_MAX_VERSUCHE``
    wird darum ein fachlicher ``LagerbuchungFehlgeschlagen`` geworfen, damit der
    Aufrufer zurückrollen und sauber melden kann — statt eines nackten 500ers.
    """
    params = {"artikel_id": artikel_id, "lager_id": lager_id}
    letzter_fehler: OperationalError | None = None

    for versuch in range(_MAX_VERSUCHE):
        try:
            # 1) Zeile sicherstellen (und Schreibtransaktion eröffnen)
            session.execute(_INSERT_BESTAND, params)
            # 2) Ist-Wert exakt als Decimal lesen (DecimalStr liest TEXT -> Decimal)
            zeile_id, alt = session.execute(
                select(Lagerbestand.id, Lagerbestand.menge).where(
                    Lagerbestand.artikel_id == artikel_id,
                    Lagerbestand.lager_id == lager_id,
                )
            ).one()
            neu = alt + delta
            # 3) Compare-and-Swap: nur schreiben, wenn niemand dazwischen geschrieben hat
            ergebnis = session.execute(
                update(Lagerbestand)
                .where(Lagerbestand.id == zeile_id, Lagerbestand.menge == alt)
                .values(menge=neu)
            )
            if ergebnis.rowcount == 1:
                return neu
            logger.warning(
                "Bestandskonflikt (Artikel %s, Lager %s) — Versuch %s wird wiederholt",
                artikel_id,
                lager_id,
                versuch + 1,
            )
        except OperationalError as fehler:
            if "database is locked" not in str(fehler):
                raise
            letzter_fehler = fehler
        time.sleep(_BACKOFF_BASIS_SEK * (versuch + 1))

    raise LagerbuchungFehlgeschlagen(
        f"Bestand für Artikel {artikel_id} in Lager {lager_id} konnte nach "
        f"{_MAX_VERSUCHE} Versuchen nicht fortgeschrieben werden "
        f"(Datenbank belegt) — bitte erneut versuchen"
    ) from letzter_fehler


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

    Upsertet den Lagerbestand (menge += ...) nebenläufigkeitssicher und schreibt
    IMMER einen Journal-Eintrag (Lagerbewegung) mit Belegreferenz und Zeitstempel.
    Negative Bestände sind fachlich erlaubt und werden nur protokolliert.
    Wirft ``LagerbuchungFehlgeschlagen``, wenn die Bestandsfortschreibung auch
    nach mehreren Versuchen nicht möglich war.
    """
    neuer_bestand = _bestand_fortschreiben(
        session, artikel_id=artikel_id, lager_id=lager_id, delta=menge
    )
    if neuer_bestand < 0:
        # Bewusste fachliche Entscheidung: kein Abbruch, nur Warnung (siehe Modulkopf).
        logger.warning(
            "Negativer Bestand %s für Artikel %s in Lager %s (Buchungsart %s)",
            neuer_bestand,
            artikel_id,
            lager_id,
            buchungsart,
        )

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
