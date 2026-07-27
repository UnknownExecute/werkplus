"""Anlage direkt beim Kunden anlegen (Bestandsanlagen/Altgeräte).

Anlass: Anwenderwunsch „Anlage bei Kundenadresse nachträglich anlegen".
Die Funktion selbst gab es bereits (`/anlagen/neu` mit Feld `sn_text` für
Altgeräte ohne Lieferschein) — sie war vom Kunden aus nur nicht erreichbar:
Der Anlagen-Tab der Kunden-Detailseite war eine reine Leseliste.
"""

import re
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Anlage,
    Artikel,
    Benutzer,
    Rolle,
    Rollenprofil,
    Zugriff,
)
from app.services.auth_service import hash_passwort


def _kunde_und_artikel(engine) -> tuple[int, int]:
    with Session(engine) as session:
        kunde = Adresse(typ=AdressTyp.KUNDE, firma="Altbau Verwaltung GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="HZ-ALT",
            bezeichnung="Gastherme Altbestand",
            einheit="Stk",
            seriennummernpflichtig=True,
            ek_preis=Decimal("0"),
            vk_preis=Decimal("0"),
            ust_satz=20,
        )
        session.add(kunde)
        session.add(artikel)
        session.commit()
        return kunde.id, artikel.id


def _anmelden(engine, client, *, admin: bool = True, recht=Zugriff.BEARBEITEN) -> None:
    rolle = "rolle_test"
    with Session(engine) as session:
        session.add(
            Rollenprofil(
                name=rolle, ist_admin=admin, recht_adressen=recht, recht_anlagen=recht
            )
        )
        session.add(
            Benutzer(
                login="tester",
                name="Tester",
                passwort_hash=hash_passwort("geheim"),
                rolle=rolle if not admin else Rolle.ADMIN,
                aktiv=True,
            )
        )
        if admin:
            session.add(
                Rollenprofil(name=Rolle.ADMIN, ist_admin=True, ist_standard=True)
            )
        session.commit()
    r = client.post(
        "/login", data={"login": "tester", "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


def test_kundenseite_verlinkt_anlage_anlegen(engine, client):
    """Der Anlagen-Tab muss einen Weg zur Neuanlage bieten — mit Kundenbezug."""
    kunde_id, _ = _kunde_und_artikel(engine)
    _anmelden(engine, client)
    html = client.get(f"/adressen/{kunde_id}").text
    assert f"/anlagen/neu?adresse_id={kunde_id}" in html, (
        "Auf der Kundenseite fehlt der Weg zur Anlagen-Neuanlage"
    )
    assert "Anlage anlegen" in html


def test_leerzustand_nennt_altgeraete(engine, client):
    """Ohne Anlagen soll der Hinweis erklären, dass auch Altgeräte gehen."""
    kunde_id, _ = _kunde_und_artikel(engine)
    _anmelden(engine, client)
    html = client.get(f"/adressen/{kunde_id}").text
    assert "Noch keine Anlagen erfasst" in html
    assert "Altgeräte" in html


def _option_ist_vorgewaehlt(html: str, wert: int) -> bool:
    """Trägt genau die <option> mit diesem value ein ``selected``?

    Bewusst genau geprüft: ein schlichtes ``"selected" in html`` wäre auch dann
    erfüllt, wenn irgendein anderes Feld vorausgewählt ist.
    """
    treffer = re.findall(r"<option\b[^>]*>", html)
    for tag in treffer:
        if f'value="{wert}"' in tag:
            return "selected" in tag
    return False


def test_formular_waehlt_kunden_vor(engine, client):
    """Der Kunde aus dem Link muss im Formular vorausgewählt sein."""
    kunde_id, _ = _kunde_und_artikel(engine)
    _anmelden(engine, client)
    html = client.get(f"/anlagen/neu?adresse_id={kunde_id}").text
    assert _option_ist_vorgewaehlt(html, kunde_id), "Der Kunde wurde nicht vorausgewählt"


def test_formular_ohne_parameter_waehlt_nichts_vor(engine, client):
    """Gegenprobe: ohne adresse_id darf kein Kunde vorausgewählt sein."""
    kunde_id, _ = _kunde_und_artikel(engine)
    _anmelden(engine, client)
    html = client.get("/anlagen/neu").text
    assert not _option_ist_vorgewaehlt(html, kunde_id)


def test_bestandsanlage_mit_freier_seriennummer_anlegen(engine, client):
    """Der eigentliche Wunsch: Altgerät ohne Lieferschein erfassen."""
    kunde_id, artikel_id = _kunde_und_artikel(engine)
    _anmelden(engine, client)
    r = client.post(
        "/anlagen/neu",
        data={
            "adresse_id": str(kunde_id),
            "artikel_id": str(artikel_id),
            "sn_text": "ALT-2011-4711",
            "standort": "Heizraum Keller",
            "inbetriebnahme": "2011-09-01",
            "naechste_wartung": "2027-03-01",
            "notizen": "Bestandsgerät, vor WerkPlus installiert",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302, f"Anlegen scheiterte: {r.status_code}"
    with Session(engine) as session:
        anlage = session.exec(
            select(Anlage).where(Anlage.adresse_id == kunde_id)
        ).one()
        assert anlage.standort == "Heizraum Keller"
        assert anlage.inbetriebnahme == date(2011, 9, 1)


def test_seriennummer_steht_in_der_kundenliste(engine, client):
    """Nach dem Anlegen muss die Seriennummer beim Kunden sichtbar sein."""
    kunde_id, artikel_id = _kunde_und_artikel(engine)
    _anmelden(engine, client)
    client.post(
        "/anlagen/neu",
        data={
            "adresse_id": str(kunde_id),
            "artikel_id": str(artikel_id),
            "sn_text": "ALT-2011-4711",
            "standort": "Heizraum Keller",
        },
        follow_redirects=False,
    )
    html = client.get(f"/adressen/{kunde_id}").text
    assert "ALT-2011-4711" in html, "Die Seriennummer fehlt in der Anlagenliste"
    assert "Gastherme Altbestand" in html


def test_ohne_bearbeitungsrecht_kein_knopf(engine, client):
    """Nur-Lese-Recht auf Anlagen: der Anlegen-Knopf bleibt verborgen."""
    kunde_id, _ = _kunde_und_artikel(engine)
    _anmelden(engine, client, admin=False, recht=Zugriff.ANSEHEN)
    html = client.get(f"/adressen/{kunde_id}").text
    assert "Anlage anlegen" not in html
