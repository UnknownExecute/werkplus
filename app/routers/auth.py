from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.database import get_session
from app.services import berechtigung
from app.services.auth_service import authentifiziere
from app.templating import templates

router = APIRouter(tags=["auth"])

# --- Brute-Force-Schutz -------------------------------------------------------
# Einfacher In-Memory-Zähler pro Login-Name. Bewusst ohne Redis: für das MVP
# reicht ein Pro-Prozess-Zähler (Single-Process). Bei mehreren Workern gilt das
# Limit je Prozess — für stärkere Garantien wäre ein geteilter Store nötig.
MAX_FEHLVERSUCHE = 5
SPERRFENSTER = timedelta(minutes=5)

# login-name -> Liste der Zeitstempel fehlgeschlagener Versuche im Fenster.
_fehlversuche: dict[str, list[datetime]] = {}


def _reset_rate_limit(login: str | None = None) -> None:
    """Setzt den Fehlversuch-Zähler zurück (ganz oder für einen Login).

    Wird bei erfolgreichem Login genutzt und steht Tests zum Aufräumen bereit.
    """
    if login is None:
        _fehlversuche.clear()
    else:
        _fehlversuche.pop(login, None)


def _aktuelle_fehlversuche(login: str, jetzt: datetime) -> list[datetime]:
    """Liefert (und bereinigt) die Fehlversuche eines Logins innerhalb des Fensters."""
    grenze = jetzt - SPERRFENSTER
    versuche = [ts for ts in _fehlversuche.get(login, []) if ts > grenze]
    if versuche:
        _fehlversuche[login] = versuche
    else:
        _fehlversuche.pop(login, None)
    return versuche


def _ist_gesperrt(login: str, jetzt: datetime) -> bool:
    return len(_aktuelle_fehlversuche(login, jetzt)) >= MAX_FEHLVERSUCHE


def _fehlversuch_erfassen(login: str, jetzt: datetime) -> None:
    _fehlversuche.setdefault(login, []).append(jetzt)


@router.get("/login")
def login_form(request: Request):
    if request.session.get("benutzer_id"):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "login.html", {"fehler": None})


@router.post("/login")
def login(
    request: Request,
    login: str = Form(...),
    passwort: str = Form(...),
    session: Session = Depends(get_session),
):
    login_name = login.strip()
    jetzt = datetime.now()

    if _ist_gesperrt(login_name, jetzt):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"fehler": "Zu viele Fehlversuche, bitte später erneut versuchen."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    benutzer = authentifiziere(session, login_name, passwort)
    if benutzer is None:
        _fehlversuch_erfassen(login_name, jetzt)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"fehler": "Login oder Passwort falsch."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _reset_rate_limit(login_name)
    profil = berechtigung.lade_profil(session, benutzer)
    request.session["benutzer_id"] = benutzer.id
    request.session["benutzer_name"] = benutzer.name
    request.session["rolle"] = str(benutzer.rolle)
    request.session["rechte"] = berechtigung.rechte_als_dict(profil)
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
