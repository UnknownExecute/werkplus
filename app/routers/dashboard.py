"""Dashboard: offene Angebote/Aufträge, fällige Wartungen, letzte Belege.

Die Wartungs-Fälligkeit wird hier bewusst direkt auf dem Anlage-Model berechnet
(gleiche Definition wie /anlagen?wartung=faellig: nächste Wartung überfällig
oder innerhalb der nächsten 30 Tage).
"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.models import (
    Adresse,
    Anlage,
    Artikel,
    Beleg,
    Belegart,
    BelegStatus,
    Benutzer,
)
from app.routers.belege import BELEGART_LABELS, STATUS_BADGES, STATUS_LABELS
from app.templating import templates

router = APIRouter(tags=["dashboard"])

# "Offen" = noch nicht erledigt/storniert
OFFENE_STATUS = (BelegStatus.ENTWURF, BelegStatus.GEDRUCKT)
WARTUNG_VORLAUF_TAGE = 30
MAX_ZEILEN = 10


def _offene_belege(
    session: Session, art: Belegart
) -> tuple[list[tuple[Beleg, Adresse]], int]:
    """Neueste offene Belege einer Art samt Adresse (max. 10) plus Gesamtanzahl."""
    zeilen = list(
        session.exec(
            select(Beleg, Adresse)
            .join(Adresse, col(Beleg.adresse_id) == col(Adresse.id))
            .where(Beleg.belegart == art, col(Beleg.status).in_(OFFENE_STATUS))
            .order_by(col(Beleg.id).desc())
        ).all()
    )
    return zeilen[:MAX_ZEILEN], len(zeilen)


def _offene_posten(
    session: Session,
) -> tuple[list[tuple[Beleg, Adresse, bool, date]], Decimal, int]:
    """Unbezahlte, gebuchte/erledigte Rechnungen samt Fälligkeit, offenem Brutto, Anzahl."""
    heute = date.today()
    zeilen = list(
        session.exec(
            select(Beleg, Adresse)
            .join(Adresse, col(Beleg.adresse_id) == col(Adresse.id))
            .where(
                Beleg.belegart == Belegart.RECHNUNG,
                col(Beleg.status).in_((BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT)),
                col(Beleg.bezahlt_am).is_(None),
            )
            .order_by(col(Beleg.id).desc())
        ).all()
    )
    offen_gesamt = sum((beleg.summe_brutto for beleg, _ in zeilen), Decimal("0.00"))
    posten = []
    for beleg, adresse in zeilen[:MAX_ZEILEN]:
        faelligkeit = beleg.datum + timedelta(days=beleg.zahlungsziel_tage)
        posten.append((beleg, adresse, faelligkeit < heute, faelligkeit))
    return posten, offen_gesamt, len(zeilen)


def _faellige_wartungen(session: Session) -> list[tuple[Anlage, Adresse, Artikel]]:
    """Aktive Anlagen, deren nächste Wartung überfällig oder binnen 30 Tagen fällig ist."""
    stichtag = date.today() + timedelta(days=WARTUNG_VORLAUF_TAGE)
    return list(
        session.exec(
            select(Anlage, Adresse, Artikel)
            .join(Adresse, col(Anlage.adresse_id) == col(Adresse.id))
            .join(Artikel, col(Anlage.artikel_id) == col(Artikel.id))
            .where(
                col(Anlage.aktiv),
                col(Anlage.naechste_wartung).is_not(None),
                col(Anlage.naechste_wartung) <= stichtag,
            )
            .order_by(col(Anlage.naechste_wartung))
        ).all()
    )


def _letzte_belege(session: Session) -> list[tuple[Beleg, Adresse]]:
    """Die zuletzt erstellten Belege über alle Belegarten."""
    return list(
        session.exec(
            select(Beleg, Adresse)
            .join(Adresse, col(Beleg.adresse_id) == col(Adresse.id))
            .order_by(col(Beleg.id).desc())
            .limit(MAX_ZEILEN)
        ).all()
    )


@router.get("/")
def dashboard(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    angebote, angebote_anzahl = _offene_belege(session, Belegart.ANGEBOT)
    auftraege, auftraege_anzahl = _offene_belege(session, Belegart.AUFTRAG)
    offene_posten, offen_gesamt, offene_posten_anzahl = _offene_posten(session)
    wartungen = _faellige_wartungen(session)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "benutzer": benutzer,
            "angebote": angebote,
            "angebote_anzahl": angebote_anzahl,
            "auftraege": auftraege,
            "auftraege_anzahl": auftraege_anzahl,
            "offene_posten": offene_posten,
            "offen_gesamt": offen_gesamt,
            "offene_posten_anzahl": offene_posten_anzahl,
            "wartungen": wartungen,
            "letzte_belege": _letzte_belege(session),
            "heute": date.today(),
            "belegart_labels": BELEGART_LABELS,
            "status_labels": STATUS_LABELS,
            "status_badges": STATUS_BADGES,
        },
    )
