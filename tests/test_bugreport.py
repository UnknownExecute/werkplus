"""Tests für den Bug-/Feature-Melder (lokal speichern + GitHub-Sync)."""

from pathlib import Path

from sqlmodel import Session, select

from app.models import BugReport, MeldungTyp, Rolle
from app.models.benutzer import Benutzer
from app.routers import bugreport as bugreport_router
from app.services import github_service
from app.services.auth_service import hash_passwort

# 1x1-PNG (transparenter Pixel) – gültige Bilddatei für den Screenshot-Upload.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63640000000600000598b0c1000000000049454e44"
    "ae426082"
)


def _login(engine, client, login="tester", passwort="geheim"):
    """Legt einen Benutzer an und meldet den Client an."""
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login,
            name="Tester",
            passwort_hash=hash_passwort(passwort),
            rolle=Rolle.SACHBEARBEITER,
            aktiv=True,
        )
        session.add(benutzer)
        session.commit()
    r = client.post(
        "/login", data={"login": login, "passwort": passwort}, follow_redirects=False
    )
    assert r.status_code == 302


def test_liste_erfordert_login(client):
    r = client.get("/bugreport", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_melden_erfordert_login(client):
    r = client.post(
        "/bugreport", data={"titel": "X"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_bug_melden_ohne_github(engine, client):
    """Ohne konfiguriertes GitHub bleibt die Meldung lokal (gesendet=False)."""
    _login(engine, client)
    r = client.post(
        "/bugreport",
        data={"typ": "bug", "titel": "Knopf kaputt", "beschreibung": "Geht nicht."},
    )
    assert r.status_code == 200
    assert "gemeldet" in r.text.lower()

    with Session(engine) as session:
        report = session.exec(select(BugReport)).one()
        assert report.titel == "Knopf kaputt"
        assert report.typ == MeldungTyp.BUG
        assert report.gesendet is False
        assert report.melder_name == "Tester"
        assert report.browser  # User-Agent gesetzt


def test_bug_melden_mit_gemocktem_github(engine, client, monkeypatch):
    _login(engine, client)
    monkeypatch.setattr(github_service, "ist_konfiguriert", lambda: True)
    monkeypatch.setattr(
        github_service,
        "erstelle_issue",
        lambda titel, koerper, labels: (42, "https://github.com/x/y/issues/42"),
    )

    r = client.post(
        "/bugreport",
        data={"typ": "feature", "titel": "Wunsch", "beschreibung": "Bitte."},
    )
    assert r.status_code == 200
    assert "#42" in r.text

    with Session(engine) as session:
        report = session.exec(select(BugReport)).one()
        assert report.gesendet is True
        assert report.github_issue_nr == 42
        assert report.github_url == "https://github.com/x/y/issues/42"
        assert report.fehler == ""


def test_screenshot_upload(engine, client):
    _login(engine, client)
    r = client.post(
        "/bugreport",
        data={"typ": "bug", "titel": "Mit Bild"},
        files={"screenshot": ("shot.png", PNG_BYTES, "image/png")},
    )
    assert r.status_code == 200

    with Session(engine) as session:
        report = session.exec(select(BugReport)).one()
        report_id = report.id
        assert report.screenshot_datei == f"{report_id}.png"

    pfad = bugreport_router.SCREENSHOT_DIR / f"{report_id}.png"
    try:
        assert pfad.exists()
        assert pfad.read_bytes() == PNG_BYTES
    finally:
        if pfad.exists():
            pfad.unlink()


def test_liste_zeigt_reports_und_erneut_senden(engine, client):
    _login(engine, client)
    client.post("/bugreport", data={"typ": "bug", "titel": "Offener Fehler"})

    r = client.get("/bugreport")
    assert r.status_code == 200
    assert "Offener Fehler" in r.text
    # Offene Meldung bietet "Erneut an GitHub senden"
    assert "Erneut an GitHub senden" in r.text


def test_erneut_senden_mit_gemocktem_github(engine, client, monkeypatch):
    _login(engine, client)
    client.post("/bugreport", data={"typ": "bug", "titel": "Retry"})
    with Session(engine) as session:
        report_id = session.exec(select(BugReport)).one().id

    monkeypatch.setattr(github_service, "ist_konfiguriert", lambda: True)
    monkeypatch.setattr(
        github_service,
        "erstelle_issue",
        lambda titel, koerper, labels: (7, "https://github.com/x/y/issues/7"),
    )
    r = client.post(
        f"/bugreport/{report_id}/senden", follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/bugreport"

    with Session(engine) as session:
        report = session.get(BugReport, report_id)
        assert report.gesendet is True
        assert report.github_issue_nr == 7


def test_screenshot_dir_pfad():
    """Screenshot-Verzeichnis liegt unter app/static/bugreports."""
    assert (
        Path(bugreport_router.__file__).resolve().parent.parent
        / "static"
        / "bugreports"
    ) == bugreport_router.SCREENSHOT_DIR
