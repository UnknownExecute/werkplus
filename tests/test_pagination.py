"""Tests für die serverseitige Pagination und die Leerzustände der langen Listen.

Die Seitengröße wird in den Tests per ``monkeypatch`` klein gesetzt, damit nicht
hunderte Datensätze angelegt werden müssen; ein Test prüft zusätzlich die echte
Standard-Seitengröße von 50.
"""

import re
from decimal import Decimal
from html import unescape

from sqlmodel import Session

from app.models import Adresse, AdressTyp, Artikel, Beleg, Belegart, Benutzer, Rolle
from app.pagination import Blaetterwerk, _fenster
from app.routers import adressen as adressen_router
from app.routers import artikel as artikel_router
from app.routers import belege as belege_router
from app.services.auth_service import hash_passwort


def _login(engine, client, login="tester", passwort="geheim"):
    """Legt einen Benutzer an und meldet den Client an."""
    with Session(engine) as session:
        session.add(
            Benutzer(
                login=login,
                name="Tester",
                passwort_hash=hash_passwort(passwort),
                rolle=Rolle.SACHBEARBEITER,
                aktiv=True,
            )
        )
        session.commit()
    r = client.post("/login", data={"login": login, "passwort": passwort}, follow_redirects=False)
    assert r.status_code == 302


# ------------------------------------------------------------------ Hilfsfunktionen


def _zeilen(html: str, praefix: str) -> list[str]:
    """IDs der verlinkten Datensätze einer Tabelle (= Anzahl der Zeilen)."""
    return re.findall(rf'href="{re.escape(praefix)}(\d+)"', html)


def _blaetter_links(html: str) -> list[str]:
    """Alle Ziel-URLs der Blätter-Navigation (HTML-Entities aufgelöst)."""
    return [unescape(u) for u in re.findall(r'class="page-link" href="([^"]+)"', html)]


def _adressen_anlegen(engine, anzahl: int, typ: AdressTyp = AdressTyp.KUNDE, praefix="Firma"):
    with Session(engine) as session:
        for i in range(anzahl):
            session.add(Adresse(firma=f"{praefix} {i:03d}", typ=typ, ort="Linz"))
        session.commit()


def _artikel_anlegen(engine, anzahl: int, praefix="A"):
    with Session(engine) as session:
        for i in range(anzahl):
            session.add(
                Artikel(
                    artikelnr=f"{praefix}-{i:03d}",
                    bezeichnung=f"Testartikel {i:03d}",
                    warengruppe="Test",
                    vk_preis=Decimal("10.00"),
                )
            )
        session.commit()


def _belege_anlegen(engine, anzahl: int, art: Belegart = Belegart.RECHNUNG) -> int:
    """Legt eine Adresse und ``anzahl`` Belege an; liefert die Adress-ID."""
    with Session(engine) as session:
        adresse = Adresse(firma="Sammelkunde GmbH", typ=AdressTyp.KUNDE, ort="Wels")
        session.add(adresse)
        session.commit()
        session.refresh(adresse)
        for i in range(anzahl):
            session.add(
                Beleg(
                    belegart=art,
                    belegnr=f"{art.value[:2].upper()}-{i:03d}",
                    adresse_id=adresse.id,
                    summe_brutto=Decimal("120.00"),
                )
            )
        session.commit()
        return adresse.id


# --------------------------------------------------------------- Blätterwerk (Unit)


def test_fenster_zeigt_hoechstens_fuenf_seiten():
    assert _fenster(1, 3) == (1, 2, 3)
    assert _fenster(1, 20) == (1, 2, 3, 4, 5)
    assert _fenster(10, 20) == (8, 9, 10, 11, 12)
    # am Ende wird das Fenster nach links geschoben, bleibt aber 5 breit
    assert _fenster(20, 20) == (16, 17, 18, 19, 20)


def test_blaetterwerk_kennzahlen():
    pag = Blaetterwerk(seite=2, seiten=3, gesamt=7, groesse=3, basis="/x?")
    assert (pag.von, pag.bis) == (4, 6)
    assert pag.hat_zurueck and pag.hat_weiter
    leer = Blaetterwerk(seite=1, seiten=1, gesamt=0, groesse=3, basis="/x?")
    assert (leer.von, leer.bis) == (0, 0)
    assert not leer.hat_zurueck and not leer.hat_weiter


# ------------------------------------------------------------------------- Belege


def test_belegliste_zeigt_nur_eine_seite(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7)

    r = client.get("/belege/rechnung")
    assert r.status_code == 200
    assert len(_zeilen(r.text, "/belege/detail/")) == 3
    assert "Seite 1 von 3" in r.text
    # Sortierung id desc → die jüngsten Belege zuerst
    assert "RE-006" in r.text
    assert "RE-003" not in r.text


def test_belegliste_seite_zwei_zeigt_andere_datensaetze(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7)

    seite1 = set(_zeilen(client.get("/belege/rechnung").text, "/belege/detail/"))
    antwort2 = client.get("/belege/rechnung", params={"seite": 2})
    seite2 = set(_zeilen(antwort2.text, "/belege/detail/"))

    assert len(seite2) == 3
    assert not seite1 & seite2
    assert "Seite 2 von 3" in antwort2.text


def test_belegliste_seite_ausserhalb_wird_geklemmt(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7)

    r = client.get("/belege/rechnung", params={"seite": 99})
    assert r.status_code == 200
    assert "Seite 3 von 3" in r.text
    assert len(_zeilen(r.text, "/belege/detail/")) == 1  # Restseite


def test_belegliste_letzte_seite_deaktiviert_weiter(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7)

    r = client.get("/belege/rechnung", params={"seite": 3})
    assert '<span class="page-link">Weiter</span>' in r.text
    assert all("seite=4" not in link for link in _blaetter_links(r.text))

    erste = client.get("/belege/rechnung")
    assert '<span class="page-link">Zurück</span>' in erste.text


def test_belegliste_ohne_zweite_seite_keine_navigation(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 2)

    r = client.get("/belege/rechnung")
    assert "Seite 1 von" not in r.text
    assert _blaetter_links(r.text) == []


def test_belegliste_blaettern_erhaelt_filter(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7)

    r = client.get("/belege/rechnung", params={"q": "RE-0", "status_filter": "entwurf"})
    links = _blaetter_links(r.text)
    assert links, "Blätter-Navigation fehlt"
    assert any("seite=2" in link for link in links)
    for link in links:
        assert "q=RE-0" in link
        assert "status_filter=entwurf" in link


def test_belegliste_htmx_partial_blaettert(engine, client, monkeypatch):
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7)

    r = client.get("/belege/rechnung/suche", params={"q": "RE-0", "seite": 2})
    assert r.status_code == 200
    assert len(_zeilen(r.text, "/belege/detail/")) == 3
    assert "Seite 2 von 3" in r.text
    # Im Partial blättert HTMX weiter (Ziel = das per HTMX getauschte tbody)
    assert 'hx-get="/belege/rechnung/suche?q=RE-0' in r.text
    assert 'hx-target="#beleg-tabelle"' in r.text


def test_lieferschein_liste_blaettert_ohne_htmx(engine, client, monkeypatch):
    """Die Lieferscheinliste ist ein GET-Formular — dort sollen normale Links blättern."""
    monkeypatch.setattr(belege_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _belege_anlegen(engine, 7, art=Belegart.LIEFERSCHEIN)

    r = client.get("/belege/lieferschein")
    links = _blaetter_links(r.text)
    assert any("/belege/lieferschein?" in link and "seite=2" in link for link in links)
    assert "hx-get" not in r.text


def test_belegliste_leerzustand_ohne_daten(engine, client):
    _login(engine, client)

    r = client.get("/belege/rechnung")
    assert "Noch keine Belege dieser Art vorhanden." in r.text


def test_belegliste_leerzustand_mit_filter(engine, client):
    _login(engine, client)
    _belege_anlegen(engine, 2)

    r = client.get("/belege/rechnung", params={"q": "gibtsnichtXYZ"})
    assert "Keine Belege gefunden" in r.text
    assert "Noch keine Belege dieser Art vorhanden." not in r.text

    # gleicher Leerzustand über das HTMX-Partial
    partial = client.get("/belege/rechnung/suche", params={"q": "gibtsnichtXYZ"})
    assert "Keine Belege gefunden" in partial.text


# ------------------------------------------------------------------------ Artikel


def test_artikelliste_blaettert(engine, client, monkeypatch):
    monkeypatch.setattr(artikel_router, "SEITENGROESSE", 4)
    _login(engine, client)
    _artikel_anlegen(engine, 9)

    r1 = client.get("/artikel")
    assert len(_zeilen(r1.text, "/artikel/")) == 4
    assert "Seite 1 von 3" in r1.text

    r2 = client.get("/artikel", params={"seite": 2})
    assert not set(_zeilen(r1.text, "/artikel/")) & set(_zeilen(r2.text, "/artikel/"))
    assert "Seite 2 von 3" in r2.text


def test_artikelliste_standard_seitengroesse_ist_50(engine, client):
    _login(engine, client)
    _artikel_anlegen(engine, 55)

    r = client.get("/artikel")
    assert artikel_router.SEITENGROESSE == 50
    assert len(_zeilen(r.text, "/artikel/")) == 50
    assert "Seite 1 von 2" in r.text

    letzte = client.get("/artikel", params={"seite": 2})
    assert len(_zeilen(letzte.text, "/artikel/")) == 5


def test_artikelliste_blaettern_erhaelt_suchbegriff(engine, client, monkeypatch):
    monkeypatch.setattr(artikel_router, "SEITENGROESSE", 4)
    _login(engine, client)
    _artikel_anlegen(engine, 9)
    _artikel_anlegen(engine, 3, praefix="Z")

    r = client.get("/artikel/suche", params={"q": "A-0"})
    links = _blaetter_links(r.text)
    assert links
    for link in links:
        assert "q=A-0" in link
    assert 'hx-target="#artikel-tabelle"' in r.text
    # Der Filter greift wirklich: 9 Treffer → 3 Seiten (nicht 12 Datensätze)
    assert "Seite 1 von 3" in r.text


def test_artikelliste_leerzustaende(engine, client):
    _login(engine, client)

    r = client.get("/artikel")
    assert "Noch keine Artikel vorhanden." in r.text

    _artikel_anlegen(engine, 2)
    treffer_leer = client.get("/artikel", params={"q": "gibtsnichtXYZ"})
    assert "Keine Artikel gefunden" in treffer_leer.text
    assert "Noch keine Artikel vorhanden." not in treffer_leer.text


# ----------------------------------------------------------------------- Adressen


def test_adressenliste_blaettert(engine, client, monkeypatch):
    monkeypatch.setattr(adressen_router, "SEITENGROESSE", 4)
    _login(engine, client)
    _adressen_anlegen(engine, 9)

    r1 = client.get("/adressen")
    assert len(_zeilen(r1.text, "/adressen/")) == 4
    assert "Seite 1 von 3" in r1.text

    r2 = client.get("/adressen", params={"seite": 2})
    assert len(_zeilen(r2.text, "/adressen/")) == 4
    assert not set(_zeilen(r1.text, "/adressen/")) & set(_zeilen(r2.text, "/adressen/"))

    r3 = client.get("/adressen", params={"seite": 3})
    assert len(_zeilen(r3.text, "/adressen/")) == 1
    assert '<span class="page-link">Weiter</span>' in r3.text


def test_adressenliste_blaettern_erhaelt_typ_filter(engine, client, monkeypatch):
    monkeypatch.setattr(adressen_router, "SEITENGROESSE", 3)
    _login(engine, client)
    _adressen_anlegen(engine, 7, typ=AdressTyp.LIEFERANT, praefix="Lieferant")
    _adressen_anlegen(engine, 5, typ=AdressTyp.KUNDE, praefix="Kunde")

    r = client.get("/adressen/suche", params={"typ": "lieferant"})
    assert "Seite 1 von 3" in r.text  # nur die 7 Lieferanten, nicht alle 12
    links = _blaetter_links(r.text)
    assert links
    for link in links:
        assert "typ=lieferant" in link
    assert any("seite=2" in link for link in links)
    assert 'hx-target="#adressen-tabelle"' in r.text


def test_adressenliste_leerzustaende(engine, client):
    _login(engine, client)

    r = client.get("/adressen")
    assert "Noch keine Adressen vorhanden." in r.text

    _adressen_anlegen(engine, 2)
    treffer_leer = client.get("/adressen/suche", params={"q": "gibtsnichtXYZ"})
    assert "Keine Adressen gefunden" in treffer_leer.text
    assert "Noch keine Adressen vorhanden." not in treffer_leer.text

    typ_leer = client.get("/adressen", params={"typ": "lieferant"})
    assert "Keine Adressen gefunden" in typ_leer.text
