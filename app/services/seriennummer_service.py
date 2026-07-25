"""Seriennummern-Verwaltung: Erfassung beim Wareneingang, Zuweisung an Lieferscheine.

Designentscheidung: Beim Wareneingang werden Seriennummern-Datensätze bereits im
ENTWURF des EK-Lieferscheins angelegt (status auf_lager, aber lager_id=None =
"noch nicht eingebucht"). Erst der Buchungs-Hook setzt lager_id auf das
Standardlager. "Verfügbar" heißt: status == auf_lager UND lager_id IS NOT NULL.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed.
"""

from sqlmodel import Session, col, select

from app.models.beleg import (
    Beleg,
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
    BelegStatus,
)
from app.models.seriennummer import Seriennummer, SnStatus
from app.services.beleg_service import pruefe_bearbeitbar
from app.services.fehler import SnNichtVerfuegbar, UngueltigeBelegart, WerkplusFehler


def verfuegbare_sns(session: Session, artikel_id: int) -> list[Seriennummer]:
    """Alle verfügbaren SNs eines Artikels (auf Lager UND eingebucht), nach sn sortiert."""
    return list(
        session.exec(
            select(Seriennummer)
            .where(
                Seriennummer.artikel_id == artikel_id,
                Seriennummer.status == SnStatus.AUF_LAGER,
                col(Seriennummer.lager_id).is_not(None),
            )
            .order_by(col(Seriennummer.sn))
        ).all()
    )


def _lade_beleg(session: Session, position: BelegPosition) -> Beleg:
    beleg = session.get(Beleg, position.beleg_id)
    if beleg is None:
        raise WerkplusFehler(f"Beleg zu Position {position.id} nicht gefunden")
    return beleg


def erfasse_wareneingang_sns(
    session: Session, position: BelegPosition, sns: list[str]
) -> list[Seriennummer]:
    """Erfasst Seriennummern zu einer EK-Lieferschein-Position (nur im Entwurf).

    Ersetzt eine frühere Erfassung dieser Position: zuvor angelegte, noch nicht
    eingebuchte SNs (lager_id IS NULL) werden samt Zuordnung gelöscht. Je String
    wird eine Seriennummer (lager_id=None) plus BelegPositionSeriennummer angelegt.
    """
    beleg = _lade_beleg(session, position)
    pruefe_bearbeitbar(beleg)
    if beleg.belegart != Belegart.EK_LIEFERSCHEIN:
        raise UngueltigeBelegart(
            f"SN-Erfassung nur auf EK-Lieferscheinen möglich, Beleg {beleg.belegnr} "
            f"ist {beleg.belegart}"
        )
    if position.artikel_id is None:
        raise WerkplusFehler("SN-Erfassung nur für Artikelpositionen möglich")

    bereinigt = [s.strip() for s in sns]
    if any(not s for s in bereinigt):
        raise SnNichtVerfuegbar("Leere Seriennummer in der Liste")
    if len(set(bereinigt)) != len(bereinigt):
        raise SnNichtVerfuegbar("Doppelte Seriennummer in der Liste")

    # Frühere Erfassung dieser Position ersetzen (nur noch nicht eingebuchte SNs)
    alte_sns = session.exec(
        select(Seriennummer).where(
            Seriennummer.wareneingang_position_id == position.id,
            col(Seriennummer.lager_id).is_(None),
        )
    ).all()
    for alte in alte_sns:
        zuordnungen = session.exec(
            select(BelegPositionSeriennummer).where(
                BelegPositionSeriennummer.seriennummer_id == alte.id
            )
        ).all()
        for zuordnung in zuordnungen:
            session.delete(zuordnung)
        session.delete(alte)
    session.flush()

    for sn_text in bereinigt:
        vorhanden = session.exec(
            select(Seriennummer).where(
                Seriennummer.artikel_id == position.artikel_id,
                Seriennummer.sn == sn_text,
            )
        ).first()
        if vorhanden is not None:
            raise SnNichtVerfuegbar(
                f"Seriennummer {sn_text} existiert bereits für diesen Artikel"
            )

    neue: list[Seriennummer] = []
    for sn_text in bereinigt:
        sn = Seriennummer(
            artikel_id=position.artikel_id,
            sn=sn_text,
            status=SnStatus.AUF_LAGER,
            lager_id=None,
            wareneingang_position_id=position.id,
        )
        session.add(sn)
        session.flush()
        session.add(
            BelegPositionSeriennummer(position_id=position.id, seriennummer_id=sn.id)
        )
        neue.append(sn)
    session.flush()
    return neue


def weise_sns_zu(
    session: Session, position: BelegPosition, seriennummer_ids: list[int]
) -> None:
    """Weist einer Lieferschein-Position (Entwurf) konkrete Seriennummern zu.

    Ersetzt die bestehende Zuordnung der Position. Jede SN muss existieren, zum
    Artikel der Position gehören, verfügbar sein (auf_lager + eingebucht) und darf
    keiner anderen offenen Lieferschein-Position zugeordnet sein.
    """
    beleg = _lade_beleg(session, position)
    pruefe_bearbeitbar(beleg)
    if beleg.belegart != Belegart.LIEFERSCHEIN:
        raise UngueltigeBelegart(
            f"SN-Zuweisung nur auf Lieferscheinen möglich, Beleg {beleg.belegnr} "
            f"ist {beleg.belegart}"
        )
    if position.artikel_id is None:
        raise WerkplusFehler("SN-Zuweisung nur für Artikelpositionen möglich")
    if len(set(seriennummer_ids)) != len(seriennummer_ids):
        raise SnNichtVerfuegbar("Seriennummer mehrfach in der Zuweisung enthalten")

    # Bestehende Zuordnung der Position ersetzen
    bisherige = session.exec(
        select(BelegPositionSeriennummer).where(
            BelegPositionSeriennummer.position_id == position.id
        )
    ).all()
    for zuordnung in bisherige:
        session.delete(zuordnung)
    session.flush()

    for sn_id in seriennummer_ids:
        sn = session.get(Seriennummer, sn_id)
        if sn is None:
            raise SnNichtVerfuegbar(f"Seriennummer mit ID {sn_id} existiert nicht")
        if sn.artikel_id != position.artikel_id:
            raise SnNichtVerfuegbar(
                f"Seriennummer {sn.sn} gehört nicht zum Artikel der Position"
            )
        if sn.status != SnStatus.AUF_LAGER or sn.lager_id is None:
            raise SnNichtVerfuegbar(f"Seriennummer {sn.sn} ist nicht verfügbar")
        anderweitig = session.exec(
            select(BelegPositionSeriennummer)
            .join(
                BelegPosition,
                col(BelegPosition.id) == col(BelegPositionSeriennummer.position_id),
            )
            .join(Beleg, col(Beleg.id) == col(BelegPosition.beleg_id))
            .where(
                BelegPositionSeriennummer.seriennummer_id == sn_id,
                BelegPositionSeriennummer.position_id != position.id,
                Beleg.belegart == Belegart.LIEFERSCHEIN,
                Beleg.status == BelegStatus.ENTWURF,
            )
        ).first()
        if anderweitig is not None:
            raise SnNichtVerfuegbar(
                f"Seriennummer {sn.sn} ist bereits einer anderen offenen "
                f"Lieferschein-Position zugeordnet"
            )
        session.add(
            BelegPositionSeriennummer(position_id=position.id, seriennummer_id=sn_id)
        )
    session.flush()


def sns_je_position(
    session: Session, position_ids: list[int]
) -> dict[int, list[Seriennummer]]:
    """Zugeordnete Seriennummern je Position (Helfer für UI/PDF); leere Listen inklusive."""
    ergebnis: dict[int, list[Seriennummer]] = {pid: [] for pid in position_ids}
    if not position_ids:
        return ergebnis
    zeilen = session.exec(
        select(BelegPositionSeriennummer, Seriennummer)
        .join(
            Seriennummer,
            col(Seriennummer.id) == col(BelegPositionSeriennummer.seriennummer_id),
        )
        .where(col(BelegPositionSeriennummer.position_id).in_(position_ids))
    ).all()
    for zuordnung, sn in zeilen:
        ergebnis[zuordnung.position_id].append(sn)
    for sns in ergebnis.values():
        sns.sort(key=lambda sn: sn.sn)
    return ergebnis
