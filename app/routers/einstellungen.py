"""Firmenstammdaten pflegen (nur Admin): Formular und Speichern."""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.database import get_session
from app.dependencies import require_admin
from app.models import Benutzer
from app.services import firma_service
from app.templating import templates

router = APIRouter(tags=["einstellungen"])


@router.get("/einstellungen")
def einstellungen_form(
    request: Request,
    gespeichert: bool = False,
    benutzer: Benutzer = Depends(require_admin),
    session: Session = Depends(get_session),
):
    firma = firma_service.lade_firma(session)
    return templates.TemplateResponse(
        request,
        "einstellungen.html",
        {"firma": firma, "gespeichert": gespeichert},
    )


@router.post("/einstellungen")
def einstellungen_speichern(
    request: Request,
    name: str = Form(""),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    land: str = Form(""),
    uid: str = Form(""),
    telefon: str = Form(""),
    email: str = Form(""),
    iban: str = Form(""),
    benutzer: Benutzer = Depends(require_admin),
    session: Session = Depends(get_session),
):
    firma_service.aktualisiere_firma(
        session,
        name=name.strip(),
        strasse=strasse.strip(),
        plz=plz.strip(),
        ort=ort.strip(),
        land=land.strip(),
        uid=uid.strip(),
        telefon=telefon.strip(),
        email=email.strip(),
        iban=iban.strip(),
    )
    return RedirectResponse(
        "/einstellungen?gespeichert=1", status_code=status.HTTP_303_SEE_OTHER
    )
