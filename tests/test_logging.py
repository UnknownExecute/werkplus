"""Tests für das zentrale Logging.

Geprüft wird die Konfiguration (Level aus der Einstellung) und dass die
sicherheitsrelevanten Ereignisse tatsächlich Spuren hinterlassen — ohne dass
Passwörter oder Tokens im Log landen.
"""

import logging
import urllib.error

from sqlmodel import Session, select

from app import logging_setup
from app.config import settings
from app.models import BugReport, Rolle
from app.models.benutzer import Benutzer
from app.routers.auth import MAX_FEHLVERSUCHE, _reset_rate_limit
from app.services import github_service
from app.services.auth_service import hash_passwort

PASSWORT = "streng-geheim-123"


def _lege_benutzer_an(engine, login="admin", passwort=PASSWORT, rolle=Rolle.ADMIN):
    with Session(engine) as session:
        session.add(
            Benutzer(
                login=login,
                name="Test",
                passwort_hash=hash_passwort(passwort),
                rolle=rolle,
                aktiv=True,
            )
        )
        session.commit()


# --- Konfiguration ------------------------------------------------------------


def test_loglevel_aus_text():
    assert logging_setup.loglevel_aus_text("debug") == logging.DEBUG
    assert logging_setup.loglevel_aus_text(" WARNING ") == logging.WARNING
    # Unsinn und Leerwerte fallen auf INFO zurück (darf den Start nie verhindern).
    assert logging_setup.loglevel_aus_text("quatsch") == logging.INFO
    assert logging_setup.loglevel_aus_text("") == logging.INFO


def test_konfiguriere_logging_setzt_level_und_bleibt_idempotent():
    root = logging.getLogger()
    vorher_level = root.level
    vorher_handler = len(root.handlers)
    try:
        logging_setup.konfiguriere_logging("DEBUG")
        assert root.level == logging.DEBUG
        anzahl = len(root.handlers)
        # Zweiter Aufruf darf keinen zweiten Handler anlegen.
        logging_setup.konfiguriere_logging("WARNING")
        assert len(root.handlers) == anzahl
        assert root.level == logging.WARNING
    finally:
        logging_setup.konfiguriere_logging(logging.getLevelName(vorher_level))
        root.setLevel(vorher_level)
        assert len(root.handlers) >= vorher_handler


def test_log_level_einstellung_vorhanden():
    """Die Settings-Dataclass kennt log_level (Default INFO)."""
    assert settings.log_level
    assert logging_setup.loglevel_aus_text(settings.log_level) in (
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    )


# --- Login-Ereignisse ---------------------------------------------------------


def test_fehlgeschlagener_login_wird_geloggt(engine, client, caplog):
    _reset_rate_limit()
    _lege_benutzer_an(engine)
    with caplog.at_level(logging.WARNING, logger="app.routers.auth"):
        r = client.post("/login", data={"login": "admin", "passwort": "falsch"})
    assert r.status_code == 401

    assert "Fehlgeschlagener Login" in caplog.text
    assert "admin" in caplog.text
    # Das Passwort darf niemals im Log stehen.
    assert "falsch" not in caplog.text.replace("Passwort falsch", "")
    assert PASSWORT not in caplog.text
    _reset_rate_limit()


def test_ausgeloeste_sperre_wird_geloggt(engine, client, caplog):
    _reset_rate_limit()
    _lege_benutzer_an(engine)
    with caplog.at_level(logging.WARNING, logger="app.routers.auth"):
        for _ in range(MAX_FEHLVERSUCHE):
            client.post("/login", data={"login": "admin", "passwort": "nope"})
        # Der Folgeversuch wird abgewiesen — auch das wird protokolliert.
        client.post("/login", data={"login": "admin", "passwort": PASSWORT})

    assert "Sperre ausgelöst" in caplog.text
    assert "Konto gesperrt" in caplog.text
    assert PASSWORT not in caplog.text
    _reset_rate_limit()


def test_erfolgreicher_login_wird_auf_info_geloggt(engine, client, caplog):
    _reset_rate_limit()
    _lege_benutzer_an(engine)
    with caplog.at_level(logging.INFO, logger="app.routers.auth"):
        r = client.post(
            "/login", data={"login": "admin", "passwort": PASSWORT}, follow_redirects=False
        )
    assert r.status_code == 302

    treffer = [
        s for s in caplog.records if "Erfolgreicher Login" in s.getMessage()
    ]
    assert treffer, caplog.text
    assert treffer[0].levelno == logging.INFO
    assert "admin" in treffer[0].getMessage()
    assert PASSWORT not in caplog.text
    _reset_rate_limit()


# --- Bisher stumm geschluckte Fehler -----------------------------------------


def test_github_fehler_wird_geloggt_und_meldung_bleibt_lokal(engine, client, monkeypatch, caplog):
    """Der GitHub-Fehler landet im Log, die Meldung bleibt trotzdem gespeichert."""
    _reset_rate_limit()
    _lege_benutzer_an(engine, login="melder", rolle=Rolle.SACHBEARBEITER)
    r = client.post(
        "/login", data={"login": "melder", "passwort": PASSWORT}, follow_redirects=False
    )
    assert r.status_code == 302

    token = "ghp-geheimer-token-darf-nicht-ins-log"
    monkeypatch.setattr(settings, "github_token", token)
    monkeypatch.setattr(settings, "github_repo", "besitzer/repo")

    def _wirf(titel, koerper, labels):
        raise github_service.GitHubFehler("GitHub-Fehler 401: Bad credentials")

    monkeypatch.setattr(github_service, "erstelle_issue", _wirf)

    with caplog.at_level(logging.ERROR, logger="app.routers.bugreport"):
        antwort = client.post("/bugreport", data={"typ": "bug", "titel": "Knopf kaputt"})
    assert antwort.status_code == 200

    assert "GitHub-Übertragung" in caplog.text
    assert "fehlgeschlagen" in caplog.text
    assert token not in caplog.text

    with Session(engine) as session:
        report = session.exec(select(BugReport)).one()
        assert report.titel == "Knopf kaputt"
        assert report.gesendet is False
        assert "401" in report.fehler
    _reset_rate_limit()


class _HTTPErrorOhneBody(urllib.error.HTTPError):
    """HTTPError, dessen Antwortkörper nicht gelesen werden kann."""

    def read(self, *args, **kwargs):
        raise OSError("Verbindung abgebrochen")


def test_unlesbare_github_fehlerantwort_wird_geloggt(monkeypatch, caplog):
    """Der bislang stumme except-Zweig in github_service protokolliert jetzt."""
    token = "ghp-noch-ein-geheimnis"
    monkeypatch.setattr(settings, "github_token", token)
    monkeypatch.setattr(settings, "github_repo", "besitzer/repo")

    def _urlopen(anfrage, timeout=None):
        raise _HTTPErrorOhneBody("https://api.github.com/x", 502, "Bad Gateway", {}, None)

    monkeypatch.setattr(github_service.urllib.request, "urlopen", _urlopen)

    with caplog.at_level(logging.WARNING, logger="app.services.github_service"):
        try:
            github_service.erstelle_issue("Titel", "Körper", ["bug"])
        except github_service.GitHubFehler as fehler:
            assert "502" in str(fehler)
        else:
            raise AssertionError("GitHubFehler erwartet")

    assert "nicht lesbar" in caplog.text
    assert token not in caplog.text
