"""Zentrale Konfiguration (Umgebungsvariablen mit Defaults für On-Premise-Betrieb)."""

import os
from dataclasses import dataclass, field


@dataclass
class Firmendaten:
    """Eigene Stammdaten des Betriebs — erscheinen auf Beleg-PDFs (§ 11 UStG)."""

    name: str = os.environ.get("WERKPLUS_FIRMA_NAME", "Muster Haustechnik GmbH")
    strasse: str = os.environ.get("WERKPLUS_FIRMA_STRASSE", "Gewerbeparkstraße 12")
    plz: str = os.environ.get("WERKPLUS_FIRMA_PLZ", "4020")
    ort: str = os.environ.get("WERKPLUS_FIRMA_ORT", "Linz")
    land: str = os.environ.get("WERKPLUS_FIRMA_LAND", "Österreich")
    uid: str = os.environ.get("WERKPLUS_FIRMA_UID", "ATU12345678")
    telefon: str = os.environ.get("WERKPLUS_FIRMA_TELEFON", "+43 732 123456")
    email: str = os.environ.get("WERKPLUS_FIRMA_EMAIL", "office@muster-haustechnik.at")
    iban: str = os.environ.get("WERKPLUS_FIRMA_IBAN", "AT12 3456 7890 1234 5678")


# Dev-Default für secret_key; darf in Produktion nicht verwendet werden.
DEV_SECRET_DEFAULT = "dev-secret-bitte-aendern"
# Mindestlänge für einen produktionstauglichen Schlüssel.
MIN_SECRET_KEY_LAENGE = 16


@dataclass
class Settings:
    database_url: str = os.environ.get("WERKPLUS_DATABASE_URL", "sqlite:///werkplus.db")
    secret_key: str = os.environ.get("WERKPLUS_SECRET_KEY", DEV_SECRET_DEFAULT)
    session_max_age: int = int(os.environ.get("WERKPLUS_SESSION_MAX_AGE", str(8 * 3600)))
    env: str = os.environ.get("WERKPLUS_ENV", "development")
    # Log-Level der Anwendung (DEBUG/INFO/WARNING/ERROR/CRITICAL). Ausgabe geht
    # nach stdout, ist also über "docker logs" sichtbar — siehe app/logging_setup.py.
    log_level: str = os.environ.get("WERKPLUS_LOG_LEVEL", "INFO")
    # Bug-Melder → GitHub Issues (privates Repo). Token = fein granulierter PAT mit
    # Issues-Schreibrecht; repo im Format "owner/repo". Leer = nur lokal speichern.
    github_token: str = os.environ.get("WERKPLUS_GITHUB_TOKEN", "")
    github_repo: str = os.environ.get("WERKPLUS_GITHUB_REPO", "")
    # CSRF-Schutz für schreibende Requests. Standard: an. Die Testsuite schaltet
    # ihn global ab (und deckt ihn gezielt in tests/test_csrf.py ab).
    csrf_aktiv: bool = os.environ.get("WERKPLUS_CSRF", "1") != "0"
    # Secure-Flag des Session-Cookies. Standard: in Produktion an (HTTPS-Pflicht).
    # WICHTIG: Bei reinem HTTP-Zugriff (z. B. direkt http://<NAS-IP>:9999 ohne
    # HTTPS-Reverse-Proxy) sendet der Browser das Cookie dann NICHT — Login
    # scheitert. In diesem Fall WERKPLUS_COOKIE_SECURE=0 setzen.
    cookie_secure: bool = os.environ.get(
        "WERKPLUS_COOKIE_SECURE",
        "1" if os.environ.get("WERKPLUS_ENV", "development") == "production" else "0",
    ) != "0"
    # Vertrauen in Proxy-Header (X-Forwarded-For) für die Client-IP-Ermittlung.
    # Standard: AUS. Nur einschalten (WERKPLUS_TRUST_PROXY=1), wenn WerkPlus
    # AUSSCHLIESSLICH über den Reverse-Proxy erreichbar ist — der Header ist vom
    # Client frei setzbar, sonst könnte ein Angreifer das IP-Limit des
    # Brute-Force-Schutzes mit erfundenen IPs umgehen (Spoofing).
    trust_proxy: bool = os.environ.get("WERKPLUS_TRUST_PROXY", "0") == "1"
    firma: Firmendaten = field(default_factory=Firmendaten)


def pruefe_produktionssicherheit(settings: Settings) -> None:
    """Verweigert unsichere Konfiguration in Produktion (Dev-Default/zu kurzer Schlüssel).

    In der Entwicklungsumgebung (env != "production") passiert nichts — Import und
    Tests bleiben lauffähig.
    """
    if settings.env != "production":
        return
    if settings.secret_key == DEV_SECRET_DEFAULT:
        raise RuntimeError(
            "WERKPLUS_SECRET_KEY muss in Produktion gesetzt sein "
            "(der Dev-Default ist nicht erlaubt)."
        )
    if len(settings.secret_key) < MIN_SECRET_KEY_LAENGE:
        raise RuntimeError(
            "WERKPLUS_SECRET_KEY muss in Produktion mindestens "
            f"{MIN_SECRET_KEY_LAENGE} Zeichen lang sein."
        )


settings = Settings()

# Sicherheitsprüfung greift nur in Produktion; in development bleibt der Import ohne Effekt.
if settings.env == "production":
    pruefe_produktionssicherheit(settings)
