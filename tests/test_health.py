"""Tests für den Health-Check-Endpunkt (/health).

Der Endpunkt muss ohne Login erreichbar sein (Reverse-Proxy/Container-Healthcheck),
die Datenbank prüfen und im Fehlerfall 503 liefern statt zu crashen.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app import database, main


class _KaputteEngine:
    """Engine-Ersatz, dessen ``connect()`` immer scheitert (DB nicht erreichbar)."""

    def connect(self):
        raise OperationalError("SELECT 1", None, Exception("Datenbank weg"))


@pytest.fixture
def db_engine_gesetzt(engine, monkeypatch):
    """Lenkt die Health-Prüfung auf die In-Memory-Testdatenbank."""
    monkeypatch.setattr(database, "engine", engine)


def test_health_ohne_login_ok(client, db_engine_gesetzt):
    """Ohne Anmeldung: 200 mit status/datenbank = ok (kein Login-Redirect)."""
    r = client.get("/health", follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "datenbank": "ok"}


def test_health_verraet_keine_details(client, db_engine_gesetzt):
    """Die Antwort enthält ausschließlich die beiden Statusfelder."""
    r = client.get("/health")
    assert set(r.json()) == {"status", "datenbank"}
    text = r.text.lower()
    for verboten in ("sqlite", "traceback", "secret", "token", "/data", "werkplus.db"):
        assert verboten not in text


def test_health_bei_kaputter_datenbank_503(client, monkeypatch, caplog):
    """Scheitert ``SELECT 1``, kommt 503 — und keine Exception."""
    monkeypatch.setattr(database, "engine", _KaputteEngine())
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "fehler", "datenbank": "fehler"}
    # Der Grund gehört ins Log, nicht in die Antwort.
    assert "Datenbank nicht erreichbar" in caplog.text
    assert "Datenbank weg" not in r.text


def test_datenbank_erreichbar_prueft_wirklich(engine, monkeypatch):
    """Die Prüffunktion liefert True/False und wirft nie."""
    monkeypatch.setattr(database, "engine", engine)
    assert main.datenbank_erreichbar() is True

    monkeypatch.setattr(database, "engine", _KaputteEngine())
    assert main.datenbank_erreichbar() is False


def test_health_ist_von_der_session_unabhaengig(client, db_engine_gesetzt):
    """Auch nach Logout/ohne Session bleibt der Endpunkt erreichbar."""
    client.post("/logout", follow_redirects=False)
    assert client.get("/health").status_code == 200
