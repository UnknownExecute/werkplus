"""Mini-CRM: Aktivitäten (Notiz/Telefonat/E-Mail) zu einer Adresse per HTMX."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user
from app.models import Adresse, Aktivitaet, AktivitaetTyp, Benutzer
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.templating import templates

router = APIRouter(tags=["aktivitaeten"], dependencies=[Depends(bereich_wache("adressen"))])


@router.post("/adressen/{adresse_id}/aktivitaeten")
def aktivitaet_anlegen(
    request: Request,
    adresse_id: int,
    typ: str = Form(AktivitaetTyp.NOTIZ),
    betreff: str = Form(""),
    text: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Legt eine Aktivität an; Antwort = Partial der aktualisierten Aktivitätenliste."""
    if session.get(Adresse, adresse_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adresse nicht gefunden")
    aktivitaet = Aktivitaet(
        adresse_id=adresse_id,
        typ=AktivitaetTyp(typ),
        betreff=betreff.strip(),
        text=text.strip(),
        benutzer_id=benutzer.id,
    )
    session.add(aktivitaet)
    session.commit()

    aktivitaeten = list(
        session.exec(
            select(Aktivitaet)
            .where(Aktivitaet.adresse_id == adresse_id)
            .order_by(Aktivitaet.zeitstempel.desc())  # type: ignore[union-attr]
        ).all()
    )
    return templates.TemplateResponse(
        request, "partials/aktivitaeten_liste.html", {"aktivitaeten": aktivitaeten}
    )
