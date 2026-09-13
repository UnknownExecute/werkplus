"""Persönliche Favoriten (Kplus-Desktop): Seiten als Schnellzugriff merken."""

from sqlmodel import Session, col, select

from app.models.favorit import Favorit
from app.services.fehler import WerkplusFehler

MAX_FAVORITEN = 12


def fuer_benutzer(session: Session, benutzer_id: int) -> list[Favorit]:
    return list(
        session.exec(
            select(Favorit)
            .where(Favorit.benutzer_id == benutzer_id)
            .order_by(col(Favorit.sort), col(Favorit.id))
        ).all()
    )


def _sicherer_pfad(url: str) -> str:
    """Nur interne Pfade (kein externes Ziel, kein Protokoll)."""
    url = (url or "").strip()
    if not url.startswith("/") or url.startswith("//"):
        raise WerkplusFehler("Favoriten können nur auf Seiten von WerkPlus zeigen")
    return url[:500]


def hinzufuegen(session: Session, benutzer_id: int, titel: str, url: str) -> Favorit:
    """Legt einen Favoriten an; gleiche URL wird nicht doppelt gespeichert."""
    url = _sicherer_pfad(url)
    vorhandene = fuer_benutzer(session, benutzer_id)
    for f in vorhandene:
        if f.url == url:
            return f
    if len(vorhandene) >= MAX_FAVORITEN:
        raise WerkplusFehler(f"Höchstens {MAX_FAVORITEN} Favoriten — bitte zuerst einen entfernen")
    favorit = Favorit(
        benutzer_id=benutzer_id,
        titel=(titel or "").strip()[:80] or url,
        url=url,
        sort=max((f.sort for f in vorhandene), default=-1) + 1,
    )
    session.add(favorit)
    session.flush()
    return favorit


def entfernen(session: Session, benutzer_id: int, favorit_id: int) -> None:
    favorit = session.get(Favorit, favorit_id)
    if favorit is None or favorit.benutzer_id != benutzer_id:
        raise WerkplusFehler("Favorit nicht gefunden")
    session.delete(favorit)
    session.flush()
