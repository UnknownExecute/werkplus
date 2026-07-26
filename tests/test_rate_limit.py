"""Tests für den gehärteten Brute-Force-Schutz beim Login.

Geprüft werden: das bestehende Limit je Login-Name, das zusätzliche Limit je
Client-IP (gegen Password-Spraying), die sichere IP-Ermittlung (``X-Forwarded-For``
nur bei ``trust_proxy``) und die Obergrenze der verfolgten Schlüssel.
"""

from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

from app.config import settings
from app.models import Benutzer, Rolle
from app.routers.auth import (
    MAX_FEHLVERSUCHE,
    MAX_FEHLVERSUCHE_IP,
    MAX_VERFOLGTE_SCHLUESSEL,
    SPERRFENSTER,
    _client_ip,
    _fehlversuch_erfassen,
    _fehlversuche,
    _fehlversuche_ip,
    _reset_rate_limit,
)
from app.services.auth_service import hash_passwort

PASSWORT = "geheim-und-lang-genug"


@pytest.fixture(autouse=True)
def _zaehler_leeren():
    """Jeder Test startet und endet mit leeren Zählern (Name UND IP)."""
    _reset_rate_limit()
    yield
    _reset_rate_limit()


@pytest.fixture(autouse=True)
def _proxy_aus(monkeypatch):
    """Standardfall: Proxy-Header wird NICHT vertraut."""
    monkeypatch.setattr(settings, "trust_proxy", False)


def _lege_benutzer_an(engine, login="admin"):
    with Session(engine) as session:
        session.add(
            Benutzer(
                login=login,
                name="Test",
                passwort_hash=hash_passwort(PASSWORT),
                rolle=Rolle.ADMIN,
                aktiv=True,
            )
        )
        session.commit()


class _Client:
    def __init__(self, host):
        self.host = host


class _Request:
    """Minimaler Request-Ersatz für _client_ip (ohne HTTP-Stack)."""

    def __init__(self, host="10.0.0.1", headers=None):
        self.client = _Client(host) if host else None
        self.headers = headers or {}


# --- Namens-Limit (Verhalten wie bisher) --------------------------------------


def test_namens_limit_sperrt_nach_max_fehlversuchen(engine, client):
    _lege_benutzer_an(engine)
    for _ in range(MAX_FEHLVERSUCHE):
        assert client.post("/login", data={"login": "admin", "passwort": "x"}).status_code == 401

    # Auch mit korrektem Passwort: gesperrt.
    r = client.post("/login", data={"login": "admin", "passwort": PASSWORT})
    assert r.status_code == 429


def test_namens_limit_greift_nicht_bei_anderem_namen(engine, client):
    """Ein anderer Login-Name bleibt unbeeinflusst (solange das IP-Limit hält)."""
    _lege_benutzer_an(engine)
    for _ in range(MAX_FEHLVERSUCHE):
        client.post("/login", data={"login": "fremd", "passwort": "x"})

    r = client.post("/login", data={"login": "admin", "passwort": PASSWORT}, follow_redirects=False)
    assert r.status_code == 302


def test_erfolgreicher_login_setzt_namens_zaehler_zurueck(engine, client):
    _lege_benutzer_an(engine)
    for _ in range(MAX_FEHLVERSUCHE - 1):
        client.post("/login", data={"login": "admin", "passwort": "x"})
    assert len(_fehlversuche["admin"]) == MAX_FEHLVERSUCHE - 1

    r = client.post("/login", data={"login": "admin", "passwort": PASSWORT}, follow_redirects=False)
    assert r.status_code == 302
    assert "admin" not in _fehlversuche


# --- IP-Limit (Password-Spraying) ---------------------------------------------


def test_ip_limit_ist_hoeher_als_namens_limit():
    """Mehrere Mitarbeiter können hinter derselben NAT-IP sitzen."""
    assert MAX_FEHLVERSUCHE_IP > MAX_FEHLVERSUCHE


def test_ip_limit_bremst_spraying_ueber_viele_namen(engine, client):
    """Ein Passwort gegen viele Namen: je Name unter dem Limit, IP-Limit greift."""
    _lege_benutzer_an(engine)
    for nummer in range(MAX_FEHLVERSUCHE_IP):
        antwort = client.post("/login", data={"login": f"opfer{nummer}", "passwort": "x"})
        assert antwort.status_code == 401
        # Kein einzelner Name erreicht das Namens-Limit.
        assert len(_fehlversuche[f"opfer{nummer}"]) == 1

    # Jetzt ist die IP dran — sogar ein gültiges Login wird abgewiesen.
    r = client.post("/login", data={"login": "admin", "passwort": PASSWORT})
    assert r.status_code == 429
    assert "Zu viele Fehlversuche" in r.text


def test_ip_sperre_wird_geloggt(engine, client, caplog):
    import logging

    _lege_benutzer_an(engine)
    with caplog.at_level(logging.WARNING, logger="app.routers.auth"):
        for nummer in range(MAX_FEHLVERSUCHE_IP):
            client.post("/login", data={"login": f"spray{nummer}", "passwort": "x"})
        client.post("/login", data={"login": "admin", "passwort": PASSWORT})

    assert "Sperre ausgelöst" in caplog.text
    assert "ip=" in caplog.text
    assert PASSWORT not in caplog.text


# --- Sichere IP-Ermittlung ----------------------------------------------------


def test_x_forwarded_for_wird_ohne_trust_proxy_ignoriert(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy", False)
    anfrage = _Request("10.0.0.1", {"x-forwarded-for": "1.2.3.4"})
    assert _client_ip(anfrage) == "10.0.0.1"


def test_x_forwarded_for_wird_mit_trust_proxy_benutzt(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy", True)
    # Linkestes Element = ursprünglicher Client.
    anfrage = _Request("10.0.0.1", {"x-forwarded-for": "1.2.3.4, 10.0.0.9"})
    assert _client_ip(anfrage) == "1.2.3.4"


def test_leerer_x_forwarded_for_faellt_auf_verbindungsadresse_zurueck(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy", True)
    assert _client_ip(_Request("10.0.0.1", {"x-forwarded-for": "  "})) == "10.0.0.1"
    assert _client_ip(_Request("10.0.0.1")) == "10.0.0.1"


def test_ohne_client_keine_ip(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy", False)
    assert _client_ip(_Request(None)) is None


def test_gefaelschter_header_umgeht_ip_limit_nicht(engine, client):
    """trust_proxy=False: wechselnde X-Forwarded-For-Werte helfen dem Angreifer nicht."""
    _lege_benutzer_an(engine)
    for nummer in range(MAX_FEHLVERSUCHE_IP):
        client.post(
            "/login",
            data={"login": f"opfer{nummer}", "passwort": "x"},
            headers={"X-Forwarded-For": f"9.9.9.{nummer}"},
        )
    # Alle Versuche zählten auf die echte Verbindungsadresse.
    assert len(_fehlversuche_ip) == 1
    r = client.post("/login", data={"login": "admin", "passwort": PASSWORT})
    assert r.status_code == 429


def test_mit_trust_proxy_zaehlt_jede_weitergeleitete_ip_eigenstaendig(engine, client, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy", True)
    _lege_benutzer_an(engine)
    for nummer in range(MAX_FEHLVERSUCHE_IP):
        client.post(
            "/login",
            data={"login": f"opfer{nummer}", "passwort": "x"},
            headers={"X-Forwarded-For": f"9.9.9.{nummer}"},
        )
    # Je weitergeleiteter IP genau ein Fehlversuch → keine Sperre.
    assert len(_fehlversuche_ip) == MAX_FEHLVERSUCHE_IP
    r = client.post(
        "/login",
        data={"login": "admin", "passwort": PASSWORT},
        headers={"X-Forwarded-For": "9.9.9.250"},
        follow_redirects=False,
    )
    assert r.status_code == 302


# --- Speicherwachstum ---------------------------------------------------------


def test_obergrenze_der_verfolgten_schluessel_wird_eingehalten():
    jetzt = datetime.now()
    for nummer in range(MAX_VERFOLGTE_SCHLUESSEL + 250):
        _fehlversuch_erfassen(f"zufall{nummer}", f"10.1.{nummer // 256}.{nummer % 256}", jetzt)

    assert len(_fehlversuche) <= MAX_VERFOLGTE_SCHLUESSEL
    assert len(_fehlversuche_ip) <= MAX_VERFOLGTE_SCHLUESSEL
    # Die jüngsten Schlüssel überleben, die ältesten sind verworfen.
    assert f"zufall{MAX_VERFOLGTE_SCHLUESSEL + 249}" in _fehlversuche
    assert "zufall0" not in _fehlversuche


def test_abgelaufene_eintraege_werden_global_aufgeraeumt():
    alt = datetime.now() - SPERRFENSTER - timedelta(seconds=1)
    for nummer in range(50):
        _fehlversuch_erfassen(f"alt{nummer}", f"10.2.0.{nummer}", alt)
    assert len(_fehlversuche) == 50

    # Der nächste Fehlversuch räumt die abgelaufenen Einträge global weg.
    _fehlversuch_erfassen("neu", "10.3.0.1", datetime.now())
    assert list(_fehlversuche) == ["neu"]
    assert list(_fehlversuche_ip) == ["10.3.0.1"]


def test_reset_leert_beide_speicher():
    jetzt = datetime.now()
    _fehlversuch_erfassen("wer-auch-immer", "10.4.0.1", jetzt)
    assert _fehlversuche and _fehlversuche_ip

    _reset_rate_limit()
    assert not _fehlversuche
    assert not _fehlversuche_ip


def test_reset_kann_gezielt_namen_und_ip_loeschen():
    jetzt = datetime.now()
    _fehlversuch_erfassen("a", "10.5.0.1", jetzt)
    _fehlversuch_erfassen("b", "10.5.0.2", jetzt)

    _reset_rate_limit(login="a")
    assert "a" not in _fehlversuche and "b" in _fehlversuche
    assert "10.5.0.1" in _fehlversuche_ip

    _reset_rate_limit(ip="10.5.0.1")
    assert "10.5.0.1" not in _fehlversuche_ip and "10.5.0.2" in _fehlversuche_ip
