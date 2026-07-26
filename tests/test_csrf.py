"""Tests für CSRF-Schutz und Sicherheits-Header (app/security.py).

Die übrige Testsuite läuft mit abgeschaltetem CSRF (conftest); hier wird er
gezielt eingeschaltet.
"""

import pytest
from sqlmodel import Session

from app.config import settings
from app.models import Rolle
from app.models.benutzer import Benutzer
from app.security import SICHERHEITS_HEADER
from app.services.auth_service import hash_passwort


@pytest.fixture
def csrf_an():
    """CSRF-Schutz für diesen Test aktivieren (conftest schaltet ihn global ab)."""
    vorher = settings.csrf_aktiv
    settings.csrf_aktiv = True
    yield
    settings.csrf_aktiv = vorher


def _login(engine, client, rolle=Rolle.ADMIN):
    with Session(engine) as session:
        session.add(
            Benutzer(
                login="tester",
                name="Tester",
                passwort_hash=hash_passwort("geheim"),
                rolle=rolle,
                aktiv=True,
            )
        )
        session.commit()
    # /login ist bewusst CSRF-ausgenommen (noch keine Session vorhanden).
    r = client.post(
        "/login", data={"login": "tester", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


def _token(client) -> str:
    """Liest den CSRF-Token aus dem Meta-Tag der Startseite."""
    html = client.get("/").text
    marker = 'name="csrf-token" content="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def test_post_ohne_token_wird_abgelehnt(engine, client, csrf_an):
    _login(engine, client)
    r = client.post("/adressen/neu", data={"firma": "Ohne Token GmbH"})
    assert r.status_code == 403
    assert "CSRF" in r.text


def test_post_mit_formularfeld_klappt(engine, client, csrf_an):
    _login(engine, client)
    r = client.post(
        "/adressen/neu",
        data={"firma": "Mit Token GmbH", "csrf_token": _token(client)},
        follow_redirects=False,
    )
    assert r.status_code == 302


def test_post_mit_header_klappt(engine, client, csrf_an):
    """HTMX-Weg: Token als X-CSRF-Token-Header."""
    _login(engine, client)
    r = client.post(
        "/adressen/neu",
        data={"firma": "Header GmbH"},
        headers={"X-CSRF-Token": _token(client)},
        follow_redirects=False,
    )
    assert r.status_code == 302


def test_falscher_token_wird_abgelehnt(engine, client, csrf_an):
    _login(engine, client)
    r = client.post(
        "/adressen/neu", data={"firma": "Falsch GmbH", "csrf_token": "gefaelscht"}
    )
    assert r.status_code == 403


def test_get_braucht_keinen_token(engine, client, csrf_an):
    _login(engine, client)
    assert client.get("/adressen").status_code == 200


def test_sicherheits_header_gesetzt(engine, client):
    _login(engine, client)
    r = client.get("/")
    for name, wert in SICHERHEITS_HEADER.items():
        assert r.headers[name] == wert
    # Clickjacking-Schutz muss in der CSP strikt sein
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
