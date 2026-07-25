"""Anlagenverwaltung: beim Kunden verbaute Geräte, Wartungstermine, Serviceeinsätze.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed.
"""

from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models.adresse import Adresse
from app.models.anlage import Anlage, Serviceeinsatz
from app.models.artikel import Artikel
from app.models.beleg import Beleg, Belegart, BelegPosition, BelegStatus
from app.models.seriennummer import Seriennummer, SnStatus
from app.services.fehler import SnNichtVerfuegbar, WerkplusFehler
from app.services.seriennummer_service import sns_je_position

# Vorlauf, ab dem eine Wartung als "fällig" gilt (Vertrag mit Dashboard/Liste)
WARTUNGS_VORLAUF_TAGE = 30


def lege_anlage_an(
    session: Session,
    *,
    adresse_id: int,
    artikel_id: int,
    seriennummer_id: int,
    standort: str = "",
    inbetriebnahme: date | None = None,
    garantieende: date | None = None,
    naechste_wartung: date | None = None,
    notizen: str = "",
) -> Anlage:
    """Legt eine Anlage zu einer vorhandenen Seriennummer an.

    Die SN muss existieren, zum Artikel gehören und darf noch keiner Anlage
    zugeordnet sein. Sie wird auf beim_kunden_verbaut gesetzt.
    """
    sn = session.get(Seriennummer, seriennummer_id)
    if sn is None:
        raise WerkplusFehler(f"Seriennummer mit ID {seriennummer_id} existiert nicht")
    if sn.artikel_id != artikel_id:
        raise WerkplusFehler(f"Seriennummer {sn.sn} gehört nicht zum gewählten Artikel")
    vorhanden = session.exec(
        select(Anlage).where(Anlage.seriennummer_id == seriennummer_id)
    ).first()
    if vorhanden is not None:
        raise WerkplusFehler(
            f"Zur Seriennummer {sn.sn} existiert bereits eine Anlage (ID {vorhanden.id})"
        )

    sn.status = SnStatus.BEIM_KUNDEN_VERBAUT
    session.add(sn)
    anlage = Anlage(
        adresse_id=adresse_id,
        artikel_id=artikel_id,
        seriennummer_id=seriennummer_id,
        standort=standort.strip(),
        inbetriebnahme=inbetriebnahme,
        garantieende=garantieende,
        naechste_wartung=naechste_wartung,
        notizen=notizen.strip(),
    )
    session.add(anlage)
    session.flush()
    return anlage


def lege_bestandsanlage_an(
    session: Session,
    *,
    adresse_id: int,
    artikel_id: int,
    sn_text: str,
    **felder,
) -> Anlage:
    """Bestandsanlage (Altbestand): legt die Seriennummer ad hoc an, dann die Anlage.

    Die SN entsteht direkt mit status beim_kunden_verbaut und ohne Lager.
    Duplikat je Artikel → SnNichtVerfuegbar.
    """
    sn_text = sn_text.strip()
    if not sn_text:
        raise SnNichtVerfuegbar("Seriennummer darf nicht leer sein")
    vorhanden = session.exec(
        select(Seriennummer).where(
            Seriennummer.artikel_id == artikel_id,
            Seriennummer.sn == sn_text,
        )
    ).first()
    if vorhanden is not None:
        raise SnNichtVerfuegbar(
            f"Seriennummer {sn_text} existiert bereits für diesen Artikel"
        )
    sn = Seriennummer(
        artikel_id=artikel_id,
        sn=sn_text,
        status=SnStatus.BEIM_KUNDEN_VERBAUT,
        lager_id=None,
    )
    session.add(sn)
    session.flush()
    return lege_anlage_an(
        session,
        adresse_id=adresse_id,
        artikel_id=artikel_id,
        seriennummer_id=sn.id,
        **felder,
    )


def anlagen_kandidaten_aus_beleg(
    session: Session, beleg: Beleg
) -> list[tuple[BelegPosition, Artikel, list[Seriennummer]]]:
    """Anlagen-Kandidaten eines gebuchten Verkaufs-Lieferscheins.

    Liefert je SN-pflichtiger Artikelposition die zugewiesenen Seriennummern,
    die noch keiner Anlage zugeordnet sind. Positionen ohne freie SNs entfallen.
    """
    if beleg.belegart != Belegart.LIEFERSCHEIN or beleg.status not in (
        BelegStatus.GEDRUCKT,
        BelegStatus.ERLEDIGT,
    ):
        return []
    positionen = session.exec(
        select(BelegPosition)
        .where(BelegPosition.beleg_id == beleg.id)
        .order_by(col(BelegPosition.pos_nr))
    ).all()
    relevante: list[tuple[BelegPosition, Artikel]] = []
    for position in positionen:
        if position.artikel_id is None:
            continue
        artikel = session.get(Artikel, position.artikel_id)
        if artikel is not None and artikel.seriennummernpflichtig:
            relevante.append((position, artikel))
    if not relevante:
        return []

    sn_map = sns_je_position(session, [p.id for p, _ in relevante])
    alle_sn_ids = [sn.id for sns in sn_map.values() for sn in sns]
    verbaut: set[int] = set()
    if alle_sn_ids:
        verbaut = set(
            session.exec(
                select(Anlage.seriennummer_id).where(
                    col(Anlage.seriennummer_id).in_(alle_sn_ids)
                )
            ).all()
        )

    kandidaten: list[tuple[BelegPosition, Artikel, list[Seriennummer]]] = []
    for position, artikel in relevante:
        freie = [sn for sn in sn_map.get(position.id, []) if sn.id not in verbaut]
        if freie:
            kandidaten.append((position, artikel, freie))
    return kandidaten


def faellige_wartungen(session: Session, stichtag: date | None = None) -> list[Anlage]:
    """Aktive Anlagen mit naechste_wartung <= Stichtag + 30 Tage, aufsteigend sortiert."""
    grenze = (stichtag or date.today()) + timedelta(days=WARTUNGS_VORLAUF_TAGE)
    return list(
        session.exec(
            select(Anlage)
            .where(
                col(Anlage.aktiv).is_(True),
                col(Anlage.naechste_wartung).is_not(None),
                Anlage.naechste_wartung <= grenze,
            )
            .order_by(col(Anlage.naechste_wartung))
        ).all()
    )


def suche_anlagen(
    session: Session,
    q: str = "",
    nur_faellig: bool = False,
    stichtag: date | None = None,
) -> list[tuple[Anlage, Adresse, Artikel, Seriennummer]]:
    """Anlagenliste mit Kunde/Artikel/SN für die UI; optional Textsuche + Wartungsfilter."""
    stmt = (
        select(Anlage, Adresse, Artikel, Seriennummer)
        .join(Adresse, col(Adresse.id) == col(Anlage.adresse_id))
        .join(Artikel, col(Artikel.id) == col(Anlage.artikel_id))
        .join(Seriennummer, col(Seriennummer.id) == col(Anlage.seriennummer_id))
    )
    if nur_faellig:
        grenze = (stichtag or date.today()) + timedelta(days=WARTUNGS_VORLAUF_TAGE)
        stmt = stmt.where(
            col(Anlage.aktiv).is_(True),
            col(Anlage.naechste_wartung).is_not(None),
            Anlage.naechste_wartung <= grenze,
        ).order_by(col(Anlage.naechste_wartung))
    else:
        stmt = stmt.order_by(col(Anlage.id).desc())
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            col(Adresse.firma).ilike(muster)
            | col(Artikel.bezeichnung).ilike(muster)
            | col(Seriennummer.sn).ilike(muster)
            | col(Anlage.standort).ilike(muster)
        )
    return list(session.exec(stmt).all())


def verkaufte_anlagenfreie_sns(
    session: Session,
) -> list[tuple[Seriennummer, Artikel]]:
    """Verkaufte Seriennummern ohne Anlage — Kandidaten fürs freie Anlage-Formular."""
    vergeben = select(Anlage.seriennummer_id)
    return list(
        session.exec(
            select(Seriennummer, Artikel)
            .join(Artikel, col(Artikel.id) == col(Seriennummer.artikel_id))
            .where(
                Seriennummer.status == SnStatus.VERKAUFT,
                col(Seriennummer.id).not_in(vergeben),
            )
            .order_by(col(Artikel.artikelnr), col(Seriennummer.sn))
        ).all()
    )


def erfasse_serviceeinsatz(
    session: Session,
    anlage: Anlage,
    *,
    datum: date,
    beschreibung: str,
    benutzer_id: int | None = None,
    naechste_wartung: date | None = None,
) -> Serviceeinsatz:
    """Erfasst einen Serviceeinsatz; setzt optional den nächsten Wartungstermin mit."""
    einsatz = Serviceeinsatz(
        anlage_id=anlage.id,
        datum=datum,
        beschreibung=beschreibung.strip(),
        benutzer_id=benutzer_id,
    )
    session.add(einsatz)
    if naechste_wartung is not None:
        anlage.naechste_wartung = naechste_wartung
        session.add(anlage)
    session.flush()
    return einsatz
