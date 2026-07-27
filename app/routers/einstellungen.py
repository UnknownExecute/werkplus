"""Firmenstammdaten pflegen (nur Admin): Formular und Speichern.

Zeigt zusätzlich Programmversion und Codestand — damit nach einem Update in der
Oberfläche prüfbar ist, ob der neue Stand wirklich läuft.
"""

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.dependencies import require_admin
from app.models import Benutzer
from app.services import firma_service
from app.templating import templates

router = APIRouter(tags=["einstellungen"])

logger = logging.getLogger(__name__)


def _db_revision(session: Session) -> str:
    """Aktuelle Alembic-Revision der Datenbank (leer, wenn nicht ermittelbar).

    Nützlich zur Kontrolle, ob die Migrationen beim Containerstart durchliefen:
    Passt die Revision nicht zur erwarteten, hängt das Schema zurück.
    """
    try:
        wert = session.exec(text("SELECT version_num FROM alembic_version")).first()
    except Exception:  # noqa: BLE001 — reine Anzeige, darf die Seite nie kippen
        logger.debug("Alembic-Revision nicht lesbar", exc_info=True)
        return ""
    if wert is None:
        return ""
    # SQLAlchemy liefert ein Row-Objekt — das ist KEIN echtes tuple, ein
    # isinstance(..., tuple) griffe also nicht und str() ergäbe "('abc',)".
    try:
        return str(wert[0])
    except (TypeError, IndexError, KeyError):
        return str(wert)


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
        {
            "firma": firma,
            "gespeichert": gespeichert,
            "umgebung": settings.env,
            "db_revision": _db_revision(session),
        },
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
