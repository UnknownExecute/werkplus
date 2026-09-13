"""Artikelstamm (Kplus-Stil): Suche über Nummer/Matchcode/EAN/Alternativnummer,
Filter nach Warengruppe, Artikelart und Lieferant, Bestand mit reserviert/bestellt,
Detail mit Reitern (Stammdaten, Lieferanten, Staffelpreise, Bestand, Set)."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.formparse import (
    MAX_AUFSCHLAG_PROZENT,
    parse_betrag,
    parse_menge,
    parse_prozent,
)
from app.models import (
    ARTIKELART_LABELS,
    UST_SAETZE,
    Adresse,
    AdressTyp,
    Artikel,
    ArtikelLieferant,
    ArtikelStaffel,
    Benutzer,
    Lager,
    Lagerbestand,
    Seriennummer,
    SetKomponente,
)
from app.pagination import Blaetterwerk, blaettere
from app.services import artikel_service, artikel_set_service, berechnung
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.services.fehler import WerkplusFehler
from app.templating import templates

router = APIRouter(
    prefix="/artikel", tags=["artikel"], dependencies=[Depends(bereich_wache("artikel"))]
)

SEITENGROESSE = 50

# Reine Textfelder des Artikelformulars (getrimmt übernommen)
TEXTFELDER = (
    "artikelnr", "matchcode", "ean", "alternativnr", "hersteller", "bezeichnung",
    "langtext", "bemerkung", "warengruppe", "unterwarengruppe",
)


def _wahr(wert) -> bool:
    return str(wert or "").strip().lower() in ("true", "on", "1")


def _suche_artikel(  # noqa: PLR0913
    session: Session,
    q: str,
    warengruppe: str = "",
    artikelart: str = "",
    lieferant_id: str = "",
    seite: int = 1,
) -> tuple[list[Artikel], Blaetterwerk]:
    """Eine Seite Artikel (Kplus „Artikel suchen"): Suchbegriff über Nummer,
    Matchcode, EAN, Alternativnummer, Bezeichnung, Warengruppe; Filter nach
    Warengruppe, Artikelart und Lieferant."""
    stmt = select(Artikel)
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            col(Artikel.artikelnr).ilike(muster)
            | col(Artikel.matchcode).ilike(muster)
            | col(Artikel.ean).ilike(muster)
            | col(Artikel.alternativnr).ilike(muster)
            | col(Artikel.bezeichnung).ilike(muster)
            | col(Artikel.warengruppe).ilike(muster)
        )
    if warengruppe:
        stmt = stmt.where(Artikel.warengruppe == warengruppe)
    if artikelart == "set":
        stmt = stmt.where(col(Artikel.ist_set).is_(True))
    elif artikelart == "lager":
        stmt = stmt.where(col(Artikel.ist_lagerartikel).is_(True), col(Artikel.ist_set).is_(False))
    elif artikelart == "dienstleistung":
        stmt = stmt.where(col(Artikel.ist_lagerartikel).is_(False), col(Artikel.ist_set).is_(False))
    if lieferant_id.strip().isdigit():
        stmt = stmt.where(
            col(Artikel.id).in_(
                select(ArtikelLieferant.artikel_id).where(
                    ArtikelLieferant.adresse_id == int(lieferant_id)
                )
            )
        )
    return blaettere(
        session,
        stmt.order_by(Artikel.artikelnr),
        seite,
        groesse=SEITENGROESSE,
        basis_url="/artikel",
        params={"q": q, "warengruppe": warengruppe, "artikelart": artikelart,
                "lieferant_id": lieferant_id},
        htmx_url="/artikel/suche",
        htmx_target="#artikel-tabelle",
    )


def _bestaende_je_artikel(
    session: Session, artikel_ids: list[int] | None = None
) -> dict[int, Decimal]:
    """Summe Lagerbestand.menge je Artikel — Decimal-sicher in Python summiert.

    Mit ``artikel_ids`` werden nur die Bestände der angezeigten Seite geladen.
    """
    if artikel_ids is not None and not artikel_ids:
        return {}
    stmt = select(Lagerbestand)
    if artikel_ids is not None:
        stmt = stmt.where(col(Lagerbestand.artikel_id).in_(artikel_ids))
    summen: dict[int, Decimal] = {}
    for bestand in session.exec(stmt).all():
        summen[bestand.artikel_id] = summen.get(bestand.artikel_id, Decimal("0")) + bestand.menge
    return summen


def _listen_kontext(session: Session, artikel: list[Artikel]) -> dict:
    ids = [a.id for a in artikel]
    return {
        "artikel_liste": artikel,
        "bestaende": _bestaende_je_artikel(session, ids),
        "reserviert": artikel_service.reserviert_je_artikel(session, ids),
        "bestellt": artikel_service.bestellt_je_artikel(session, ids),
    }


def _filter_kontext(session: Session) -> dict:
    warengruppen = sorted(
        {w for w in session.exec(select(Artikel.warengruppe).distinct()).all() if w}
    )
    lieferanten = list(
        session.exec(
            select(Adresse)
            .where(col(Adresse.typ).in_([AdressTyp.LIEFERANT, AdressTyp.BEIDE]))
            .where(col(Adresse.aktiv).is_(True))
            .order_by(Adresse.firma)
        ).all()
    )
    return {
        "warengruppen": warengruppen,
        "lieferanten": lieferanten,
        "artikelart_labels": ARTIKELART_LABELS,
    }


def _artikelnr_vergeben(session: Session, artikelnr: str, ausser_id: int | None = None) -> bool:
    """Prüft, ob die Artikelnummer bereits von einem (anderen) Artikel belegt ist."""
    stmt = select(Artikel).where(Artikel.artikelnr == artikelnr)
    if ausser_id is not None:
        stmt = stmt.where(Artikel.id != ausser_id)
    return session.exec(stmt).first() is not None


def _artikel_aus_form(artikel: Artikel, formular) -> Artikel:
    """Formularwerte übernehmen (neu + bearbeiten); ``formular`` = rohes Starlette-Formular."""
    try:
        ust_satz = int(str(formular.get("ust_satz", "20")))
    except ValueError:
        ust_satz = -1
    if ust_satz not in UST_SAETZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unzulässiger USt-Satz: {ust_satz}",
        )
    for feld in TEXTFELDER:
        setattr(artikel, feld, str(formular.get(feld, "") or "").strip())
    artikel.einheit = str(formular.get("einheit", "") or "").strip() or "Stk"
    artikel.ust_satz = ust_satz
    # Kalkulation: EK ist rein abgeleitet (Server autoritativ); VK per Kalkulation
    # vorbelegt, aber überschreibbar — ein eingegebener VK > 0 wird übernommen.
    alter_vk, alter_lp = artikel.vk_preis, artikel.listenpreis_netto
    artikel.listenpreis_netto = parse_betrag(
        str(formular.get("listenpreis_netto", "0") or "0"), "Listenpreis"
    )
    artikel.rabatt1_prozent = parse_prozent(
        str(formular.get("rabatt1_prozent", "0") or "0"), "EK-Rabatt 1"
    )
    artikel.rabatt2_prozent = parse_prozent(
        str(formular.get("rabatt2_prozent", "0") or "0"), "EK-Rabatt 2"
    )
    artikel.aufschlag_prozent = parse_prozent(
        str(formular.get("aufschlag_prozent", "0") or "0"), "Aufschlag",
        min_wert=Decimal("-100"), max_wert=MAX_AUFSCHLAG_PROZENT,
    )
    artikel.ek_preis = berechnung.kalkuliere_ek(
        artikel.listenpreis_netto, artikel.rabatt1_prozent, artikel.rabatt2_prozent
    )
    eingegeben_vk = parse_betrag(str(formular.get("vk_preis", "0") or "0"), "Verkaufspreis")
    artikel.vk_preis = (
        eingegeben_vk
        if eingegeben_vk > 0
        else berechnung.kalkuliere_vk(artikel.ek_preis, artikel.aufschlag_prozent)
    )
    artikel.mindest_vk = parse_betrag(str(formular.get("mindest_vk", "0") or "0"), "Mindest-VK")
    artikel.mindestbestand = parse_menge(str(formular.get("mindestbestand", "0") or "0"))
    preis_geaendert = artikel.vk_preis != alter_vk or artikel.listenpreis_netto != alter_lp
    if artikel.id is not None and preis_geaendert:
        artikel.letzte_preisaenderung = date.today()
    artikel.seriennummernpflichtig = _wahr(formular.get("seriennummernpflichtig"))
    artikel.ist_lagerartikel = _wahr(formular.get("ist_lagerartikel"))
    artikel.ist_set = _wahr(formular.get("ist_set"))
    artikel.aktiv = _wahr(formular.get("aktiv"))
    return artikel


@router.get("")
def artikel_liste(  # noqa: PLR0913
    request: Request,
    q: str = "",
    warengruppe: str = "",
    artikelart: str = "",
    lieferant_id: str = "",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel, pag = _suche_artikel(session, q, warengruppe, artikelart, lieferant_id, seite)
    gefiltert = bool(q.strip() or warengruppe or artikelart or lieferant_id)
    return templates.TemplateResponse(
        request,
        "artikel/liste.html",
        {
            **_listen_kontext(session, artikel),
            **_filter_kontext(session),
            "pag": pag,
            "gefiltert": gefiltert,
            "q": q,
            "warengruppe": warengruppe,
            "artikelart": artikelart,
            "lieferant_id": lieferant_id,
        },
    )


@router.get("/suche")
def artikel_suche(  # noqa: PLR0913
    request: Request,
    q: str = "",
    warengruppe: str = "",
    artikelart: str = "",
    lieferant_id: str = "",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen (inkl. Blätter-Zeile)."""
    artikel, pag = _suche_artikel(session, q, warengruppe, artikelart, lieferant_id, seite)
    return templates.TemplateResponse(
        request,
        "partials/artikel_zeilen.html",
        {
            **_listen_kontext(session, artikel),
            "pag": pag,
            "gefiltert": bool(q.strip() or warengruppe or artikelart or lieferant_id),
        },
    )


@router.get("/neu")
def artikel_neu_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
):
    leer = Artikel(artikelnr="", bezeichnung="")
    return templates.TemplateResponse(
        request,
        "artikel/form.html",
        {"artikel": leer, "neu": True, "ust_saetze": UST_SAETZE},
    )


@router.post("/neu")
async def artikel_neu(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    formular = await request.form()
    # Transientes Objekt — wird bei Duplikat nur zur Formular-Anzeige verwendet
    artikel = _artikel_aus_form(Artikel(artikelnr="", bezeichnung=""), formular)
    if not artikel.artikelnr or not artikel.bezeichnung:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Artikelnummer und Bezeichnung sind Pflicht",
        )
    if _artikelnr_vergeben(session, artikel.artikelnr):
        return templates.TemplateResponse(
            request,
            "artikel/form.html",
            {
                "artikel": artikel,
                "neu": True,
                "ust_saetze": UST_SAETZE,
                "fehler": f"Artikelnummer {artikel.artikelnr} ist bereits vergeben.",
            },
        )
    session.add(artikel)
    session.commit()
    session.refresh(artikel)
    return RedirectResponse(f"/artikel/{artikel.id}", status_code=status.HTTP_302_FOUND)


def _lade_artikel(session: Session, artikel_id: int) -> Artikel:
    artikel = session.get(Artikel, artikel_id)
    if artikel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")
    return artikel


def _set_daten(session: Session, set_artikel: Artikel) -> dict:
    """Set-Komponenten (mit Komponentenartikel) und auswählbare Nicht-Set-Artikel."""
    zeilen = [
        (komp, session.get(Artikel, komp.komponente_artikel_id))
        for komp in artikel_set_service.komponenten(session, set_artikel.id)
    ]
    verfuegbare = [
        a
        for a in session.exec(select(Artikel).order_by(Artikel.artikelnr)).all()
        if not a.ist_set and a.id != set_artikel.id
    ]
    return {"set_komponenten": zeilen, "verfuegbare_artikel": verfuegbare}


def _lieferanten_kontext(session: Session, artikel: Artikel) -> dict:
    eintraege = artikel_service.lieferanten(session, artikel.id)
    namen = {
        a.id: a
        for a in session.exec(
            select(Adresse).where(col(Adresse.id).in_([e.adresse_id for e in eintraege] or [0]))
        ).all()
    }
    return {
        "artikel": artikel,
        "lieferanten_eintraege": [(e, namen.get(e.adresse_id)) for e in eintraege],
        "lieferanten": _filter_kontext(session)["lieferanten"],
    }


def _detail_daten(session: Session, artikel_id: int) -> dict:
    """Bestände, Seriennummern, Lieferanten, Staffeln für die Detailseite eines Artikels."""
    bestaende = list(
        session.exec(
            select(Lagerbestand, Lager)
            .join(Lager, col(Lagerbestand.lager_id) == col(Lager.id))
            .where(Lagerbestand.artikel_id == artikel_id)
        ).all()
    )
    seriennummern = list(
        session.exec(
            select(Seriennummer).where(Seriennummer.artikel_id == artikel_id).order_by(
                col(Seriennummer.sn)
            )
        ).all()
    )
    artikel = session.get(Artikel, artikel_id)
    gesamt = sum((b.menge for b, _ in bestaende), Decimal("0"))
    reserviert = artikel_service.reserviert(session, artikel_id)
    bestellt = artikel_service.bestellt(session, artikel_id)
    letzter_lieferant = (
        session.get(Adresse, artikel.letzter_lieferant_id)
        if artikel is not None and artikel.letzter_lieferant_id
        else None
    )
    daten = {
        "bestaende": bestaende,
        "bestand_gesamt": gesamt,
        "reserviert": reserviert,
        "bestellt": bestellt,
        "verfuegbar": gesamt - reserviert,
        "seriennummern": seriennummern,
        "ust_saetze": UST_SAETZE,
        "staffeln": artikel_service.staffeln(session, artikel_id),
        "letzter_lieferant": letzter_lieferant,
    }
    if artikel is not None:
        daten.update(_lieferanten_kontext(session, artikel))
        if artikel.ist_set:
            daten.update(_set_daten(session, artikel))
    return daten


@router.get("/{artikel_id}")
def artikel_detail(
    request: Request,
    artikel_id: int,
    tab: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    return templates.TemplateResponse(
        request,
        "artikel/detail.html",
        {
            **_detail_daten(session, artikel_id),
            "artikel": artikel,
            "aktiver_tab": tab or "stammdaten",
        },
    )


@router.post("/{artikel_id}")
async def artikel_bearbeiten(
    request: Request,
    artikel_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    formular = await request.form()
    neue_nr = str(formular.get("artikelnr", "") or "").strip()
    if _artikelnr_vergeben(session, neue_nr, ausser_id=artikel_id):
        # Transientes Anzeige-Objekt mit den eingegebenen Werten — nichts wird gespeichert
        anzeige = _artikel_aus_form(Artikel(artikelnr="", bezeichnung=""), formular)
        anzeige.id = artikel_id
        return templates.TemplateResponse(
            request,
            "artikel/detail.html",
            {
                **_detail_daten(session, artikel_id),
                "artikel": anzeige,
                "aktiver_tab": "stammdaten",
                "fehler": f"Artikelnummer {anzeige.artikelnr} ist bereits vergeben.",
            },
        )
    _artikel_aus_form(artikel, formular)
    session.add(artikel)
    session.commit()
    return RedirectResponse(f"/artikel/{artikel_id}", status_code=status.HTTP_302_FOUND)


# ----------------------------------------------------------------- Lieferanten


def _lieferanten_partial(request: Request, session: Session, artikel: Artikel):
    return templates.TemplateResponse(
        request, "partials/artikel_lieferanten.html", _lieferanten_kontext(session, artikel)
    )


@router.post("/{artikel_id}/lieferanten")
async def lieferant_speichern(
    request: Request,
    artikel_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Bezugsquelle anlegen/aktualisieren (gleicher Lieferant = überschreiben)."""
    artikel = _lade_artikel(session, artikel_id)
    formular = await request.form()
    adresse_text = str(formular.get("adresse_id", "") or "").strip()
    adresse = session.get(Adresse, int(adresse_text)) if adresse_text.isdigit() else None
    if adresse is None or not adresse.ist_lieferant:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Lieferant fehlt"
        )
    try:
        lieferzeit = int(str(formular.get("lieferzeit_tage", "0") or "0"))
    except ValueError:
        lieferzeit = 0
    artikel_service.setze_lieferant(
        session,
        artikel,
        adresse.id,
        lieferanten_artikelnr=str(formular.get("lieferanten_artikelnr", "") or ""),
        ek_preis=parse_betrag(str(formular.get("ek_preis", "0") or "0"), "EK-Preis"),
        lieferzeit_tage=lieferzeit,
        notiz=str(formular.get("notiz", "") or ""),
        ist_hauptlieferant=_wahr(formular.get("ist_hauptlieferant")),
    )
    session.commit()
    return _lieferanten_partial(request, session, artikel)


def _lade_lieferant(session: Session, artikel: Artikel, eintrag_id: int) -> ArtikelLieferant:
    eintrag = session.get(ArtikelLieferant, eintrag_id)
    if eintrag is None or eintrag.artikel_id != artikel.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lieferant nicht gefunden"
        )
    return eintrag


@router.post("/{artikel_id}/lieferanten/{eintrag_id}/haupt")
def lieferant_haupt(
    request: Request,
    artikel_id: int,
    eintrag_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    eintrag = _lade_lieferant(session, artikel, eintrag_id)
    artikel_service.setze_hauptlieferant(session, artikel, eintrag)
    session.commit()
    return _lieferanten_partial(request, session, artikel)


@router.post("/{artikel_id}/lieferanten/{eintrag_id}/loeschen")
def lieferant_loeschen(
    request: Request,
    artikel_id: int,
    eintrag_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    eintrag = _lade_lieferant(session, artikel, eintrag_id)
    artikel_service.loesche_lieferant(session, artikel, eintrag)
    session.commit()
    return _lieferanten_partial(request, session, artikel)


# ---------------------------------------------------------------- Staffelpreise


def _staffeln_partial(request: Request, session: Session, artikel: Artikel):
    return templates.TemplateResponse(
        request,
        "partials/artikel_staffeln.html",
        {"artikel": artikel, "staffeln": artikel_service.staffeln(session, artikel.id)},
    )


@router.post("/{artikel_id}/staffeln")
def staffel_speichern(
    request: Request,
    artikel_id: int,
    ab_menge: str = Form("0"),
    vk_preis: str = Form("0"),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    try:
        artikel_service.setze_staffel(
            session, artikel, parse_menge(ab_menge), parse_betrag(vk_preis, "Staffelpreis")
        )
    except WerkplusFehler as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    session.commit()
    return _staffeln_partial(request, session, artikel)


@router.post("/{artikel_id}/staffeln/{staffel_id}/loeschen")
def staffel_loeschen(
    request: Request,
    artikel_id: int,
    staffel_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    staffel = session.get(ArtikelStaffel, staffel_id)
    if staffel is not None and staffel.artikel_id == artikel.id:
        session.delete(staffel)
        session.commit()
    return _staffeln_partial(request, session, artikel)


# ------------------------------------------------------------- Set-Komponenten


def _set_komponenten_partial(request: Request, session: Session, set_artikel: Artikel):
    return templates.TemplateResponse(
        request,
        "partials/set_komponenten.html",
        {"artikel": set_artikel, **_set_daten(session, set_artikel)},
    )


@router.post("/{artikel_id}/set-komponenten")
def set_komponente_hinzufuegen(
    request: Request,
    artikel_id: int,
    komponente_artikel_id: int = Form(...),
    menge: str = Form("1"),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Fügt dem Set eine Komponente hinzu — nur Nicht-Set-Artikel, nicht sich selbst."""
    set_artikel = _lade_artikel(session, artikel_id)
    komponente = session.get(Artikel, komponente_artikel_id)
    # Nur echte Nicht-Set-Artikel als Komponente; Sets und Selbstbezug still ignorieren.
    if komponente is not None and not komponente.ist_set and komponente.id != set_artikel.id:
        naechster_sort = (
            max(
                (k.sort for k in artikel_set_service.komponenten(session, artikel_id)),
                default=-1,
            )
            + 1
        )
        session.add(
            SetKomponente(
                set_artikel_id=artikel_id,
                komponente_artikel_id=komponente_artikel_id,
                menge=parse_menge(menge),
                sort=naechster_sort,
            )
        )
        session.commit()
    return _set_komponenten_partial(request, session, set_artikel)


@router.post("/{artikel_id}/set-komponenten/{komp_id}/loeschen")
def set_komponente_loeschen(
    request: Request,
    artikel_id: int,
    komp_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Entfernt eine Komponente aus dem Set."""
    set_artikel = _lade_artikel(session, artikel_id)
    komponente = session.get(SetKomponente, komp_id)
    if komponente is not None and komponente.set_artikel_id == artikel_id:
        session.delete(komponente)
        session.commit()
    return _set_komponenten_partial(request, session, set_artikel)
