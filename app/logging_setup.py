"""Zentrale Logging-Konfiguration (nur Standardbibliothek).

Ein einziger Handler auf ``stdout``, damit ``docker logs`` / der Synology
Container Manager alles sieht. Das Level kommt aus ``WERKPLUS_LOG_LEVEL``
(siehe :mod:`app.config`), Standard ``INFO``.

Aufgerufen wird :func:`konfiguriere_logging` einmalig beim Start in
``app.main``. Module besorgen sich ihren Logger wie üblich mit
``logging.getLogger(__name__)`` — dadurch steht der Modulname in jeder Zeile.

WICHTIG: In Logs gehören keine Passwörter, Tokens, Secret-Keys oder
vollständigen Personendaten. Für die Nachvollziehbarkeit reichen Login-Name,
IDs und der Grund eines Fehlers.
"""

import logging
import sys

from app.config import settings

# Zeitstempel, Level, Logger-Name, Nachricht
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_DATUM_FORMAT = "%Y-%m-%d %H:%M:%S"

# Merker, damit ein zweiter Aufruf (z. B. Reload) keinen doppelten Handler anlegt.
_handler: logging.Handler | None = None


def loglevel_aus_text(wert: str) -> int:
    """Übersetzt einen Levelnamen ("debug", "WARNING", …) in die logging-Konstante.

    Unbekannte oder leere Werte ergeben ``INFO`` — eine vertippte
    Umgebungsvariable darf den Start nicht verhindern.
    """
    stufe = logging.getLevelName((wert or "").strip().upper())
    return stufe if isinstance(stufe, int) else logging.INFO


def konfiguriere_logging(level: str | None = None) -> None:
    """Richtet das Root-Logging auf stdout ein (idempotent).

    ``level`` überschreibt bei Bedarf ``settings.log_level`` (Tests, Skripte).
    """
    global _handler
    stufe = loglevel_aus_text(level if level is not None else settings.log_level)
    root = logging.getLogger()
    if _handler is None:
        _handler = logging.StreamHandler(sys.stdout)
        _handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATUM_FORMAT))
        root.addHandler(_handler)
    _handler.setLevel(stufe)
    root.setLevel(stufe)
