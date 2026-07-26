"""Lagerwirkung der Belege — als Buchungs-/Storno-Hooks am Beleg-Service registriert.

Wirkung nur für Lieferschein (Abgang) und EK-Lieferschein (Wareneingang); alle
anderen Belegarten (Angebot, Auftrag, Rechnung, Gutschrift, Bestellung,
EK-Rechnung) haben KEINE Lagerwirkung.

Fachliche Regeln:
- Negative Bestände sind erlaubt (keine Bestandssperre für Nicht-SN-Artikel).
- SN-pflichtige Positionen brauchen beim Buchen exakt Menge viele zugewiesene SNs.
- Dienstleistungen (ist_lagerartikel=False) erzeugen nie Lagerbewegungen.
- Storno erzeugt Gegenbuchungen (Journal bleibt lückenlos, nichts wird gelöscht).

Die Hooks validieren zuerst vollständig und buchen erst danach — schlägt die
Validierung fehl, bleibt nur der (ungecommittete) Statuswechsel aus setze_status
in der Session; der Aufrufer muss bei einer Hook-Exception rollbacken.

Nebenläufigkeit: ``lager_service.buche`` schreibt den Bestand optimistisch gesperrt
mit Retry fort und wirft nach erschöpften Versuchen ``LagerbuchungFehlgeschlagen``
(Unterklasse von ``WerkplusFehler``). Die Hooks fangen das absichtlich NICHT ab —
teilgebuchte Belege dürfen nicht bestehen bleiben. Der Aufrufer (siehe
``_statuswechsel`` in app/routers/belege.py) rollt zurück und meldet fachlich.
"""

from decimal import Decimal

from sqlmodel import Session, col, select

from app.models.artikel import Artikel
from app.models.beleg import Beleg, Belegart, BelegPosition, BelegPositionSeriennummer
from app.models.lager import Buchungsart, Lagerbewegung
from app.models.seriennummer import Seriennummer, SnStatus
from app.services import beleg_service, lager_service
from app.services.beleg_service import registriere_buchungs_hook, registriere_storno_hook
from app.services.fehler import SnNichtVerfuegbar, SnPflichtVerletzt, StornoNichtMoeglich


def _lagerpositionen(session: Session, beleg: Beleg) -> list[tuple[BelegPosition, Artikel]]:
    """Positionen des Belegs mit Lagerartikel (Freitext und Dienstleistungen fallen raus)."""
    ergebnis: list[tuple[BelegPosition, Artikel]] = []
    positionen = session.exec(
        select(BelegPosition)
        .where(BelegPosition.beleg_id == beleg.id)
        .order_by(col(BelegPosition.pos_nr))
    ).all()
    for position in positionen:
        if position.artikel_id is None:
            continue
        artikel = session.get(Artikel, position.artikel_id)
        if artikel is None or not artikel.ist_lagerartikel:
            continue
        ergebnis.append((position, artikel))
    return ergebnis


def _zugeordnete_sns(session: Session, position: BelegPosition) -> list[Seriennummer]:
    return list(
        session.exec(
            select(Seriennummer)
            .join(
                BelegPositionSeriennummer,
                col(BelegPositionSeriennummer.seriennummer_id) == col(Seriennummer.id),
            )
            .where(BelegPositionSeriennummer.position_id == position.id)
            .order_by(col(Seriennummer.sn))
        ).all()
    )


def _erfasste_sns(session: Session, position: BelegPosition) -> list[Seriennummer]:
    return list(
        session.exec(
            select(Seriennummer)
            .where(Seriennummer.wareneingang_position_id == position.id)
            .order_by(col(Seriennummer.sn))
        ).all()
    )


def _pruefe_sn_anzahl(position: BelegPosition, anzahl: int) -> None:
    if position.menge != position.menge.to_integral_value():
        raise SnPflichtVerletzt(
            f"Position {position.pos_nr}: Menge {position.menge} muss für "
            f"seriennummernpflichtige Artikel ganzzahlig sein"
        )
    if anzahl != int(position.menge):
        raise SnPflichtVerletzt(
            f"Position {position.pos_nr}: {anzahl} Seriennummer(n) erfasst/zugewiesen, "
            f"benötigt werden {int(position.menge)}"
        )


def _positions_ids(session: Session, beleg: Beleg) -> list[int]:
    return list(
        session.exec(select(col(BelegPosition.id)).where(BelegPosition.beleg_id == beleg.id)).all()
    )


def _gegenbuchungen(session: Session, beleg: Beleg, benutzer_id: int | None) -> None:
    """Kehrt alle Nicht-Storno-Bewegungen des Belegs per Storno-Gegenbuchung um."""
    bewegungen = session.exec(
        select(Lagerbewegung).where(
            Lagerbewegung.beleg_id == beleg.id,
            Lagerbewegung.buchungsart != Buchungsart.STORNO,
        )
    ).all()
    for bewegung in bewegungen:
        lager_service.buche(
            session,
            artikel_id=bewegung.artikel_id,
            lager_id=bewegung.lager_id,
            menge=-bewegung.menge,
            buchungsart=Buchungsart.STORNO,
            beleg_id=beleg.id,
            position_id=bewegung.position_id,
            seriennummer_id=bewegung.seriennummer_id,
            benutzer_id=benutzer_id,
        )


def _buche_lieferschein(session: Session, beleg: Beleg, benutzer_id: int | None) -> None:
    """Lieferschein buchen: Abgang je Lagerartikel-Position, SNs werden verkauft."""
    # Validierungslauf — erst wenn alles passt, wird gebucht
    arbeit: list[tuple[BelegPosition, Artikel, list[Seriennummer] | None]] = []
    braucht_standardlager = False
    for position, artikel in _lagerpositionen(session, beleg):
        if artikel.seriennummernpflichtig:
            sns = _zugeordnete_sns(session, position)
            _pruefe_sn_anzahl(position, len(sns))
            for sn in sns:
                if sn.status != SnStatus.AUF_LAGER or sn.lager_id is None:
                    raise SnNichtVerfuegbar(f"Seriennummer {sn.sn} ist nicht (mehr) auf Lager")
            arbeit.append((position, artikel, sns))
        else:
            braucht_standardlager = True
            arbeit.append((position, artikel, None))
    lager = lager_service.standard_lager(session) if braucht_standardlager else None

    for position, artikel, sns in arbeit:
        if sns is not None:
            for sn in sns:
                lager_service.buche(
                    session,
                    artikel_id=artikel.id,
                    lager_id=sn.lager_id,
                    menge=Decimal("-1"),
                    buchungsart=Buchungsart.LIEFERSCHEIN,
                    beleg_id=beleg.id,
                    position_id=position.id,
                    seriennummer_id=sn.id,
                    benutzer_id=benutzer_id,
                )
                sn.status = SnStatus.VERKAUFT
                sn.lieferschein_position_id = position.id
                session.add(sn)
        else:
            lager_service.buche(
                session,
                artikel_id=artikel.id,
                lager_id=lager.id,
                menge=-position.menge,
                buchungsart=Buchungsart.LIEFERSCHEIN,
                beleg_id=beleg.id,
                position_id=position.id,
                benutzer_id=benutzer_id,
            )
    session.flush()


def _storno_lieferschein(session: Session, beleg: Beleg, benutzer_id: int | None) -> None:
    """Lieferschein-Storno: Gegenbuchungen, zugeordnete SNs wieder auf Lager."""
    _gegenbuchungen(session, beleg, benutzer_id)
    pos_ids = _positions_ids(session, beleg)
    if pos_ids:
        sns = session.exec(
            select(Seriennummer).where(
                col(Seriennummer.lieferschein_position_id).in_(pos_ids)
            )
        ).all()
        for sn in sns:
            sn.status = SnStatus.AUF_LAGER
            sn.lieferschein_position_id = None
            session.add(sn)
    session.flush()


def _buche_ek_lieferschein(session: Session, beleg: Beleg, benutzer_id: int | None) -> None:
    """EK-Lieferschein buchen: Wareneingang je Lagerartikel-Position, SNs einbuchen."""
    arbeit: list[tuple[BelegPosition, Artikel, list[Seriennummer] | None]] = []
    for position, artikel in _lagerpositionen(session, beleg):
        if artikel.seriennummernpflichtig:
            sns = _erfasste_sns(session, position)
            _pruefe_sn_anzahl(position, len(sns))
            arbeit.append((position, artikel, sns))
        else:
            arbeit.append((position, artikel, None))
    if not arbeit:
        return
    lager = lager_service.standard_lager(session)

    for position, artikel, sns in arbeit:
        if sns is not None:
            for sn in sns:
                lager_service.buche(
                    session,
                    artikel_id=artikel.id,
                    lager_id=lager.id,
                    menge=Decimal("1"),
                    buchungsart=Buchungsart.WARENEINGANG,
                    beleg_id=beleg.id,
                    position_id=position.id,
                    seriennummer_id=sn.id,
                    benutzer_id=benutzer_id,
                )
                sn.lager_id = lager.id
                session.add(sn)
        else:
            lager_service.buche(
                session,
                artikel_id=artikel.id,
                lager_id=lager.id,
                menge=position.menge,
                buchungsart=Buchungsart.WARENEINGANG,
                beleg_id=beleg.id,
                position_id=position.id,
                benutzer_id=benutzer_id,
            )
    session.flush()


def _storno_ek_lieferschein(session: Session, beleg: Beleg, benutzer_id: int | None) -> None:
    """EK-Lieferschein-Storno: nur wenn alle über diesen Beleg angelegten SNs noch
    auf Lager und keiner Lieferschein-Position zugeordnet sind. SNs bleiben als
    erfasst bestehen (lager_id=None), der Bestand wird per Gegenbuchung ausgebucht.
    """
    pos_ids = _positions_ids(session, beleg)
    sns: list[Seriennummer] = []
    if pos_ids:
        sns = list(
            session.exec(
                select(Seriennummer).where(
                    col(Seriennummer.wareneingang_position_id).in_(pos_ids)
                )
            ).all()
        )
    for sn in sns:
        if sn.status != SnStatus.AUF_LAGER or sn.lieferschein_position_id is not None:
            raise StornoNichtMoeglich(
                f"Seriennummer {sn.sn} ist bereits verkauft/verbaut — "
                f"Storno des Wareneingangs nicht möglich"
            )
        ls_zuordnung = session.exec(
            select(BelegPositionSeriennummer)
            .join(
                BelegPosition,
                col(BelegPosition.id) == col(BelegPositionSeriennummer.position_id),
            )
            .join(Beleg, col(Beleg.id) == col(BelegPosition.beleg_id))
            .where(
                BelegPositionSeriennummer.seriennummer_id == sn.id,
                Beleg.belegart == Belegart.LIEFERSCHEIN,
            )
        ).first()
        if ls_zuordnung is not None:
            raise StornoNichtMoeglich(
                f"Seriennummer {sn.sn} ist einer Lieferschein-Position zugeordnet — "
                f"Storno des Wareneingangs nicht möglich"
            )

    _gegenbuchungen(session, beleg, benutzer_id)
    for sn in sns:
        sn.lager_id = None
        session.add(sn)
    session.flush()


def registriere_lager_hooks() -> None:
    """Registriert die Lager-Hooks am Beleg-Service (idempotent).

    Nur Lieferschein und EK-Lieferschein erhalten Lagerwirkung.
    """
    if beleg_service._buchungs_hooks.get(Belegart.LIEFERSCHEIN) is _buche_lieferschein:
        return  # bereits registriert
    registriere_buchungs_hook(Belegart.LIEFERSCHEIN, _buche_lieferschein)
    registriere_buchungs_hook(Belegart.EK_LIEFERSCHEIN, _buche_ek_lieferschein)
    registriere_storno_hook(Belegart.LIEFERSCHEIN, _storno_lieferschein)
    registriere_storno_hook(Belegart.EK_LIEFERSCHEIN, _storno_ek_lieferschein)
