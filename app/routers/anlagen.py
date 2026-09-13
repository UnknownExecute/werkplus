"""Anlagen-UI (Kplus „Anlagenstamm"): Anlagensuche mit Filtern, Anlage anlegen
(frei, mit Lagerartikel oder aus Lieferschein), Detail mit Reitern Anlagedaten |
Wartung | Komponenten | Wartungsvertrag | Belege, Auftrag aus Anlage, Anlagenblatt.

Die Routen sind bewusst dünn — die Fachlogik liegt in app.services.anlagen_service.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.formparse import parse_betrag
from app.models import (
    ANLAGE_ARTEN,
    AUFTRAGSART_LABELS,
    Adresse,
    AdressTyp,
    Anlage,
    AnlageKomponente,
    Ansprechpartner,
    Artikel,
    Auftragsart,
    Beleg,
    Belegart,
    BelegStatus,
    Benutzer,
    Seriennummer,
    Serviceeinsatz,
)
from app.routers.belege import BELEGART_LABELS, STATUS_BADGES, STATUS_LABELS
from app.services import anlagen_service, firma_service
from app.services.berechtigung import bereich_wache, darf_bereich  # noqa: E402
from app.services.fehler import WerkplusFehler
from app.templating import templates

router = APIRouter(
    prefix="/anlagen", tags=["anlagen"], dependencies=[Depends(bereich_wache("anlagen"))]
)

DATUMSFELDER = ("herstellungsdatum", "inbetriebnahme", "garantieende", "naechste_wartung",
                "vertrag_von", "vertrag_bis")


def _parse_datum(text: str | None) -> date | None:
    if not text or not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Ungültiges Datum: {text!r}",
        ) from exc


def _int_oder_none(wert) -> int | None:
    text = str(wert or "").strip()
    return int(text) if text.isdigit() else None


def _stammfelder(formular) -> dict:
    """Freie Stammdaten + Datumsfelder + Vertrag aus dem rohen Formular."""
    felder = {name: str(formular.get(name, "") or "") for name in anlagen_service.STAMMFELDER}
    for name in DATUMSFELDER:
        felder[name] = _parse_datum(str(formular.get(name, "") or ""))
    try:
        felder["wartungsintervall_monate"] = max(
            0, int(str(formular.get("wartungsintervall_monate", "0") or "0"))
        )
    except ValueError:
        felder["wartungsintervall_monate"] = 0
    felder["vertrag_pauschale"] = parse_betrag(
        str(formular.get("vertrag_pauschale", "0") or "0"), "Vertragspauschale"
    )
    felder["ansprechpartner_id"] = _int_oder_none(formular.get("ansprechpartner_id"))
    felder["lieferant_id"] = _int_oder_none(formular.get("lieferant_id"))
    return felder


def _lade_anlage(session: Session, anlage_id: int) -> Anlage:
    anlage = session.get(Anlage, anlage_id)
    if anlage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anlage nicht gefunden")
    return anlage


def _lade_gebuchten_lieferschein(session: Session, beleg_id: int) -> Beleg:
    """Beleg für den von_beleg-Modus: muss ein gebuchter Verkaufs-Lieferschein sein."""
    beleg = session.get(Beleg, beleg_id)
    if (
        beleg is None
        or beleg.belegart != Belegart.LIEFERSCHEIN
        or beleg.status not in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kein gebuchter Lieferschein",
        )
    return beleg


def _listen_kontext(  # noqa: PLR0913
    session: Session, q: str, wartung: str, art: str = "", gruppe: str = "", vertrag: str = ""
) -> dict:
    heute = date.today()
    zeilen = anlagen_service.suche_anlagen(
        session, q=q, nur_faellig=wartung == "faellig", art=art, gruppe=gruppe,
        nur_vertrag=bool(vertrag),
    )
    return {
        "zeilen": zeilen,
        "heute": heute,
        "grenze": heute + timedelta(days=anlagen_service.WARTUNGS_VORLAUF_TAGE),
        "q": q,
        "wartung": wartung,
        "art": art,
        "gruppe": gruppe,
        "vertrag": vertrag,
        "arten": [a for a in ANLAGE_ARTEN if a],
        "gruppen": anlagen_service.gruppen(session),
    }


def _auswahllisten(session: Session) -> dict:
    kunden = session.exec(
        select(Adresse)
        .where(
            col(Adresse.typ).in_([AdressTyp.KUNDE, AdressTyp.BEIDE]),
            col(Adresse.aktiv).is_(True),
        )
        .order_by(col(Adresse.firma))
    ).all()
    lieferanten = session.exec(
        select(Adresse)
        .where(
            col(Adresse.typ).in_([AdressTyp.LIEFERANT, AdressTyp.BEIDE]),
            col(Adresse.aktiv).is_(True),
        )
        .order_by(col(Adresse.firma))
    ).all()
    artikel_liste = session.exec(
        select(Artikel)
        .where(col(Artikel.seriennummernpflichtig).is_(True), col(Artikel.aktiv).is_(True))
        .order_by(col(Artikel.artikelnr))
    ).all()
    return {
        "kunden": kunden,
        "lieferanten": lieferanten,
        "artikel_liste": artikel_liste,
        "arten": ANLAGE_ARTEN,
    }


def _frei_formular(
    request: Request, session: Session, fehler: str = "", adresse_id: int | None = None
):
    """Formular-Modus (a): freie Anlage/Bestandsanlage ohne Beleg-Bezug.

    ``adresse_id`` wählt den Kunden vor — genutzt vom Knopf „Anlage anlegen" auf
    der Kunden-Detailseite, damit man den Kunden nicht erneut suchen muss.
    """
    return templates.TemplateResponse(
        request,
        "anlagen/neu.html",
        {
            "beleg": None,
            **_auswahllisten(session),
            "sn_optionen": anlagen_service.verkaufte_anlagenfreie_sns(session),
            "fehler": fehler,
            "vorgewaehlte_adresse_id": adresse_id,
        },
    )


def _beleg_formular(request: Request, session: Session, beleg: Beleg, fehler: str = ""):
    """Formular-Modus (b): Kandidaten-Zeilen aus einem gebuchten Lieferschein."""
    kandidaten = anlagen_service.anlagen_kandidaten_aus_beleg(session, beleg)
    adresse = session.get(Adresse, beleg.adresse_id)
    return templates.TemplateResponse(
        request,
        "anlagen/neu.html",
        {"beleg": beleg, "adresse": adresse, "kandidaten": kandidaten, "fehler": fehler},
    )


def _einsaetze(session: Session, anlage_id: int) -> list[tuple[Serviceeinsatz, Benutzer | None]]:
    """Servicehistorie einer Anlage, neueste zuerst, inkl. erfassendem Benutzer."""
    return list(
        session.exec(
            select(Serviceeinsatz, Benutzer)
            .join(Benutzer, col(Benutzer.id) == col(Serviceeinsatz.benutzer_id), isouter=True)
            .where(Serviceeinsatz.anlage_id == anlage_id)
            .order_by(col(Serviceeinsatz.datum).desc(), col(Serviceeinsatz.id).desc())
        ).all()
    )


# ---------------------------------------------------------------- Liste + Suche


@router.get("")
def anlagen_liste(  # noqa: PLR0913
    request: Request,
    q: str = "",
    wartung: str = "",
    art: str = "",
    gruppe: str = "",
    vertrag: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "anlagen/liste.html", _listen_kontext(session, q, wartung, art, gruppe, vertrag)
    )


@router.get("/suche")
def anlagen_suche(  # noqa: PLR0913
    request: Request,
    q: str = "",
    wartung: str = "",
    art: str = "",
    gruppe: str = "",
    vertrag: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen."""
    return templates.TemplateResponse(
        request,
        "partials/anlagen_zeilen.html",
        _listen_kontext(session, q, wartung, art, gruppe, vertrag),
    )


# ---------------------------------------------------------------- Anlegen


@router.get("/neu")
def anlage_neu_form(
    request: Request,
    von_beleg: int | None = None,
    adresse_id: int | None = None,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if von_beleg is not None:
        beleg = _lade_gebuchten_lieferschein(session, von_beleg)
        return _beleg_formular(request, session, beleg)
    return _frei_formular(request, session, adresse_id=adresse_id)


@router.post("/neu")
async def anlage_neu(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    formular = await request.form()
    von_beleg = str(formular.get("von_beleg", "") or "").strip()
    adresse_id = str(formular.get("adresse_id", "") or "").strip()
    artikel_id = str(formular.get("artikel_id", "") or "").strip()
    seriennummer_id = str(formular.get("seriennummer_id", "") or "").strip()
    sn_text = str(formular.get("sn_text", "") or "").strip()
    felder = _stammfelder(formular)

    # Modus (b): Anlage aus gebuchtem Lieferschein — Kunde/Artikel kommen vom Beleg
    if von_beleg:
        beleg = _lade_gebuchten_lieferschein(session, int(von_beleg))
        sn = session.get(Seriennummer, int(seriennummer_id)) if seriennummer_id.isdigit() else None
        if sn is None:
            return _beleg_formular(
                request, session, beleg, fehler="Bitte eine Seriennummer wählen."
            )
        if felder["inbetriebnahme"] is None:
            felder["inbetriebnahme"] = beleg.datum
        try:
            anlagen_service.lege_anlage_an(
                session,
                adresse_id=beleg.adresse_id,
                artikel_id=sn.artikel_id,
                seriennummer_id=sn.id,
                **felder,
            )
            session.commit()
        except WerkplusFehler as exc:
            session.rollback()
            return _beleg_formular(request, session, beleg, fehler=str(exc))
        return RedirectResponse(
            f"/anlagen/neu?von_beleg={beleg.id}", status_code=status.HTTP_302_FOUND
        )

    # Modus (a): freie Anlage — ohne Artikel (Fremdgerät) ODER mit Artikel und
    # vorhandener bzw. neuer Lager-Seriennummer
    if not adresse_id.isdigit():
        return _frei_formular(request, session, fehler="Bitte einen Kunden wählen.")
    try:
        if not artikel_id:
            if not felder["bezeichnung"].strip():
                return _frei_formular(
                    request, session,
                    fehler="Bitte eine Anlagebezeichnung eingeben (oder einen Artikel wählen).",
                )
            anlage = anlagen_service.lege_freie_anlage_an(
                session, adresse_id=int(adresse_id), **felder
            )
        else:
            sn_gewaehlt = bool(seriennummer_id)
            sn_neu = bool(sn_text)
            if sn_gewaehlt == sn_neu:
                return _frei_formular(
                    request,
                    session,
                    fehler="Bitte entweder eine vorhandene Seriennummer wählen "
                    "oder eine neue Seriennummer eingeben (genau eines von beiden).",
                )
            if sn_neu:
                anlage = anlagen_service.lege_bestandsanlage_an(
                    session, adresse_id=int(adresse_id), artikel_id=int(artikel_id),
                    sn_text=sn_text, **felder,
                )
            else:
                anlage = anlagen_service.lege_anlage_an(
                    session, adresse_id=int(adresse_id), artikel_id=int(artikel_id),
                    seriennummer_id=int(seriennummer_id), **felder,
                )
        session.commit()
    except WerkplusFehler as exc:
        session.rollback()
        return _frei_formular(request, session, fehler=str(exc))
    return RedirectResponse(f"/anlagen/{anlage.id}", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------- Detail + Service


def _detail_kontext(request: Request, session: Session, anlage: Anlage) -> dict:
    adresse = session.get(Adresse, anlage.adresse_id)
    kontakte = list(
        session.exec(
            select(Ansprechpartner).where(Ansprechpartner.adresse_id == anlage.adresse_id)
        ).all()
    )
    return {
        "anlage": anlage,
        "adresse": adresse,
        "artikel": session.get(Artikel, anlage.artikel_id) if anlage.artikel_id else None,
        "sn": session.get(Seriennummer, anlage.seriennummer_id) if anlage.seriennummer_id else None,
        "lieferant": session.get(Adresse, anlage.lieferant_id) if anlage.lieferant_id else None,
        "ansprechpartner": session.get(Ansprechpartner, anlage.ansprechpartner_id)
        if anlage.ansprechpartner_id
        else None,
        "kontakte": kontakte,
        "einsaetze": _einsaetze(session, anlage.id),
        "komponenten": anlagen_service.komponenten(session, anlage.id),
        "belege": anlagen_service.belege_der_anlage(session, anlage.id),
        "belegart_labels": BELEGART_LABELS,
        "status_labels": STATUS_LABELS,
        "status_badges": STATUS_BADGES,
        "auftragsarten": list(Auftragsart),
        "auftragsart_labels": AUFTRAGSART_LABELS,
        "darf_verkauf": darf_bereich(request, "verkauf", "bearbeiten"),
        "heute": date.today(),
        **_auswahllisten(session),
    }


@router.get("/{anlage_id}")
def anlage_detail(
    request: Request,
    anlage_id: int,
    tab: str = "",
    fehler: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    anlage = _lade_anlage(session, anlage_id)
    kontext = _detail_kontext(request, session, anlage)
    kontext.update({"aktiver_tab": tab or "daten", "fehler": fehler})
    return templates.TemplateResponse(request, "anlagen/detail.html", kontext)


@router.get("/{anlage_id}/blatt")
def anlagenblatt(
    request: Request,
    anlage_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Kplus „Druck": Anlagenblatt als Druckansicht (Browser-Druck)."""
    anlage = _lade_anlage(session, anlage_id)
    kontext = _detail_kontext(request, session, anlage)
    kontext["firma"] = firma_service.lade_firma(session)
    return templates.TemplateResponse(request, "anlagen/blatt.html", kontext)


@router.post("/{anlage_id}")
async def anlage_bearbeiten(
    request: Request,
    anlage_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    anlage = _lade_anlage(session, anlage_id)
    formular = await request.form()
    for name, wert in _stammfelder(formular).items():
        setattr(anlage, name, wert.strip() if isinstance(wert, str) else wert)
    anlage.aktiv = str(formular.get("aktiv", "")).lower() in ("true", "on", "1")
    if not anlage.bezeichnung and anlage.artikel_id:
        artikel = session.get(Artikel, anlage.artikel_id)
        anlage.bezeichnung = artikel.bezeichnung if artikel else ""
    session.add(anlage)
    session.commit()
    tab = str(formular.get("tab", "") or "")
    return RedirectResponse(
        f"/anlagen/{anlage_id}" + (f"?tab={tab}" if tab else ""), status_code=status.HTTP_302_FOUND
    )


@router.post("/{anlage_id}/serviceeinsatz")
def serviceeinsatz_erfassen(
    request: Request,
    anlage_id: int,
    datum: str = Form(""),
    beschreibung: str = Form(""),
    naechste_wartung: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX: Serviceeinsatz anlegen und die Historie neu rendern."""
    anlage = _lade_anlage(session, anlage_id)
    anlagen_service.erfasse_serviceeinsatz(
        session,
        anlage,
        datum=_parse_datum(datum) or date.today(),
        beschreibung=beschreibung,
        benutzer_id=benutzer.id,
        naechste_wartung=_parse_datum(naechste_wartung),
    )
    session.commit()
    return templates.TemplateResponse(
        request,
        "partials/anl_serviceeinsatz_response.html",
        {"anlage": anlage, "einsaetze": _einsaetze(session, anlage_id)},
    )


# ---------------------------------------------------------------- Komponenten


def _komponenten_partial(request: Request, session: Session, anlage: Anlage):
    return templates.TemplateResponse(
        request,
        "partials/anlage_komponenten.html",
        {"anlage": anlage, "komponenten": anlagen_service.komponenten(session, anlage.id)},
    )


@router.post("/{anlage_id}/komponenten")
async def komponente_anlegen(
    request: Request,
    anlage_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    anlage = _lade_anlage(session, anlage_id)
    formular = await request.form()
    try:
        anlagen_service.fuege_komponente_hinzu(
            session,
            anlage,
            bezeichnung=str(formular.get("bezeichnung", "") or ""),
            hersteller=str(formular.get("hersteller", "") or ""),
            typ=str(formular.get("typ", "") or ""),
            seriennummer_text=str(formular.get("seriennummer_text", "") or ""),
            einbaudatum=_parse_datum(str(formular.get("einbaudatum", "") or "")),
            notiz=str(formular.get("notiz", "") or ""),
            artikel_id=_int_oder_none(formular.get("artikel_id")),
        )
    except WerkplusFehler as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    session.commit()
    return _komponenten_partial(request, session, anlage)


@router.post("/{anlage_id}/komponenten/{komponente_id}/loeschen")
def komponente_loeschen(
    request: Request,
    anlage_id: int,
    komponente_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    anlage = _lade_anlage(session, anlage_id)
    komponente = session.get(AnlageKomponente, komponente_id)
    if komponente is not None and komponente.anlage_id == anlage.id:
        session.delete(komponente)
        session.commit()
    return _komponenten_partial(request, session, anlage)


# ---------------------------------------------------------------- Auftrag aus Anlage


@router.post("/{anlage_id}/auftrag")
def auftrag_aus_anlage(
    request: Request,
    anlage_id: int,
    auftragsart: str = Form("service"),
    belegart: str = Form("auftrag"),
    betreff: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Kplus „Auftrag": Serviceauftrag (oder Angebot) aus dem Anlagenstamm erzeugen."""
    if not darf_bereich(request, "verkauf", "bearbeiten"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Kein Verkaufsrecht")
    anlage = _lade_anlage(session, anlage_id)
    art = Belegart.ANGEBOT if belegart == "angebot" else Belegart.AUFTRAG
    try:
        beleg = anlagen_service.auftrag_aus_anlage(
            session,
            anlage,
            benutzer_id=benutzer.id,
            auftragsart=auftragsart if auftragsart in set(Auftragsart) else "service",
            betreff=betreff,
            belegart=art,
        )
    except WerkplusFehler as exc:
        session.rollback()
        return RedirectResponse(
            f"/anlagen/{anlage_id}?fehler={exc}", status_code=status.HTTP_302_FOUND
        )
    session.commit()
    return RedirectResponse(f"/belege/detail/{beleg.id}", status_code=status.HTTP_302_FOUND)
