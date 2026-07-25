"""Tests für die Preis-/Kalkulations-Ausblendung je nach Rollenrecht.

Ein Rollenprofil mit ``darf_kalkulation_sehen=True`` sieht EK-Preise, Kalkulation
und Marge; ein Profil mit ``False`` sieht sie nicht (Kalkulationsfelder werden im
Artikelformular als Hidden-Inputs mitgesendet, damit das Speichern funktioniert).
"""

from decimal import Decimal

from sqlmodel import Session

from app.models import Adresse, AdressTyp, Artikel, Rollenprofil, Zugriff
from app.models.benutzer import Benutzer
from app.services.auth_service import hash_passwort


def _profil_und_login(engine, client, *, darf_kalkulation: bool, login: str) -> None:
    """Legt ein Rollenprofil samt Benutzer an und meldet den Client an."""
    with Session(engine) as session:
        profil = Rollenprofil(
            name=f"rolle_{login}",
            beschreibung="Testrolle",
            darf_kalkulation_sehen=darf_kalkulation,
            recht_artikel=Zugriff.BEARBEITEN,
            recht_verkauf=Zugriff.BEARBEITEN,
        )
        session.add(profil)
        session.commit()
        session.add(
            Benutzer(
                login=login,
                name=login.title(),
                passwort_hash=hash_passwort("geheim"),
                rolle=profil.name,
                aktiv=True,
            )
        )
        session.commit()
    r = client.post(
        "/login", data={"login": login, "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


def _angebot_mit_position(engine, client) -> int:
    """Legt einen Kunden + Artikel an, erstellt ein Angebot mit einer Position."""
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Beispiel GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="M-1",
            bezeichnung="Testartikel",
            einheit="Stk",
            ek_preis=Decimal("60.00"),
            vk_preis=Decimal("100.00"),
            ust_satz=20,
        )
        session.add(adresse)
        session.add(artikel)
        session.commit()
        adresse_id, artikel_id = adresse.id, artikel.id

    r = client.post(
        "/belege/angebot/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    assert r.status_code == 302
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])

    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    assert r.status_code == 200
    return beleg_id


# --- Artikelformular -------------------------------------------------------


def test_artikel_formular_zeigt_kalkulation_mit_recht(engine, client):
    _profil_und_login(engine, client, darf_kalkulation=True, login="kalkchef")
    r = client.get("/artikel/neu")
    assert r.status_code == 200
    assert "Listenpreis" in r.text
    assert "EK-Rabatt" in r.text
    assert "Aufschlag %" in r.text


def test_artikel_formular_verbirgt_kalkulation_ohne_recht(engine, client):
    _profil_und_login(engine, client, darf_kalkulation=False, login="monteur")
    r = client.get("/artikel/neu")
    assert r.status_code == 200
    # Sichtbare Kalkulations-Beschriftungen fehlen
    assert "EK-Rabatt" not in r.text
    assert "Aufschlag %" not in r.text
    assert "Listenpreis" not in r.text
    # Pflichtwerte aber als Hidden-Inputs vorhanden → Speichern bleibt möglich
    assert 'type="hidden"' in r.text
    assert 'name="listenpreis_netto"' in r.text
    assert 'name="aufschlag_prozent"' in r.text
    assert 'name="vk_preis"' in r.text
    # Nicht-Kalkulationsfelder bleiben sichtbar
    assert 'name="artikelnr"' in r.text
    assert 'name="bezeichnung"' in r.text


# --- Beleg-Editor (Marge) --------------------------------------------------


def test_beleg_editor_zeigt_marge_mit_recht(engine, client):
    _profil_und_login(engine, client, darf_kalkulation=True, login="kalkchef")
    beleg_id = _angebot_mit_position(engine, client)
    r = client.get(f"/belege/detail/{beleg_id}")
    assert r.status_code == 200
    assert "Deckungsbeitrag" in r.text
    assert "Marge" in r.text
    assert "EK-Summe" in r.text


def test_beleg_editor_verbirgt_marge_ohne_recht(engine, client):
    _profil_und_login(engine, client, darf_kalkulation=False, login="monteur")
    beleg_id = _angebot_mit_position(engine, client)
    r = client.get(f"/belege/detail/{beleg_id}")
    assert r.status_code == 200
    assert "Deckungsbeitrag" not in r.text
    assert "EK-Summe" not in r.text
    # Normale Summen bleiben sichtbar
    assert "Gesamt brutto" in r.text
