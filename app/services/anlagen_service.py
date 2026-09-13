"""Anlagenverwaltung (Kplus „Anlagenstamm"): beim Kunden installierte Anlagen,
Komponenten, Wartungstermine/-vertrag, Serviceeinsätze, Auftrag aus Anlage.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed.
"""

from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models.adresse import Adresse
from app.models.anlage import Anlage, AnlageKomponente, Serviceeinsatz
from app.models.artikel import Artikel
from app.models.beleg import Beleg, Belegart, BelegPosition, BelegStatus
from app.models.seriennummer import Seriennummer, SnStatus
from app.services import beleg_service, nummernkreis_service
from app.services.fehler import SnNichtVerfuegbar, WerkplusFehler
from app.services.seriennummer_service import sns_je_position

# Vorlauf, ab dem eine Wartung als "fällig" gilt (Vertrag mit Dashboard/Liste)
WARTUNGS_VORLAUF_TAGE = 30
ANLAGENR_BASIS = 1000
ANLAGENR_SCHLUESSEL = "anlage"

# Felder des Anlagenstamms, die frei aus dem Formular übernommen werden
STAMMFELDER = (
    "bezeichnung", "kostenstelle", "hersteller", "typ", "seriennummer_text", "standort",
    "technische_info", "art", "anlagengruppe", "untergruppe", "notizen",
    "vertrag_nr", "vertrag_notiz",
)


def naechste_anlagenr(session: Session) -> str:
    """Zieht atomar die nächste Anlagenummer (``1001``, ``1002`` …)."""
    nummer = nummernkreis_service.naechste_nummer(session, ANLAGENR_SCHLUESSEL, 0)
    return str(ANLAGENR_BASIS + nummer)


def monate_spaeter(datum: date, monate: int) -> date:
    """Datum + n Monate (Tag wird bei kürzeren Monaten gekappt)."""
    monat = datum.month - 1 + monate
    jahr = datum.year + monat // 12
    monat = monat % 12 + 1
    tag = min(datum.day, [31, 29 if jahr % 4 == 0 and (jahr % 100 != 0 or jahr % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][monat - 1])
    return date(jahr, monat, tag)


def _neue_anlage(session: Session, adresse_id: int, **felder) -> Anlage:
    anlage = Anlage(adresse_id=adresse_id, anlagenr=naechste_anlagenr(session))
    for name, wert in felder.items():
        setattr(anlage, name, wert.strip() if isinstance(wert, str) else wert)
    return anlage


def lege_anlage_an(
    session: Session,
    *,
    adresse_id: int,
    artikel_id: int,
    seriennummer_id: int,
    **felder,
) -> Anlage:
    """Legt eine Anlage zu einer vorhandenen Lager-Seriennummer an.

    Die SN muss existieren, zum Artikel gehören und darf noch keiner Anlage
    zugeordnet sein. Sie wird auf beim_kunden_verbaut gesetzt; Bezeichnung,
    Hersteller und SN-Text werden aus Artikel/SN vorbelegt, sofern leer.
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
    artikel = session.get(Artikel, artikel_id)
    sn.status = SnStatus.BEIM_KUNDEN_VERBAUT
    session.add(sn)
    anlage = _neue_anlage(session, adresse_id, **felder)
    anlage.artikel_id = artikel_id
    anlage.seriennummer_id = seriennummer_id
    if not anlage.seriennummer_text:
        anlage.seriennummer_text = sn.sn
    if artikel is not None:
        if not anlage.bezeichnung:
            anlage.bezeichnung = artikel.bezeichnung
        if not anlage.hersteller:
            anlage.hersteller = artikel.hersteller
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
    """Bestandsanlage mit Lagerartikel: legt die Seriennummer ad hoc an, dann die Anlage.

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


def lege_freie_anlage_an(session: Session, *, adresse_id: int, **felder) -> Anlage:
    """Kplus: Anlage ohne Lagerartikel/Lager-SN (Fremdgerät, Altbestand) — nur Stammdaten."""
    anlage = _neue_anlage(session, adresse_id, **felder)
    if not anlage.bezeichnung:
        raise WerkplusFehler("Eine Anlagebezeichnung ist Pflicht")
    session.add(anlage)
    session.flush()
    return anlage


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


def suche_anlagen(  # noqa: PLR0913
    session: Session,
    q: str = "",
    nur_faellig: bool = False,
    stichtag: date | None = None,
    art: str = "",
    gruppe: str = "",
    nur_vertrag: bool = False,
) -> list[tuple[Anlage, Adresse, Artikel | None, Seriennummer | None]]:
    """Kplus „Anlagensuche": Textsuche über Nummer, Bezeichnung, Hersteller, Typ, SN,
    Standort, Kostenstelle und Kunde; Filter Art, Gruppe, Wartung fällig, nur mit Vertrag.

    Artikel und Lager-Seriennummer kommen per Outer-Join mit — für Anlagen aus dem
    Lager, deren Textfelder leer sind (Altbestand), greifen sie als Anzeige-Fallback.
    """
    stmt = (
        select(Anlage, Adresse, Artikel, Seriennummer)
        .join(Adresse, col(Adresse.id) == col(Anlage.adresse_id))
        .join(Artikel, col(Artikel.id) == col(Anlage.artikel_id), isouter=True)
        .join(Seriennummer, col(Seriennummer.id) == col(Anlage.seriennummer_id), isouter=True)
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
    if art:
        stmt = stmt.where(Anlage.art == art)
    if gruppe:
        stmt = stmt.where(Anlage.anlagengruppe == gruppe)
    if nur_vertrag:
        stmt = stmt.where(
            (col(Anlage.vertrag_nr) != "") | col(Anlage.vertrag_bis).is_not(None)
        )
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            col(Anlage.anlagenr).ilike(muster)
            | col(Anlage.bezeichnung).ilike(muster)
            | col(Anlage.hersteller).ilike(muster)
            | col(Anlage.typ).ilike(muster)
            | col(Anlage.seriennummer_text).ilike(muster)
            | col(Anlage.standort).ilike(muster)
            | col(Anlage.kostenstelle).ilike(muster)
            | col(Adresse.firma).ilike(muster)
            | col(Adresse.adressnr).ilike(muster)
            | col(Artikel.bezeichnung).ilike(muster)
            | col(Seriennummer.sn).ilike(muster)
        )
    return list(session.exec(stmt).all())


def gruppen(session: Session) -> list[str]:
    return sorted({g for g in session.exec(select(Anlage.anlagengruppe).distinct()).all() if g})


def verkaufte_anlagenfreie_sns(
    session: Session,
) -> list[tuple[Seriennummer, Artikel]]:
    """Verkaufte Seriennummern ohne Anlage — Kandidaten fürs freie Anlage-Formular."""
    vergeben = select(Anlage.seriennummer_id).where(col(Anlage.seriennummer_id).is_not(None))
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
    """Erfasst einen Serviceeinsatz; setzt den nächsten Wartungstermin — explizit
    oder, bei gesetztem Wartungsintervall, automatisch Datum + Intervall."""
    einsatz = Serviceeinsatz(
        anlage_id=anlage.id,
        datum=datum,
        beschreibung=beschreibung.strip(),
        benutzer_id=benutzer_id,
    )
    session.add(einsatz)
    if naechste_wartung is None and anlage.wartungsintervall_monate > 0:
        naechste_wartung = monate_spaeter(datum, anlage.wartungsintervall_monate)
    if naechste_wartung is not None:
        anlage.naechste_wartung = naechste_wartung
        session.add(anlage)
    session.flush()
    return einsatz


# ------------------------------------------------------------------ Komponenten


def komponenten(session: Session, anlage_id: int) -> list[AnlageKomponente]:
    return list(
        session.exec(
            select(AnlageKomponente)
            .where(AnlageKomponente.anlage_id == anlage_id)
            .order_by(col(AnlageKomponente.sort), col(AnlageKomponente.id))
        ).all()
    )


def fuege_komponente_hinzu(session: Session, anlage: Anlage, **felder) -> AnlageKomponente:
    komponente = AnlageKomponente(anlage_id=anlage.id)
    for name, wert in felder.items():
        setattr(komponente, name, wert.strip() if isinstance(wert, str) else wert)
    if not komponente.bezeichnung:
        raise WerkplusFehler("Eine Komponente braucht eine Bezeichnung")
    komponente.sort = max((k.sort for k in komponenten(session, anlage.id)), default=-1) + 1
    session.add(komponente)
    session.flush()
    return komponente


# ---------------------------------------------------------------- Belege / Auftrag


def belege_der_anlage(session: Session, anlage_id: int) -> list[Beleg]:
    return list(
        session.exec(
            select(Beleg).where(Beleg.anlage_id == anlage_id).order_by(col(Beleg.id).desc())
        ).all()
    )


def auftrag_aus_anlage(
    session: Session,
    anlage: Anlage,
    benutzer_id: int | None = None,
    auftragsart: str = "service",
    betreff: str = "",
    belegart: Belegart = Belegart.AUFTRAG,
) -> Beleg:
    """Kplus „Auftrag" aus dem Anlagenstamm: Serviceauftrag (oder Angebot) für den
    Kunden der Anlage, Kopf mit Anlagendaten vorbelegt und mit der Anlage verknüpft."""
    if belegart not in (Belegart.AUFTRAG, Belegart.ANGEBOT):
        raise WerkplusFehler("Aus einer Anlage entsteht ein Auftrag oder ein Angebot")
    adresse = session.get(Adresse, anlage.adresse_id)
    if adresse is None:
        raise WerkplusFehler("Kunde der Anlage nicht gefunden")
    zeilen = [f"Anlage {anlage.anlagenr}: {anlage.kurz}"]
    if anlage.hersteller or anlage.typ:
        zeilen.append(f"Hersteller/Typ: {anlage.hersteller} {anlage.typ}".strip())
    if anlage.seriennummer_text:
        zeilen.append(f"Seriennummer: {anlage.seriennummer_text}")
    if anlage.standort:
        zeilen.append(f"Standort: {anlage.standort}")
    beleg = beleg_service.erstelle_beleg(
        session,
        belegart,
        adresse,
        benutzer_id=benutzer_id,
        betreff=betreff.strip() or f"Service {anlage.kurz}",
        auftragsart=auftragsart if belegart == Belegart.AUFTRAG else "",
        kopftext="\n".join(zeilen),
        anlage_id=anlage.id,
    )
    return beleg
