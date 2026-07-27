"""Programmversion und Codestand — sichtbar in der UI, genutzt fürs Cache-Busting.

Zwei Angaben, weil sie verschiedene Fragen beantworten:

* ``VERSION`` — die gepflegte Versionsnummer. Bei nennenswerten Änderungen
  hochzählen (Semantic Versioning: MAJOR.MINOR.PATCH).
* :func:`code_stand` — Zeitpunkt der neuesten Programmdatei. Ändert sich bei
  **jedem** Ausrollen automatisch, weil ``update.sh`` die Dateien ersetzt. Damit
  ist in der UI prüfbar, ob ein Update wirklich angekommen ist — auch wenn die
  Versionsnummer gleich blieb.

:func:`asset_version` liefert daraus eine kurze Kennung, die an die URLs von
CSS/JS gehängt wird. Ohne sie kann ein Browser nach einem Update altes
JavaScript mit neuem Servercode kombinieren — dann schlagen z. B. alle
schreibenden Anfragen am CSRF-Schutz fehl, weil das alte Skript den Token nicht
mitsendet. Genau dieser Fall ist im Betrieb aufgetreten.
"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path

VERSION = "1.1.0"

_APP_DIR = Path(__file__).resolve().parent
# Diese Verzeichnisse bestimmen, was der Browser ausgeliefert bekommt.
_BEOBACHTET = ("static", "templates")
_EIGENE_ENDUNGEN = {".py", ".css", ".js", ".html"}


@lru_cache(maxsize=1)
def code_stand() -> datetime:
    """Zeitstempel der neuesten Programmdatei (Modulebene, also einmal je Start).

    Absichtlich gecacht: Der Wert soll pro Prozessstart konstant sein — er
    beschreibt den ausgelieferten Stand, nicht den Moment der Anfrage. Nach einem
    Update startet der Container neu, damit wird er ohnehin neu bestimmt.
    """
    neueste = 0.0
    kandidaten: list[Path] = [p for p in _APP_DIR.glob("*.py")]
    for ordner in _BEOBACHTET:
        pfad = _APP_DIR / ordner
        if pfad.exists():
            kandidaten.extend(
                p for p in pfad.rglob("*") if p.suffix in _EIGENE_ENDUNGEN
            )
    for pfad in kandidaten:
        try:
            neueste = max(neueste, pfad.stat().st_mtime)
        except OSError:  # Datei verschwunden (Deploy läuft) — überspringen
            continue
    # Fallback auf "jetzt", falls nichts gefunden wurde (sollte nie passieren)
    return datetime.fromtimestamp(neueste) if neueste else datetime.now()


@lru_cache(maxsize=1)
def asset_version() -> str:
    """Kurze Kennung für CSS-/JS-URLs (``?v=…``), wechselt bei jedem Ausrollen."""
    return f"{VERSION}-{int(code_stand().timestamp())}"
