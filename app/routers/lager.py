"""Lager-UI: Bewegungsjournal (neueste zuerst) mit HTMX-Livesuche.

Dünne Routen — die Lagerlogik selbst liegt in app.services.lager_service;
hier wird nur das Journal gelesen und gerendert.
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.formparse import parse_decimal
from app.models import Artikel, Beleg, Benutzer, Buchungsart, Lager, Lagerbewegung
from app.services import lager_service
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.services.fehler import LagerbuchungFehlgeschlagen
from app.templating import templates

router = APIRouter(
    prefix="/lager", tags=["lager"], dependencies=[Depends(bereich_wache("lager"))]
)

BUCHUNGSART_LABELS: dict[Buchungsart, str] = {
    Buchungsart.WARENEINGANG: "Wareneingang",
    Buchungsart.LIEFERSCHEIN: "Lieferschein",
    Buchungsart.STORNO: "Storno",
    Buchungsart.KORREKTUR: "Korrektur",
}

JOURNAL_LIMIT = 200


def _suche_bewegungen(session: Session, q: str) -> list:
    """Neueste Bewegungen samt Artikel/Lager/Beleg/Benutzer; Textfilter auf Artikel."""
    stmt = (
        select(Lagerbewegung, Artikel, Lager, Beleg, Benutzer)
        .join(Artikel, Lagerbewegung.artikel_id == Artikel.id)  # type: ignore[arg-type]
        .join(Lager, Lagerbewegung.lager_id == Lager.id)  # type: ignore[arg-type]
        .join(Beleg, Lagerbewegung.beleg_id == Beleg.id, isouter=True)  # type: ignore[arg-type]
        .join(
            Benutzer,
            Lagerbewegung.benutzer_id == Benutzer.id,  # type: ignore[arg-type]
            isouter=True,
        )
    )
    if q.strip():
        muster = f"%{q.strip()}%"
        stmt = stmt.where(
            Artikel.artikelnr.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.bezeichnung.ilike(muster)  # type: ignore[attr-defined]
        )
    stmt = stmt.order_by(Lagerbewegung.id.desc()).limit(JOURNAL_LIMIT)  # type: ignore[union-attr]
    return list(session.exec(stmt).all())


@router.get("/bewegungen")
def bewegungen_liste(
    request: Request,
    q: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "lager/bewegungen.html",
        {
            "zeilen": _suche_bewegungen(session, q),
            "buchungsart_labels": BUCHUNGSART_LABELS,
            "q": q,
        },
    )


@router.get("/bewegungen/suche")
def bewegungen_suche(
    request: Request,
    q: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen des Journals."""
    return templates.TemplateResponse(
        request,
        "partials/bewegung_zeilen.html",
        {"zeilen": _suche_bewegungen(session, q), "buchungsart_labels": BUCHUNGSART_LABELS},
    )


# ---------------------------------------------------------------- Bestandskorrektur


def _korrektur_kontext(session: Session, fehler: str = "") -> dict:
    """Auswahllisten fürs Korrekturformular; Standardlager wird vorbelegt."""
    artikel_liste = session.exec(
        select(Artikel)
        .where(col(Artikel.ist_lagerartikel).is_(True), col(Artikel.aktiv).is_(True))
        .order_by(col(Artikel.artikelnr))
    ).all()
    lager_liste = session.exec(select(Lager).order_by(col(Lager.name))).all()
    standard = session.exec(select(Lager).where(col(Lager.ist_standard).is_(True))).first()
    return {
        "artikel_liste": artikel_liste,
        "lager_liste": lager_liste,
        "standard_lager_id": standard.id if standard else None,
        "fehler": fehler,
    }


@router.get("/korrektur")
def korrektur_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "lager/korrektur.html", _korrektur_kontext(session)
    )


@router.post("/korrektur")
def korrektur_buchen(  # noqa: PLR0913
    request: Request,
    artikel_id: str = Form(""),
    lager_id: str = Form(""),
    korrekturart: str = Form("differenz"),
    menge: str = Form("0"),
    grund: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Manuelle Bestandskorrektur als KORREKTUR-Buchung ins Journal.

    "absolut": Zielbestand angeben → es wird die Differenz zum Ist gebucht.
    "differenz": vorzeichenbehaftete Menge (+/-) direkt buchen.
    """
    if not artikel_id.strip() or not lager_id.strip():
        return templates.TemplateResponse(
            request,
            "lager/korrektur.html",
            _korrektur_kontext(session, fehler="Bitte Artikel und Lager wählen."),
        )
    aid = int(artikel_id)
    lid = int(lager_id)
    eingabe = parse_decimal(menge)
    if korrekturart == "absolut":
        ist = lager_service.bestand(session, aid, lid)
        buchungsmenge = eingabe - ist
    else:
        buchungsmenge = eingabe
    if buchungsmenge != 0:
        try:
            lager_service.buche(
                session,
                artikel_id=aid,
                lager_id=lid,
                menge=buchungsmenge,
                buchungsart=Buchungsart.KORREKTUR,
                benutzer_id=benutzer.id,
                notiz=grund.strip(),
            )
        except LagerbuchungFehlgeschlagen as exc:
            # Nebenläufigkeitskonflikt: nichts halb gebucht stehen lassen und das
            # Formular mit sprechendem Hinweis erneut zeigen (kein 500).
            session.rollback()
            return templates.TemplateResponse(
                request, "lager/korrektur.html", _korrektur_kontext(session, fehler=str(exc))
            )
        session.commit()
    return RedirectResponse("/lager/bewegungen", status_code=status.HTTP_302_FOUND)
