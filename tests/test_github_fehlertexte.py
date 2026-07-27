"""Tests für die Klartext-Fehlermeldungen der GitHub-Anbindung.

Anlass aus dem Betrieb: Im Meldedialog stand rohes GitHub-JSON
(``GitHub-Fehler 401: {"message": "Bad credentials", ...}``). Der Anwender
musste den HTTP-Status selbst deuten — die eigentliche Ursache war ein beim
Kopieren abgeschnittener Token. Jetzt soll die Meldung sagen, WAS zu tun ist.
"""

import urllib.error

import pytest

from app.services import github_service


class _Antwort:
    """Minimaler Ersatz für die Fehlerantwort von urlopen."""

    def __init__(self, inhalt: bytes):
        self._inhalt = inhalt

    def read(self) -> bytes:
        return self._inhalt


def _http_fehler(status: int, koerper: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/x/y/issues",
        code=status,
        msg="Fehler",
        hdrs=None,
        fp=_Antwort(koerper.encode("utf-8")),
    )


@pytest.fixture
def konfiguriert(monkeypatch):
    """Token/Repo setzen, damit erstelle_issue bis zum HTTP-Aufruf kommt."""
    monkeypatch.setattr(github_service.settings, "github_token", "github_pat_test")
    monkeypatch.setattr(github_service.settings, "github_repo", "besitzer/repo")


def _rufe_mit_status(monkeypatch, status: int, koerper: str = "{}") -> str:
    """Löst erstelle_issue aus und liefert den Text des GitHubFehler."""

    def wirf(*_args, **_kwargs):
        raise _http_fehler(status, koerper)

    monkeypatch.setattr(github_service.urllib.request, "urlopen", wirf)
    with pytest.raises(github_service.GitHubFehler) as exc:
        github_service.erstelle_issue("Titel", "Körper", ["bug"])
    return str(exc.value)


def test_401_nennt_token_und_env(konfiguriert, monkeypatch):
    """Der reale Fall: ungültiger/abgeschnittener Token."""
    text = _rufe_mit_status(
        monkeypatch, 401, '{"message": "Bad credentials", "status": "401"}'
    )
    assert "401" in text
    assert "WERKPLUS_GITHUB_TOKEN" in text
    assert "github_pat_" in text, "Der Hinweis soll das erwartete Token-Format nennen"
    # Kein rohes GitHub-JSON mehr in der Meldung
    assert "Bad credentials" not in text
    assert "documentation_url" not in text


def test_403_nennt_fehlende_issue_rechte(konfiguriert, monkeypatch):
    text = _rufe_mit_status(monkeypatch, 403, '{"message": "Forbidden"}')
    assert "403" in text
    assert "Issues" in text


def test_404_nennt_repo_einstellung(konfiguriert, monkeypatch):
    text = _rufe_mit_status(monkeypatch, 404, '{"message": "Not Found"}')
    assert "404" in text
    assert "WERKPLUS_GITHUB_REPO" in text


def test_422_wird_erklaert(konfiguriert, monkeypatch):
    text = _rufe_mit_status(monkeypatch, 422, '{"message": "Validation Failed"}')
    assert "422" in text


def test_unbekannter_status_behaelt_rohtext_gekuerzt(konfiguriert, monkeypatch):
    """Bei unerwarteten Codes bleibt der Rohtext — sonst fehlt die Diagnose."""
    text = _rufe_mit_status(monkeypatch, 500, '{"message": "Internal Server Error"}')
    assert "500" in text
    assert "Internal Server Error" in text


def test_rohtext_wird_gekuerzt(konfiguriert, monkeypatch):
    """Eine sehr lange Antwort darf den Meldedialog nicht überschwemmen."""
    text = _rufe_mit_status(monkeypatch, 599, '{"message": "' + "x" * 5000 + '"}')
    assert len(text) < 300


def test_token_taucht_nie_in_der_meldung_auf(konfiguriert, monkeypatch):
    """Sicherheitsnetz: der Token darf in keiner Fehlermeldung landen."""
    for status in (401, 403, 404, 422, 500):
        text = _rufe_mit_status(monkeypatch, status, '{"message": "x"}')
        assert "github_pat_test" not in text


def test_netzwerkfehler_bleibt_verstaendlich(konfiguriert, monkeypatch):
    def wirf(*_args, **_kwargs):
        raise urllib.error.URLError("Name oder Dienst nicht bekannt")

    monkeypatch.setattr(github_service.urllib.request, "urlopen", wirf)
    with pytest.raises(github_service.GitHubFehler) as exc:
        github_service.erstelle_issue("Titel", "Körper", ["bug"])
    assert "nicht erreichbar" in str(exc.value)
