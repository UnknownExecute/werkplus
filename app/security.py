"""Web-Sicherheit: CSRF-Schutz, Sicherheits-Header, sichere Cookies.

Drei Bausteine, in ``app.main`` verdrahtet:

* :func:`csrf_schutz` — globale Dependency; erzwingt bei schreibenden Methoden
  einen gültigen CSRF-Token (Header ``X-CSRF-Token`` für HTMX oder Formularfeld
  ``csrf_token`` für native Formulare). Der Token liegt in der Session; der
  Client liest ihn aus ``<meta name="csrf-token">`` (siehe ``werkplus.js``).
* :func:`sicherheits_header` — Middleware, die Clickjacking-/MIME-/Referrer-
  Schutz und eine CSP setzt.
* Das ``Secure``-Flag des Session-Cookies wird in ``app.main`` abhängig von
  ``settings.env`` gesetzt.

CSRF ist per Default aktiv (``settings.csrf_aktiv``); die Testsuite schaltet es
global ab und deckt es gezielt in ``tests/test_csrf.py`` ab.
"""

import secrets

from fastapi import HTTPException, Request, status

from app.config import settings

# Methoden ohne Seiteneffekt brauchen keinen CSRF-Token.
SICHERE_METHODEN = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Diese Pfade sind vom CSRF-Schutz ausgenommen: /login besitzt noch keine
# etablierte Session, /logout verwirft sie ohnehin. Beide sind durch
# SameSite=Lax abgedeckt.
CSRF_AUSNAHMEN = {"/login", "/logout"}

# Content-Security-Policy: Quellen auf 'self' beschränken. 'unsafe-inline' für
# Skripte/Styles ist nötig (Inline-Theme-Setter + Inline-Handler in base.html);
# der zentrale Clickjacking-Schutz (frame-ancestors 'none') bleibt strikt.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)

SICHERHEITS_HEADER = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CSP,
}


def _ensure_token(request: Request) -> str:
    """Liefert den CSRF-Token der Session; legt ihn beim ersten Bedarf an."""
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def csrf_token(request: Request) -> str:
    """Template-Global: aktueller CSRF-Token (für ``<meta>``/Hidden-Feld)."""
    return _ensure_token(request)


async def _uebermittelter_token(request: Request) -> str | None:
    """Token aus Header (HTMX) oder Formularfeld (natives Formular) lesen."""
    header_token = request.headers.get("x-csrf-token")
    if header_token:
        return header_token
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 — kein/kaputtes Formular ⇒ kein Token
        return None
    wert = form.get("csrf_token")
    return wert if isinstance(wert, str) else None


async def csrf_schutz(request: Request) -> None:
    """Globale Dependency: verifiziert den CSRF-Token bei schreibenden Requests.

    Sichere Methoden und ausgenommene Pfade passieren ungeprüft (der Token wird
    dabei sichergestellt, damit ihn Templates ausliefern können).
    """
    _ensure_token(request)
    if not settings.csrf_aktiv:
        return
    if request.method in SICHERE_METHODEN or request.url.path in CSRF_AUSNAHMEN:
        return
    erwartet = request.session.get("csrf")
    geliefert = await _uebermittelter_token(request)
    if not erwartet or not geliefert or not secrets.compare_digest(erwartet, geliefert):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF-Token fehlt oder ist ungültig. Bitte Seite neu laden.",
        )


async def sicherheits_header(request: Request, call_next):
    """Middleware: setzt Sicherheits-Header auf jede Antwort."""
    response = await call_next(request)
    for name, wert in SICHERHEITS_HEADER.items():
        response.headers.setdefault(name, wert)
    return response
