"""Firmenstammdaten pflegen (nur Admin): Formular und Speichern.

Zeigt zusätzlich Programmversion und Codestand — damit nach einem Update in der
Oberfläche prüfbar ist, ob der neue Stand wirklich läuft.
"""

import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.dependencies import require_admin
from app.models import Belegart, Benutzer
from app.routers.belege import BELEGART_LABELS
from app.services import adresse_service, firma_service, mail_service, nummernkreis_service
from app.services.fehler import WerkplusFehler
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


def _nummernkreis_kontext(session: Session, jahr: int) -> dict:
    """Stände je Belegart für die Nummernkreis-Tabelle (Kplus-Anschluss)."""
    staende = nummernkreis_service.staende(session, jahr)
    return {
        "nk_jahr": jahr,
        "adressnr_stand": adresse_service.adressnr_stand(session),
        "nk_zeilen": [
            (
                art,
                BELEGART_LABELS[art],
                staende[art],
                nummernkreis_service.formatiere_belegnr(jahr, staende[art] + 1),
            )
            for art in Belegart
        ],
    }


@router.get("/einstellungen")
def einstellungen_form(  # noqa: PLR0913
    request: Request,
    gespeichert: bool = False,
    nk_gespeichert: bool = False,
    nk_fehler: str = "",
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
            "nk_gespeichert": nk_gespeichert,
            "nk_fehler": nk_fehler,
            "umgebung": settings.env,
            "db_revision": _db_revision(session),
            "mail_konfiguriert": mail_service.ist_konfiguriert(),
            "smtp_host": settings.smtp_host,
            "mail_absender": (
                mail_service.absender(session) if mail_service.ist_konfiguriert() else ""
            ),
            **_nummernkreis_kontext(session, date.today().year),
        },
    )


@router.post("/einstellungen/nummernkreise")
async def nummernkreise_speichern(
    request: Request,
    benutzer: Benutzer = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Nummernstände je Belegart setzen (nur nach oben — nahtloser Anschluss an Kplus).

    Formularfelder ``stand_<belegart>`` = letzte vergebene Nummer; die nächste
    Belegnummer ist dann Stand + 1. Leere Felder bleiben unverändert.
    """
    formular = await request.form()
    jahr = date.today().year
    try:
        adress_wert = str(formular.get("stand_adresse", "")).strip()
        if adress_wert:
            try:
                adresse_service.setze_adressnr_stand(session, int(adress_wert))
            except ValueError:
                raise WerkplusFehler(f"Adressen: '{adress_wert}' ist keine ganze Zahl") from None
        for art in Belegart:
            wert = str(formular.get(f"stand_{art}", "")).strip()
            if not wert:
                continue
            try:
                stand = int(wert)
            except ValueError:
                raise WerkplusFehler(
                    f"{BELEGART_LABELS[art]}: '{wert}' ist keine ganze Zahl"
                ) from None
            nummernkreis_service.setze_stand(session, art, jahr, stand)
    except WerkplusFehler as exc:
        session.rollback()
        return RedirectResponse(
            f"/einstellungen?nk_fehler={quote(str(exc))}", status_code=status.HTTP_303_SEE_OTHER
        )
    session.commit()
    return RedirectResponse(
        "/einstellungen?nk_gespeichert=1", status_code=status.HTTP_303_SEE_OTHER
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
