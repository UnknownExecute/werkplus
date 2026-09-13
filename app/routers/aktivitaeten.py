"""CRM-Aktivitäten zu einer Adresse (HTMX) und die Wiedervorlage-Liste (Kplus).

Aktivitäten haben Art (Notiz/Telefonat/E-Mail/Termin/Aufgabe), optional ein
Wiedervorlage-Datum und einen Erledigt-Status. Die Seite ``/wiedervorlage``
zeigt alle offenen Wiedervorlagen — fällige zuerst — über alle Adressen.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.models import (
    AKTIVITAET_LABELS,
    Adresse,
    Aktivitaet,
    AktivitaetTyp,
    Beleg,
    Benutzer,
)
from app.services import adresse_service
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.templating import templates

router = APIRouter(tags=["aktivitaeten"], dependencies=[Depends(bereich_wache("adressen"))])

VORSCHAU_TAGE = 7  # "demnächst" = Wiedervorlage innerhalb der nächsten 7 Tage


def _parse_datum(text: str | None) -> date | None:
    if not text or not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _liste_response(request: Request, session: Session, adresse_id: int):
    # Router-Import hier, um den Zirkel adressen ↔ aktivitaeten zu vermeiden
    from app.routers.adressen import _aktivitaeten_kontext

    return templates.TemplateResponse(
        request, "partials/aktivitaeten_liste.html", _aktivitaeten_kontext(session, adresse_id)
    )


def _lade_aktivitaet(session: Session, adresse_id: int, aktivitaet_id: int) -> Aktivitaet:
    aktivitaet = session.get(Aktivitaet, aktivitaet_id)
    if aktivitaet is None or aktivitaet.adresse_id != adresse_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aktivität nicht gefunden"
        )
    return aktivitaet


@router.post("/adressen/{adresse_id}/aktivitaeten")
async def aktivitaet_anlegen(
    request: Request,
    adresse_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Legt eine Aktivität an; Antwort = Partial der aktualisierten Aktivitätenliste."""
    if session.get(Adresse, adresse_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adresse nicht gefunden")
    formular = await request.form()
    typ = str(formular.get("typ", AktivitaetTyp.NOTIZ))
    beleg_id = None
    beleg_text = str(formular.get("beleg_id", "") or "").strip()
    if beleg_text.isdigit():
        beleg = session.get(Beleg, int(beleg_text))
        if beleg is not None and beleg.adresse_id == adresse_id:
            beleg_id = beleg.id
    aktivitaet = Aktivitaet(
        adresse_id=adresse_id,
        typ=AktivitaetTyp(typ) if typ in set(AktivitaetTyp) else AktivitaetTyp.NOTIZ,
        betreff=str(formular.get("betreff", "") or "").strip(),
        text=str(formular.get("text", "") or "").strip(),
        benutzer_id=benutzer.id,
        wiedervorlage_am=_parse_datum(str(formular.get("wiedervorlage_am", "") or "")),
        beleg_id=beleg_id,
    )
    session.add(aktivitaet)
    session.commit()
    return _liste_response(request, session, adresse_id)


@router.post("/adressen/{adresse_id}/aktivitaeten/{aktivitaet_id}/erledigt")
def aktivitaet_erledigt(
    request: Request,
    adresse_id: int,
    aktivitaet_id: int,
    zurueck: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Erledigt-Status umschalten (offen ↔ erledigt)."""
    aktivitaet = _lade_aktivitaet(session, adresse_id, aktivitaet_id)
    adresse_service.setze_erledigt(session, aktivitaet, not aktivitaet.erledigt)
    session.commit()
    if zurueck == "wiedervorlage":
        return RedirectResponse("/wiedervorlage", status_code=status.HTTP_302_FOUND)
    return _liste_response(request, session, adresse_id)


@router.post("/adressen/{adresse_id}/aktivitaeten/{aktivitaet_id}/loeschen")
def aktivitaet_loeschen(
    request: Request,
    adresse_id: int,
    aktivitaet_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    aktivitaet = _lade_aktivitaet(session, adresse_id, aktivitaet_id)
    session.delete(aktivitaet)
    session.commit()
    return _liste_response(request, session, adresse_id)


# ---------------------------------------------------------------- Wiedervorlage


@router.get("/wiedervorlage")
def wiedervorlage(
    request: Request,
    filter: str = "faellig",
    nur_eigene: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Kplus „Wiedervorlage": offene Aktivitäten mit Datum, fällige zuerst."""
    heute = date.today()
    if filter == "alle":
        bis = None
    elif filter == "woche":
        bis = heute + timedelta(days=VORSCHAU_TAGE)
    else:
        filter, bis = "faellig", heute
    zeilen = adresse_service.offene_wiedervorlagen(
        session, bis=bis, benutzer_id=benutzer.id if nur_eigene else None
    )
    beleg_ids = {a.beleg_id for a, _ in zeilen if a.beleg_id}
    belegnrn: dict[int, str] = {}
    if beleg_ids:
        stmt = select(Beleg).where(col(Beleg.id).in_(beleg_ids))
        belegnrn = {b.id: b.belegnr for b in session.exec(stmt).all()}
    return templates.TemplateResponse(
        request,
        "wiedervorlage.html",
        {
            "zeilen": zeilen,
            "filter": filter,
            "nur_eigene": bool(nur_eigene),
            "heute": heute,
            "akt_belegnr": belegnrn,
            "aktivitaet_labels": AKTIVITAET_LABELS,
            "faellig_anzahl": sum(1 for a, _ in zeilen if a.ist_faellig(heute)),
        },
    )
