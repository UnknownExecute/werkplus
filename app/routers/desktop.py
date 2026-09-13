"""Kplus-Desktop: Kalender (Wiedervorlagen, Belegtermine, Wartungen) und Favoriten."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.database import get_session
from app.dependencies import get_current_user
from app.models import Benutzer
from app.services import favoriten_service, kalender_service
from app.services.berechtigung import darf_bereich
from app.services.fehler import WerkplusFehler
from app.templating import templates

router = APIRouter(tags=["desktop"])

MONATE = (
    "Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
    "September", "Oktober", "November", "Dezember",
)


def _monat_aus_text(text: str, heute: date) -> tuple[int, int]:
    try:
        jahr_text, monat_text = text.split("-", 1)
        jahr, monat = int(jahr_text), int(monat_text)
        if 1 <= monat <= 12 and 2000 <= jahr <= 2100:
            return jahr, monat
    except (ValueError, AttributeError):
        pass
    return heute.year, heute.month


def _sichtbar(request: Request, eintrag: kalender_service.Eintrag) -> bool:
    """Kalendereinträge respektieren die Bereichsrechte."""
    bereich = {"wiedervorlage": "adressen", "termin": "verkauf", "wartung": "anlagen"}[eintrag.art]
    return darf_bereich(request, bereich)


def kalender_kontext(request: Request, session: Session, jahr: int, monat: int) -> dict:
    """Monatsraster samt Einträgen — für die Kalenderseite und die Dashboard-Miniatur."""
    raster = kalender_service.monatsraster(jahr, monat)
    von, bis = raster[0][0], raster[-1][-1]
    eintraege = [e for e in kalender_service.eintraege(session, von, bis) if _sichtbar(request, e)]
    zurueck = kalender_service.monat_verschieben(jahr, monat, -1)
    weiter = kalender_service.monat_verschieben(jahr, monat, 1)
    return {
        "jahr": jahr,
        "monat": monat,
        "monatsname": MONATE[monat - 1],
        "raster": raster,
        "je_tag": kalender_service.je_tag(eintraege),
        "eintraege": eintraege,
        "heute": date.today(),
        "monat_zurueck": f"{zurueck[0]}-{zurueck[1]:02d}",
        "monat_weiter": f"{weiter[0]}-{weiter[1]:02d}",
        "arten": kalender_service.ARTEN,
    }


@router.get("/kalender")
def kalender(
    request: Request,
    monat: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    heute = date.today()
    jahr, m = _monat_aus_text(monat, heute)
    return templates.TemplateResponse(
        request, "kalender.html", kalender_kontext(request, session, jahr, m)
    )


# ---------------------------------------------------------------- Favoriten


@router.post("/favoriten")
def favorit_anlegen(
    request: Request,
    titel: str = Form(""),
    url: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Aktuelle Seite als Favorit merken (Stern in der Kopfzeile)."""
    try:
        favoriten_service.hinzufuegen(session, benutzer.id, titel, url)
    except WerkplusFehler:
        session.rollback()
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    session.commit()
    ziel = url if url.startswith("/") and not url.startswith("//") else "/"
    return RedirectResponse(ziel, status_code=status.HTTP_302_FOUND)


@router.post("/favoriten/{favorit_id}/loeschen")
def favorit_loeschen(
    request: Request,
    favorit_id: int,
    zurueck: str = Form("/"),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        favoriten_service.entfernen(session, benutzer.id, favorit_id)
    except WerkplusFehler:
        session.rollback()
    else:
        session.commit()
    ziel = zurueck if zurueck.startswith("/") and not zurueck.startswith("//") else "/"
    return RedirectResponse(ziel, status_code=status.HTTP_302_FOUND)
