"""GitHub-Anbindung für den Bug-/Feature-Melder.

Legt Meldungen als GitHub-Issues an. Nutzt bewusst die stdlib ``urllib.request``
(kein ``httpx`` im Produktions-Image). Ist kein Token/Repo konfiguriert, bleibt
die Meldung rein lokal und diese Funktionen werfen ``GitHubNichtKonfiguriert``.
"""

import json
import logging
import urllib.error
import urllib.request

from app.config import settings

API_BASIS = "https://api.github.com"
TIMEOUT_SEKUNDEN = 10

# WICHTIG: In diesem Modul darf nie der GitHub-Token geloggt werden — also
# niemals Header oder ``settings.github_token`` in eine Logzeile schreiben.
logger = logging.getLogger(__name__)


class GitHubNichtKonfiguriert(Exception):
    """Es ist kein GitHub-Token oder -Repository hinterlegt."""


class GitHubFehler(Exception):
    """Der GitHub-API-Aufruf ist fehlgeschlagen (HTTP- oder Netzwerkfehler)."""


# Klartext-Hinweise je HTTP-Status. Rohes GitHub-JSON im Meldedialog hilft
# niemandem — der Anwender soll lesen können, WAS zu tun ist.
# Erfahrungswert: Ein abgeschnittener Token (beim Kopieren vorne/hinten verloren)
# ist die häufigste Ursache für 401.
_STATUS_HINWEISE: dict[int, str] = {
    401: (
        "GitHub lehnt den Zugangs-Token ab (401). Er ist ungültig, abgelaufen "
        "oder beim Kopieren unvollständig geraten. Bitte WERKPLUS_GITHUB_TOKEN "
        "in der .env prüfen (ein fine-grained Token beginnt mit „github_pat_“) "
        "und den Container neu starten."
    ),
    403: (
        "GitHub verweigert den Zugriff (403). Dem Token fehlt die Berechtigung "
        "„Issues: Read and write“ für dieses Repository — oder es greift gerade "
        "ein Anfragelimit."
    ),
    404: (
        "Das Repository wurde nicht gefunden (404). Bitte WERKPLUS_GITHUB_REPO "
        "prüfen (Format „owner/repo“) und ob der Token Zugriff auf dieses "
        "private Repository hat."
    ),
    422: (
        "GitHub hat die Meldung abgelehnt (422) — meist ein unzulässiges Label "
        "oder ein leerer Titel."
    ),
}


def _fehlertext(status: int, rohantwort: str) -> str:
    """Baut eine verständliche Fehlermeldung; Rohtext nur bei unbekanntem Status.

    Der Rohtext bleibt für unerwartete Fälle erhalten (Diagnose), wird aber
    gekürzt, damit der Meldedialog lesbar bleibt.
    """
    hinweis = _STATUS_HINWEISE.get(status)
    if hinweis:
        return hinweis
    kurz = " ".join(rohantwort.split())[:200]
    return f"GitHub-Fehler {status}: {kurz}"


def ist_konfiguriert() -> bool:
    """True, wenn sowohl Token als auch Repo gesetzt sind."""
    return bool(settings.github_token) and bool(settings.github_repo)


def erstelle_issue(titel: str, koerper: str, labels: list[str]) -> tuple[int, str]:
    """Legt ein GitHub-Issue an und gibt ``(issue_number, html_url)`` zurück.

    Wirft ``GitHubNichtKonfiguriert`` ohne Token/Repo und ``GitHubFehler`` bei
    HTTP- oder Netzwerkfehlern.
    """
    if not ist_konfiguriert():
        raise GitHubNichtKonfiguriert(
            "Kein GitHub-Token oder -Repository konfiguriert."
        )

    url = f"{API_BASIS}/repos/{settings.github_repo}/issues"
    daten = json.dumps(
        {"title": titel, "body": koerper, "labels": labels}
    ).encode("utf-8")
    anfrage = urllib.request.Request(
        url,
        data=daten,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "WerkPlus-Bug-Melder",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(anfrage, timeout=TIMEOUT_SEKUNDEN) as antwort:
            ergebnis = json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as fehler:
        try:
            meldung = fehler.read().decode("utf-8")
        except Exception as lesefehler:  # noqa: BLE001 — Fallback auf die Kurzmeldung
            logger.warning(
                "GitHub-Fehlerantwort (HTTP %s) nicht lesbar: %s", fehler.code, lesefehler
            )
            meldung = str(fehler)
        # Den Rohtext ins Log (dort ist Platz für Diagnose), in die Meldung nur
        # den Klartext-Hinweis. Der Token steckt nie in der Antwort.
        logger.warning("GitHub-Fehlerantwort (HTTP %s): %s", fehler.code, meldung)
        raise GitHubFehler(_fehlertext(fehler.code, meldung)) from fehler
    except urllib.error.URLError as fehler:
        raise GitHubFehler(
            f"GitHub nicht erreichbar: {fehler.reason}"
        ) from fehler

    return int(ergebnis["number"]), str(ergebnis.get("html_url", ""))
