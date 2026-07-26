"""Login/Logout samt Brute-Force-Schutz.

Der Brute-Force-Schutz zählt Fehlversuche in zwei getrennten In-Memory-Speichern:
je Login-Name (gegen gezieltes Durchprobieren eines Kontos) und je Client-IP
(gegen "Password Spraying", also ein Passwort gegen viele Login-Namen). Gesperrt
wird, wenn EINES der beiden Limits im Zeitfenster erreicht ist.

WICHTIG — Betrieb mit genau EINEM Worker: Die Zähler liegen absichtlich im
Prozessspeicher (keine zusätzliche Abhängigkeit wie Redis). Bei MEHREREN
uvicorn-Workern hätte jeder Worker eigene Zähler, das effektive Limit wäre also
Limit × Anzahl Worker und die Sperre entsprechend schwächer. WerkPlus ist daher
mit einem einzigen Worker zu betreiben — der Docker-Entrypoint startet genau
einen. Für mehrere Worker/Instanzen bräuchte es einen geteilten Store.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.services import berechtigung
from app.services.auth_service import authentifiziere
from app.templating import templates

router = APIRouter(tags=["auth"])

# Sicherheitsrelevante Ereignisse werden protokolliert: Login-Name, Grund,
# Zählerstand und — falls ermittelbar — die Client-IP.
# NIEMALS das Passwort (auch nicht gekürzt oder gehasht).
logger = logging.getLogger(__name__)

# --- Brute-Force-Schutz -------------------------------------------------------
# Limit je Login-Name: 5 Fehlversuche im Fenster. Konservativ, weil ein echter
# Benutzer sein eigenes Passwort selten mehr als ein paar Mal vertippt.
MAX_FEHLVERSUCHE = 5
# Limit je Client-IP: deutlich höher (20), weil hinter einer NAT-IP (Firmennetz,
# Mobilfunk) mehrere Mitarbeiter sitzen können und ein zu strenges IP-Limit sonst
# das ganze Büro aussperrt. 20 Fehlversuche in 5 Minuten sind für normale Nutzung
# nicht erreichbar, bremsen Spraying über viele Login-Namen aber wirksam:
# ohne IP-Limit könnte ein Angreifer je Name unter 5 bleiben und nie sperren.
MAX_FEHLVERSUCHE_IP = 20
SPERRFENSTER = timedelta(minutes=5)
# Harte Obergrenze je Speicher gegen Memory-DoS: Wer mit zufälligen Login-Namen
# (oder — bei trust_proxy — erfundenen IPs) flutet, darf den Zähler nicht
# beliebig wachsen lassen. Bei Überschreitung werden die ältesten Schlüssel
# verworfen. 1000 reicht für jeden realen Betrieb weit aus.
MAX_VERFOLGTE_SCHLUESSEL = 1000

# Schlüssel -> Zeitstempel fehlgeschlagener Versuche im Fenster.
# _fehlversuche: Login-Name, _fehlversuche_ip: Client-IP.
_fehlversuche: dict[str, list[datetime]] = {}
_fehlversuche_ip: dict[str, list[datetime]] = {}


def _reset_rate_limit(login: str | None = None, ip: str | None = None) -> None:
    """Setzt die Fehlversuch-Zähler zurück (ganz oder gezielt).

    Ohne Argumente werden BEIDE Speicher (Name und IP) geleert — so nutzen es die
    Tests zum Aufräumen. Mit ``login`` und/oder ``ip`` wird nur der jeweilige
    Schlüssel entfernt.
    """
    if login is None and ip is None:
        _fehlversuche.clear()
        _fehlversuche_ip.clear()
        return
    if login is not None:
        _fehlversuche.pop(login, None)
    if ip is not None:
        _fehlversuche_ip.pop(ip, None)


def _aktuelle(
    speicher: dict[str, list[datetime]], schluessel: str, jetzt: datetime
) -> list[datetime]:
    """Liefert (und bereinigt) die Fehlversuche eines Schlüssels innerhalb des Fensters."""
    grenze = jetzt - SPERRFENSTER
    versuche = [ts for ts in speicher.get(schluessel, []) if ts > grenze]
    if versuche:
        speicher[schluessel] = versuche
    else:
        speicher.pop(schluessel, None)
    return versuche


def _raeume_auf(speicher: dict[str, list[datetime]], jetzt: datetime) -> None:
    """Entfernt global alle abgelaufenen Zeitstempel und leeren Schlüssel."""
    grenze = jetzt - SPERRFENSTER
    for schluessel in list(speicher):
        rest = [ts for ts in speicher[schluessel] if ts > grenze]
        if rest:
            speicher[schluessel] = rest
        else:
            del speicher[schluessel]


def _kappe(speicher: dict[str, list[datetime]]) -> None:
    """Erzwingt die Obergrenze: die ältesten Schlüssel fallen zuerst heraus."""
    ueberhang = len(speicher) - MAX_VERFOLGTE_SCHLUESSEL
    if ueberhang <= 0:
        return
    # Sortiert nach jüngstem Zeitstempel: die am längsten unbenutzten zuerst.
    aelteste = sorted(speicher, key=lambda s: max(speicher[s]))[:ueberhang]
    for schluessel in aelteste:
        del speicher[schluessel]


def _aktuelle_fehlversuche(login: str, jetzt: datetime) -> list[datetime]:
    """Fehlversuche eines Login-Namens im Fenster (bereinigt)."""
    return _aktuelle(_fehlversuche, login, jetzt)


def _aktuelle_fehlversuche_ip(ip: str, jetzt: datetime) -> list[datetime]:
    """Fehlversuche einer Client-IP im Fenster (bereinigt)."""
    return _aktuelle(_fehlversuche_ip, ip, jetzt)


def _client_ip(request: Request) -> str | None:
    """Ermittelt die Client-IP — nur mit ``trust_proxy`` aus ``X-Forwarded-For``.

    ``X-Forwarded-For`` ist vom Client frei setzbar. Wird der Header blind
    ausgewertet, umgeht ein Angreifer das IP-Limit mit erfundenen Adressen.
    Deshalb wird er nur berücksichtigt, wenn ``WERKPLUS_TRUST_PROXY=1`` gesetzt
    ist (WerkPlus also ausschließlich hinter dem Reverse-Proxy erreichbar ist);
    dann gilt das LINKESTE Element als ursprünglicher Client.
    """
    if settings.trust_proxy:
        weitergeleitet = request.headers.get("x-forwarded-for", "")
        erster = weitergeleitet.split(",")[0].strip()
        if erster:
            return erster
    return request.client.host if request.client else None


def _ist_gesperrt(login: str, ip: str | None, jetzt: datetime) -> str | None:
    """Gibt den Sperrgrund ("login"/"ip") zurück oder ``None``, wenn frei."""
    if len(_aktuelle_fehlversuche(login, jetzt)) >= MAX_FEHLVERSUCHE:
        return "login"
    if ip and len(_aktuelle_fehlversuche_ip(ip, jetzt)) >= MAX_FEHLVERSUCHE_IP:
        return "ip"
    return None


def _fehlversuch_erfassen(login: str, ip: str | None, jetzt: datetime) -> None:
    """Erfasst einen Fehlversuch für Name und (falls bekannt) IP.

    Dabei werden beide Speicher global von abgelaufenen Einträgen befreit und auf
    ``MAX_VERFOLGTE_SCHLUESSEL`` gekappt — der Zähler kann so nicht unbegrenzt
    wachsen, auch nicht bei einer Flut zufälliger Login-Namen.
    """
    _fehlversuche.setdefault(login, []).append(jetzt)
    if ip:
        _fehlversuche_ip.setdefault(ip, []).append(jetzt)
    for speicher in (_fehlversuche, _fehlversuche_ip):
        _raeume_auf(speicher, jetzt)
        _kappe(speicher)


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
    ip = _client_ip(request)
    jetzt = datetime.now()
    # Die IP steht nur im Log, wenn sie überhaupt ermittelbar war.
    ip_zusatz = f" ip={ip!r}" if ip else ""
    minuten = int(SPERRFENSTER.total_seconds() // 60)

    grund = _ist_gesperrt(login_name, ip, jetzt)
    if grund is not None:
        if grund == "ip":
            logger.warning(
                "Login abgewiesen: Konto gesperrt (zu viele Fehlversuche von dieser "
                "Adresse) — login=%r%s",
                login_name,
                ip_zusatz,
            )
        else:
            logger.warning(
                "Login abgewiesen: Konto gesperrt (zu viele Fehlversuche) — login=%r%s",
                login_name,
                ip_zusatz,
            )
        return templates.TemplateResponse(
            request,
            "login.html",
            {"fehler": "Zu viele Fehlversuche, bitte später erneut versuchen."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    benutzer = authentifiziere(session, login_name, passwort)
    if benutzer is None:
        _fehlversuch_erfassen(login_name, ip, jetzt)
        anzahl = len(_aktuelle_fehlversuche(login_name, jetzt))
        anzahl_ip = len(_aktuelle_fehlversuche_ip(ip, jetzt)) if ip else 0
        logger.warning(
            "Fehlgeschlagener Login: login=%r%s grund=%s versuch=%d/%d",
            login_name,
            ip_zusatz,
            "Login unbekannt, inaktiv oder Passwort falsch",
            anzahl,
            MAX_FEHLVERSUCHE,
        )
        if anzahl >= MAX_FEHLVERSUCHE:
            logger.warning(
                "Sperre ausgelöst: login=%r ist für %d Minuten gesperrt (%d Fehlversuche)",
                login_name,
                minuten,
                anzahl,
            )
        if ip and anzahl_ip >= MAX_FEHLVERSUCHE_IP:
            logger.warning(
                "Sperre ausgelöst: ip=%r ist für %d Minuten gesperrt "
                "(%d Fehlversuche über beliebige Login-Namen)",
                ip,
                minuten,
                anzahl_ip,
            )
        return templates.TemplateResponse(
            request,
            "login.html",
            {"fehler": "Login oder Passwort falsch."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Erfolgreicher Login entsperrt den Namen. Der IP-Zähler bleibt bewusst
    # stehen: sonst könnte ein Angreifer mit einem eigenen gültigen Konto das
    # IP-Limit jederzeit zurücksetzen und weiter sprayen.
    _reset_rate_limit(login_name)
    logger.info("Erfolgreicher Login: login=%r%s", login_name, ip_zusatz)
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
