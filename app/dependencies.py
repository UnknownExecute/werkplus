"""FastAPI-Dependencies: Session-Login und Admin-Prüfung.

Die feingranulare Bereichsprüfung liegt in ``app.services.berechtigung``
(``bereich_wache``). ``require_admin`` bleibt hier, um Zirkelimporte zu vermeiden
(``berechtigung`` importiert ``get_current_user`` von hier).
"""

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.database import get_session
from app.models import Benutzer, Rolle, Rollenprofil


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> Benutzer:
    """Liest den angemeldeten Benutzer aus der Session; 401 mit Redirect-Hinweis sonst."""
    benutzer_id = request.session.get("benutzer_id")
    benutzer = session.get(Benutzer, benutzer_id) if benutzer_id else None
    if benutzer is None or not benutzer.aktiv:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"Location": "/login"},
        )
    return benutzer


def ist_admin(session: Session, benutzer: Benutzer) -> bool:
    """Admin = Rollenprofil mit ist_admin, Fallback: Rollenname 'admin'."""
    profil = session.exec(
        select(Rollenprofil).where(Rollenprofil.name == benutzer.rolle)
    ).first()
    return profil.ist_admin if profil is not None else (benutzer.rolle == Rolle.ADMIN)


def require_admin(
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Benutzer:
    if not ist_admin(session, benutzer):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur für Admins")
    return benutzer
