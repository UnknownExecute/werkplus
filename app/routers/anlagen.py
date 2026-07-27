"""Anlagen-UI: globale Liste mit Wartungsfilter, Anlage anlegen (frei oder aus
Lieferschein), Detailseite mit Servicehistorie.

Die Routen sind bewusst dünn — die Fachlogik liegt in app.services.anlagen_service.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.models import (
    Adresse,
    AdressTyp,
    Anlage,
    Artikel,
    Beleg,
    Belegart,
    BelegStatus,
    Benutzer,
    Seriennummer,
    Serviceeinsatz,
)
from app.services import anlagen_service
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.services.fehler import WerkplusFehler
from app.templating import templates

router = APIRouter(
    prefix="/anlagen", tags=["anlagen"], dependencies=[Depends(bereich_wache("anlagen"))]
)


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


def _listen_kontext(session: Session, q: str, wartung: str) -> dict:
    heute = date.today()
    zeilen = anlagen_service.suche_anlagen(session, q=q, nur_faellig=wartung == "faellig")
    return {
        "zeilen": zeilen,
        "heute": heute,
        "grenze": heute + timedelta(days=anlagen_service.WARTUNGS_VORLAUF_TAGE),
        "q": q,
        "wartung": wartung,
    }


def _frei_formular(
    request: Request, session: Session, fehler: str = "", adresse_id: int | None = None
):
    """Formular-Modus (a): freie Anlage/Bestandsanlage ohne Beleg-Bezug.

    ``adresse_id`` wählt den Kunden vor — genutzt vom Knopf „Anlage anlegen" auf
    der Kunden-Detailseite, damit man den Kunden nicht erneut suchen muss.
    """
    kunden = session.exec(
        select(Adresse)
        .where(
            col(Adresse.typ).in_([AdressTyp.KUNDE, AdressTyp.BEIDE]),
            col(Adresse.aktiv).is_(True),
        )
        .order_by(col(Adresse.firma))
    ).all()
    artikel_liste = session.exec(
        select(Artikel)
        .where(col(Artikel.seriennummernpflichtig).is_(True), col(Artikel.aktiv).is_(True))
        .order_by(col(Artikel.artikelnr))
    ).all()
    sn_optionen = anlagen_service.verkaufte_anlagenfreie_sns(session)
    return templates.TemplateResponse(
        request,
        "anlagen/neu.html",
        {
            "beleg": None,
            "kunden": kunden,
            "artikel_liste": artikel_liste,
            "sn_optionen": sn_optionen,
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
def anlagen_liste(
    request: Request,
    q: str = "",
    wartung: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "anlagen/liste.html", _listen_kontext(session, q, wartung)
    )


@router.get("/suche")
def anlagen_suche(
    request: Request,
    q: str = "",
    wartung: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen."""
    return templates.TemplateResponse(
        request, "partials/anlagen_zeilen.html", _listen_kontext(session, q, wartung)
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
def anlage_neu(  # noqa: PLR0913
    request: Request,
    von_beleg: str = Form(""),
    adresse_id: str = Form(""),
    artikel_id: str = Form(""),
    seriennummer_id: str = Form(""),
    sn_text: str = Form(""),
    standort: str = Form(""),
    inbetriebnahme: str = Form(""),
    garantieende: str = Form(""),
    naechste_wartung: str = Form(""),
    notizen: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    felder = {
        "standort": standort,
        "inbetriebnahme": _parse_datum(inbetriebnahme),
        "garantieende": _parse_datum(garantieende),
        "naechste_wartung": _parse_datum(naechste_wartung),
        "notizen": notizen,
    }

    # Modus (b): Anlage aus gebuchtem Lieferschein — Kunde/Artikel kommen vom Beleg
    if von_beleg.strip():
        beleg = _lade_gebuchten_lieferschein(session, int(von_beleg))
        sn = session.get(Seriennummer, int(seriennummer_id)) if seriennummer_id.strip() else None
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

    # Modus (a): freie Anlage — vorhandene SN wählen ODER neue SN eingeben
    if not adresse_id.strip() or not artikel_id.strip():
        return _frei_formular(request, session, fehler="Bitte Kunde und Artikel wählen.")
    sn_gewaehlt = bool(seriennummer_id.strip())
    sn_neu = bool(sn_text.strip())
    if sn_gewaehlt == sn_neu:
        return _frei_formular(
            request,
            session,
            fehler="Bitte entweder eine vorhandene Seriennummer wählen "
            "oder eine neue Seriennummer eingeben (genau eines von beiden).",
        )
    try:
        if sn_neu:
            anlage = anlagen_service.lege_bestandsanlage_an(
                session,
                adresse_id=int(adresse_id),
                artikel_id=int(artikel_id),
                sn_text=sn_text,
                **felder,
            )
        else:
            anlage = anlagen_service.lege_anlage_an(
                session,
                adresse_id=int(adresse_id),
                artikel_id=int(artikel_id),
                seriennummer_id=int(seriennummer_id),
                **felder,
            )
        session.commit()
    except WerkplusFehler as exc:
        session.rollback()
        return _frei_formular(request, session, fehler=str(exc))
    return RedirectResponse(f"/anlagen/{anlage.id}", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------- Detail + Service


@router.get("/{anlage_id}")
def anlage_detail(
    request: Request,
    anlage_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    anlage = _lade_anlage(session, anlage_id)
    return templates.TemplateResponse(
        request,
        "anlagen/detail.html",
        {
            "anlage": anlage,
            "adresse": session.get(Adresse, anlage.adresse_id),
            "artikel": session.get(Artikel, anlage.artikel_id),
            "sn": session.get(Seriennummer, anlage.seriennummer_id),
            "einsaetze": _einsaetze(session, anlage_id),
            "heute": date.today(),
        },
    )


@router.post("/{anlage_id}")
def anlage_bearbeiten(  # noqa: PLR0913
    request: Request,
    anlage_id: int,
    standort: str = Form(""),
    inbetriebnahme: str = Form(""),
    garantieende: str = Form(""),
    naechste_wartung: str = Form(""),
    notizen: str = Form(""),
    aktiv: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    anlage = _lade_anlage(session, anlage_id)
    anlage.standort = standort.strip()
    anlage.inbetriebnahme = _parse_datum(inbetriebnahme)
    anlage.garantieende = _parse_datum(garantieende)
    anlage.naechste_wartung = _parse_datum(naechste_wartung)
    anlage.notizen = notizen.strip()
    anlage.aktiv = aktiv
    session.add(anlage)
    session.commit()
    return RedirectResponse(f"/anlagen/{anlage_id}", status_code=status.HTTP_302_FOUND)


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
