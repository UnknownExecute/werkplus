"""Textbaustein-Stammdaten: Liste mit Livesuche und Kategorie-Filter, Anlegen, Bearbeiten."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.database import get_session
from app.dependencies import get_current_user
from app.models import Benutzer, Textbaustein, TextbausteinKategorie
from app.services import textbaustein_service
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.templating import templates

router = APIRouter(
    prefix="/textbausteine", tags=["textbausteine"],
    dependencies=[Depends(bereich_wache("artikel"))],
)

KATEGORIE_LABELS = {
    TextbausteinKategorie.POSITION: "Position",
    TextbausteinKategorie.KOPF: "Kopftext",
    TextbausteinKategorie.FUSS: "Fußtext",
}


@router.get("")
def textbausteine_liste(
    request: Request,
    q: str = "",
    kategorie: str = "alle",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    bausteine = textbaustein_service.suche_bausteine(session, q, kategorie)
    return templates.TemplateResponse(
        request,
        "textbausteine/liste.html",
        {
            "bausteine": bausteine,
            "q": q,
            "kategorie": kategorie,
            "kategorie_labels": KATEGORIE_LABELS,
        },
    )


@router.get("/suche")
def textbausteine_suche(
    request: Request,
    q: str = "",
    kategorie: str = "alle",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen."""
    bausteine = textbaustein_service.suche_bausteine(session, q, kategorie)
    return templates.TemplateResponse(
        request,
        "partials/textbaustein_zeilen.html",
        {"bausteine": bausteine, "kategorie_labels": KATEGORIE_LABELS},
    )


@router.get("/neu")
def textbaustein_neu_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request,
        "textbausteine/form.html",
        {
            "baustein": Textbaustein(),
            "neu": True,
            "kategorie_labels": KATEGORIE_LABELS,
        },
    )


@router.post("/neu")
def textbaustein_neu(
    request: Request,
    kuerzel: str = Form(""),
    titel: str = Form(...),
    text: str = Form(""),
    kategorie: str = Form(TextbausteinKategorie.POSITION.value),
    aktiv: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    baustein = textbaustein_service.speichere(
        session, None, kuerzel, titel, text, kategorie, aktiv
    )
    return RedirectResponse(
        f"/textbausteine/{baustein.id}", status_code=status.HTTP_302_FOUND
    )


def _lade(session: Session, baustein_id: int) -> Textbaustein:
    baustein = textbaustein_service.lade(session, baustein_id)
    if baustein is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Textbaustein nicht gefunden"
        )
    return baustein


@router.get("/{baustein_id}")
def textbaustein_bearbeiten_form(
    request: Request,
    baustein_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    baustein = _lade(session, baustein_id)
    return templates.TemplateResponse(
        request,
        "textbausteine/form.html",
        {
            "baustein": baustein,
            "neu": False,
            "kategorie_labels": KATEGORIE_LABELS,
        },
    )


@router.post("/{baustein_id}")
def textbaustein_bearbeiten(
    request: Request,
    baustein_id: int,
    kuerzel: str = Form(""),
    titel: str = Form(...),
    text: str = Form(""),
    kategorie: str = Form(TextbausteinKategorie.POSITION.value),
    aktiv: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _lade(session, baustein_id)
    textbaustein_service.speichere(
        session, baustein_id, kuerzel, titel, text, kategorie, aktiv
    )
    return RedirectResponse(
        f"/textbausteine/{baustein_id}", status_code=status.HTTP_302_FOUND
    )
