from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
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
)
from app.services.buchung import registriere_lager_hooks
from app.templating import templates

app = FastAPI(title="WerkPlus", docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    same_site="lax",
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
app.include_router(suche.router)
app.include_router(einstellungen.router)
app.include_router(textbausteine.router)
app.include_router(benutzer.router)
app.include_router(bugreport.router)

# Lagerwirkung der Belegbuchung (Lieferschein/Wareneingang/Storno) aktivieren
registriere_lager_hooks()


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
