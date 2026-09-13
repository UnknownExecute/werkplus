"""Globale Suche: ein Suchbegriff über Adressen, Artikel und Belege."""

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user
from app.models import Adresse, Artikel, Beleg, Benutzer
from app.routers.belege import BELEGART_LABELS, STATUS_BADGES, STATUS_LABELS, _bereich_fuer
from app.services.berechtigung import darf_bereich
from app.templating import templates

router = APIRouter(tags=["suche"])

# Ab dieser Länge wird gesucht — kürzere/leere Begriffe liefern leere Ergebnisse.
MIN_LAENGE = 2
# Maximale Treffer je Gruppe.
MAX_TREFFER = 15


def _suche_adressen(session: Session, muster: str) -> list[Adresse]:
    stmt = (
        select(Adresse)
        .where(
            Adresse.firma.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.adressnr.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.matchcode.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.ansprechpartner.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.ort.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.uid.ilike(muster)  # type: ignore[attr-defined]
        )
        .order_by(Adresse.firma)
        .limit(MAX_TREFFER)
    )
    return list(session.exec(stmt).all())


def _suche_artikel(session: Session, muster: str) -> list[Artikel]:
    stmt = (
        select(Artikel)
        .where(
            Artikel.artikelnr.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.matchcode.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.ean.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.bezeichnung.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.warengruppe.ilike(muster)  # type: ignore[attr-defined]
        )
        .order_by(Artikel.artikelnr)
        .limit(MAX_TREFFER)
    )
    return list(session.exec(stmt).all())


def _suche_belege(session: Session, muster: str) -> list[tuple[Beleg, Adresse]]:
    stmt = (
        select(Beleg, Adresse)
        .join(Adresse, Beleg.adresse_id == Adresse.id)  # type: ignore[arg-type]
        .where(
            Beleg.belegnr.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.firma.ilike(muster)  # type: ignore[attr-defined]
        )
        .order_by(Beleg.id.desc())  # type: ignore[union-attr]
        .limit(MAX_TREFFER)
    )
    return list(session.exec(stmt).all())


@router.get("/suche")
def suche(
    request: Request,
    q: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    begriff = q.strip()
    adressen: list[Adresse] = []
    artikel: list[Artikel] = []
    belege: list[tuple[Beleg, Adresse]] = []
    if len(begriff) >= MIN_LAENGE:
        muster = f"%{begriff}%"
        # Jede Trefferguppe nur, wenn der Benutzer den Bereich sehen darf —
        # die Suche darf keine Abkürzung um die Bereichsrechte sein.
        if darf_bereich(request, "adressen"):
            adressen = _suche_adressen(session, muster)
        if darf_bereich(request, "artikel"):
            artikel = _suche_artikel(session, muster)
        belege = [
            (beleg, adresse)
            for beleg, adresse in _suche_belege(session, muster)
            if darf_bereich(request, _bereich_fuer(beleg.belegart))
        ]
    return templates.TemplateResponse(
        request,
        "suche.html",
        {
            "q": q,
            "adressen": adressen,
            "artikel": artikel,
            "belege": belege,
            "belegart_labels": BELEGART_LABELS,
            "status_labels": STATUS_LABELS,
            "status_badges": STATUS_BADGES,
        },
    )
