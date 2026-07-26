import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import database
from app.config import settings
from app.logging_setup import konfiguriere_logging
from app.routers import (
    adressen,
    aktivitaeten,
    anlagen,
    artikel,
    auth,
    belege,
    benutzer,
    bugreport,
    dashboard,
    einstellungen,
    lager,
    suche,
    textbausteine,
    zeiterfassung,
)
from app.security import csrf_schutz, sicherheits_header
from app.services.buchung import registriere_lager_hooks
from app.templating import templates

# Logging als Erstes einrichten, damit auch der Start protokolliert wird.
konfiguriere_logging()
logger = logging.getLogger(__name__)

# Globale CSRF-Wache: gilt für jede Route (Static-Mounts ausgenommen).
app = FastAPI(
    title="WerkPlus", docs_url=None, redoc_url=None,
    dependencies=[Depends(csrf_schutz)],
)

# Sicherheits-Header auf jede Antwort (Clickjacking/MIME/Referrer/CSP).
app.middleware("http")(sicherheits_header)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    same_site="lax",
    # Nur über HTTPS senden (Standard in Produktion). Bei reinem HTTP-Zugriff
    # per WERKPLUS_COOKIE_SECURE=0 abschaltbar — siehe app/config.py.
    https_only=settings.cookie_secure,
)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(adressen.router)
app.include_router(artikel.router)
app.include_router(aktivitaeten.router)
app.include_router(belege.router)
app.include_router(lager.router)
app.include_router(anlagen.router)
app.include_router(zeiterfassung.router)
app.include_router(suche.router)
app.include_router(einstellungen.router)
app.include_router(textbausteine.router)
app.include_router(benutzer.router)
app.include_router(bugreport.router)

# Lagerwirkung der Belegbuchung (Lieferschein/Wareneingang/Storno) aktivieren
registriere_lager_hooks()

logger.info("WerkPlus gestartet (env=%s, log_level=%s)", settings.env, settings.log_level)


def datenbank_erreichbar() -> bool:
    """Prüft die Datenbank mit einem minimalen ``SELECT 1``.

    Als Dependency von ``/health`` eingehängt: liefert nur True/False und wirft
    nie — der Health-Check soll einen klaren Status melden statt zu crashen.
    Der Grund landet im Log (nicht in der Antwort).
    """
    try:
        with database.engine.connect() as verbindung:
            verbindung.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health-Check: Datenbank nicht erreichbar")
        return False
    return True


@app.get("/health", include_in_schema=False)
def health(datenbank_ok: bool = Depends(datenbank_erreichbar)) -> JSONResponse:
    """Zustandsauskunft für Reverse-Proxy und Container-Healthcheck.

    Bewusst ohne Login und ohne jedes Detail zur Umgebung: nur ``ok``/``fehler``.
    Pfade, Versionen, Konfiguration oder Stacktraces gehören hier NICHT hinein,
    weil der Endpunkt unauthentifiziert erreichbar ist.
    """
    if datenbank_ok:
        return JSONResponse({"status": "ok", "datenbank": "ok"})
    return JSONResponse(
        {"status": "fehler", "datenbank": "fehler"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.exception_handler(StarletteHTTPException)
async def auth_redirect_handler(request: Request, exc: StarletteHTTPException):
    """401 → Login-Redirect; 403 → freundliche Kein-Zugriff-Seite; sonst Standard."""
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return templates.TemplateResponse(
            request, "403.html", {"detail": exc.detail}, status_code=status.HTTP_403_FORBIDDEN
        )
    return await http_exception_handler(request, exc)
