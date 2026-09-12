"""Tests für die objektbezogene Bereichsdurchsetzung (Verkauf vs. Einkauf).

Die Router-Wache des Belege-Routers prüft nur "verkauf ODER einkauf". Ergänzend
prüft ``_lade_beleg`` den Bereich der konkreten Belegart — sonst könnte ein
Nutzer mit ausschließlich Verkaufsrecht Einkaufsbelege lesen/ändern.

Zusätzlich abgedeckt: globale Suche und Dashboard dürfen die Bereichsrechte
nicht umgehen.
"""

from decimal import Decimal

from sqlmodel import Session

from app.models import Adresse, AdressTyp, Artikel, Belegart, Rollenprofil, Zugriff
from app.models.benutzer import Benutzer
from app.services.auth_service import hash_passwort


def _login_mit_rechten(engine, client, *, login: str, **rechte) -> None:
    """Legt ein Rollenprofil mit den angegebenen Bereichsrechten an und meldet an."""
    with Session(engine) as session:
        profil = Rollenprofil(name=f"rolle_{login}", beschreibung="Testrolle", **rechte)
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


def _stammdaten(engine) -> tuple[int, int]:
    """Kunde/Lieferant (BEIDE) + Artikel anlegen; liefert (adresse_id, artikel_id)."""
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.BEIDE, firma="Zwitter GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="A-1",
            bezeichnung="Testartikel",
            einheit="Stk",
            ek_preis=Decimal("50.00"),
            vk_preis=Decimal("100.00"),
            ust_satz=20,
        )
        session.add(adresse)
        session.add(artikel)
        session.commit()
        return adresse.id, artikel.id


def _beleg(engine, art: Belegart, adresse_id: int) -> int:
    """Erzeugt direkt in der DB einen Beleg der Art (ohne HTTP, um Rechte zu umgehen)."""
    from app.services import beleg_service

    with Session(engine) as session:
        adresse = session.get(Adresse, adresse_id)
        beleg = beleg_service.erstelle_beleg(session, art, adresse)
        session.commit()
        return beleg.id


# --- S4: IDOR Verkauf/Einkauf ------------------------------------------------


def test_verkaufsrecht_darf_einkaufsbeleg_nicht_sehen(engine, client):
    adresse_id, _ = _stammdaten(engine)
    bestellung_id = _beleg(engine, Belegart.BESTELLUNG, adresse_id)
    _login_mit_rechten(engine, client, login="nurverkauf", recht_verkauf=Zugriff.BEARBEITEN)

    r = client.get(f"/belege/detail/{bestellung_id}")
    assert r.status_code == 403


def test_verkaufsrecht_darf_einkaufsbeleg_nicht_aendern(engine, client):
    adresse_id, artikel_id = _stammdaten(engine)
    bestellung_id = _beleg(engine, Belegart.BESTELLUNG, adresse_id)
    _login_mit_rechten(engine, client, login="nurverkauf", recht_verkauf=Zugriff.BEARBEITEN)

    r = client.post(
        f"/belege/detail/{bestellung_id}/positionen", data={"artikel_id": artikel_id}
    )
    assert r.status_code == 403


def test_einkaufsrecht_darf_verkaufsbeleg_nicht_sehen(engine, client):
    adresse_id, _ = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _login_mit_rechten(engine, client, login="nureinkauf", recht_einkauf=Zugriff.BEARBEITEN)

    assert client.get(f"/belege/detail/{angebot_id}").status_code == 403


def test_eigener_bereich_bleibt_erlaubt(engine, client):
    """Gegenprobe: der zuständige Bereich funktioniert unverändert."""
    adresse_id, _ = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _login_mit_rechten(engine, client, login="verkaeufer", recht_verkauf=Zugriff.BEARBEITEN)

    assert client.get(f"/belege/detail/{angebot_id}").status_code == 200


def test_nur_ansehen_darf_nicht_schreiben(engine, client):
    """recht_verkauf=ANSEHEN erlaubt GET, aber kein POST."""
    adresse_id, artikel_id = _stammdaten(engine)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _login_mit_rechten(engine, client, login="leser", recht_verkauf=Zugriff.ANSEHEN)

    assert client.get(f"/belege/detail/{angebot_id}").status_code == 200
    r = client.post(
        f"/belege/detail/{angebot_id}/positionen", data={"artikel_id": artikel_id}
    )
    assert r.status_code == 403


# --- S5: Suche und Dashboard respektieren die Bereichsrechte -----------------


def test_suche_zeigt_keine_fremdbereich_belege(engine, client):
    adresse_id, _ = _stammdaten(engine)
    bestellung_id = _beleg(engine, Belegart.BESTELLUNG, adresse_id)
    angebot_id = _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _login_mit_rechten(engine, client, login="nurverkauf", recht_verkauf=Zugriff.BEARBEITEN)

    r = client.get("/suche?q=Zwitter")
    assert r.status_code == 200
    # Belegnummern sind je Belegart gleich (260001) — daher über die Links prüfen
    assert f"/belege/detail/{angebot_id}" in r.text  # Angebot (Verkauf) erlaubt
    assert f"/belege/detail/{bestellung_id}" not in r.text  # Bestellung (Einkauf) gefiltert


def test_suche_zeigt_keine_adressen_ohne_recht(engine, client):
    _stammdaten(engine)
    _login_mit_rechten(engine, client, login="nurverkauf", recht_verkauf=Zugriff.BEARBEITEN)

    r = client.get("/suche?q=Zwitter")
    assert r.status_code == 200
    # Adressen-Bereich ist KEIN → Firmenname darf nicht als Adresstreffer erscheinen
    assert "/adressen/" not in r.text


def test_suche_zeigt_adressen_mit_recht(engine, client):
    _stammdaten(engine)
    _login_mit_rechten(
        engine,
        client,
        login="stammdatenpfleger",
        recht_adressen=Zugriff.ANSEHEN,
    )
    r = client.get("/suche?q=Zwitter")
    assert r.status_code == 200
    assert "/adressen/" in r.text


def test_dashboard_ohne_verkaufsrecht_zeigt_keine_verkaufszahlen(engine, client):
    adresse_id, _ = _stammdaten(engine)
    _beleg(engine, Belegart.ANGEBOT, adresse_id)
    _login_mit_rechten(engine, client, login="nureinkauf", recht_einkauf=Zugriff.BEARBEITEN)

    r = client.get("/")
    assert r.status_code == 200
    assert "AN-" not in r.text  # kein Verkaufsbeleg in "letzte Belege"
