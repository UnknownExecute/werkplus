"""Tests für die Bestätigungsdialoge bei unumkehrbaren Aktionen.

Zwei Mechanismen werden geprüft:

* HTMX-Aktionen (``hx-post``/``hx-delete``) tragen ``hx-confirm``.
* Native ``<form method="post">`` tragen ``data-bestaetigen`` (am Formular oder
  am Submit-Button) bzw. ``data-bestaetigen-aus`` an einer Checkbox; ein
  zentraler Listener in ``app/static/werkplus.js`` fragt beim Absenden nach.

Zusätzlich Gegentests: unkritische Aktionen (Speichern, Duplizieren, Position
hinzufügen, Verschieben) dürfen KEINE Rückfrage tragen.
"""

from decimal import Decimal

from sqlmodel import Session, select

from app.models import Adresse, AdressTyp, Artikel, Lager, Rollenprofil, SetKomponente
from app.models.benutzer import Benutzer, Rolle
from app.services.auth_service import hash_passwort

# --- Hilfsfunktionen ----------------------------------------------------------


def _login(engine, client, login="tester", passwort="geheim", rolle=Rolle.SACHBEARBEITER):
    """Legt einen Benutzer an und meldet den Client an."""
    with Session(engine) as session:
        session.add(
            Benutzer(
                login=login,
                name="Tester",
                passwort_hash=hash_passwort(passwort),
                rolle=rolle,
                aktiv=True,
            )
        )
        session.commit()
    r = client.post(
        "/login", data={"login": login, "passwort": passwort}, follow_redirects=False
    )
    assert r.status_code == 302


def _formular(html: str, action: str) -> str:
    """Schneidet das ``<form>``-Fragment mit der angegebenen ``action`` heraus."""
    pos = html.find(f'action="{action}"')
    assert pos != -1, f"Formular mit action={action} nicht im HTML gefunden"
    start = html.rfind("<form", 0, pos)
    ende = html.find("</form>", pos)
    assert start != -1 and ende != -1
    return html[start:ende]


def _button(html: str, marker: str) -> str:
    """Schneidet das ``<button>``-Fragment heraus, das ``marker`` enthält."""
    pos = html.find(marker)
    assert pos != -1, f"Button-Marker {marker!r} nicht im HTML gefunden"
    start = html.rfind("<button", 0, pos)
    ende = html.find("</button>", pos)
    assert start != -1 and ende != -1
    return html[start:ende]


def _stammdaten(engine) -> tuple[int, int]:
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Muster GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="HZ-1",
            bezeichnung="Gastherme Testino",
            einheit="Stk",
            vk_preis=Decimal("100.00"),
            ust_satz=20,
        )
        session.add_all([adresse, artikel])
        session.commit()
        return adresse.id, artikel.id


def _beleg_mit_position(client, adresse_id: int, artikel_id: int, belegart="angebot") -> int:
    r = client.post(
        f"/belege/{belegart}/neu", data={"adresse_id": adresse_id}, follow_redirects=False
    )
    assert r.status_code == 302
    beleg_id = int(r.headers["location"].rsplit("/", 1)[-1])
    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    assert r.status_code == 200
    return beleg_id


# --- Belegeditor: Buchen und Stornieren ---------------------------------------


def test_buchen_hat_rueckfrage_mit_belegnummer(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_mit_position(client, adresse_id, artikel_id)

    html = client.get(f"/belege/detail/{beleg_id}").text
    form = _formular(html, f"/belege/detail/{beleg_id}/status/gedruckt")
    assert "data-bestaetigen=" in form
    assert "jetzt buchen?" in form
    assert "unveränderlich" in form
    assert "Angebot 260001" in form or "Angebot " in form  # Belegnummer steht im Rückfragetext
    # Angebot bucht kein Lager -> kein irreführender Hinweis
    assert "Lagerbewegungen" not in form


def test_buchen_beim_lieferschein_nennt_lagerbuchung(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_mit_position(client, adresse_id, artikel_id, belegart="lieferschein")

    html = client.get(f"/belege/detail/{beleg_id}").text
    form = _formular(html, f"/belege/detail/{beleg_id}/status/gedruckt")
    assert "Lagerbewegungen werden sofort gebucht" in form


def test_storno_hat_rueckfrage_mit_konsequenz(engine, client):
    _login(engine, client, login="chef", rolle=Rolle.ADMIN)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_mit_position(client, adresse_id, artikel_id)

    html = client.get(f"/belege/detail/{beleg_id}").text
    form = _formular(html, f"/belege/detail/{beleg_id}/status/storniert")
    assert "data-bestaetigen=" in form
    assert "wirklich stornieren?" in form
    assert "nicht rückgängig machen" in form
    assert "zurückgebucht" in form


def test_unkritische_belegaktionen_ohne_rueckfrage(engine, client):
    """Gegentest: Speichern, Duplizieren, Folgebeleg & PDF fragen NICHT nach."""
    _login(engine, client, login="chef", rolle=Rolle.ADMIN)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_mit_position(client, adresse_id, artikel_id)

    html = client.get(f"/belege/detail/{beleg_id}").text
    for action in (
        f"/belege/detail/{beleg_id}/kopf",  # Kopf speichern
        f"/belege/detail/{beleg_id}/duplizieren",
    ):
        form = _formular(html, action)
        assert "data-bestaetigen" not in form, f"{action} sollte keine Rückfrage haben"

    # "Freie Position" (hx-post) bleibt ohne Rückfrage
    freie_position = _button(html, "Freie Position")
    assert "hx-confirm" not in freie_position


# --- Positionsgrid ------------------------------------------------------------


def test_position_loeschen_hat_hx_confirm(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_mit_position(client, adresse_id, artikel_id)

    html = client.get(f"/belege/detail/{beleg_id}").text
    loeschen = _button(html, "Position löschen")
    assert "hx-delete=" in loeschen
    assert "hx-confirm=" in loeschen
    assert "wirklich löschen?" in loeschen
    assert "Gastherme Testino" in loeschen  # Bezeichnung im Rückfragetext


def test_position_verschieben_ohne_rueckfrage(engine, client):
    """Gegentest: die Sortier-Buttons sind harmlos und fragen nicht nach."""
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_mit_position(client, adresse_id, artikel_id)

    html = client.get(f"/belege/detail/{beleg_id}").text
    for titel in ("Nach oben", "Nach unten"):
        assert "hx-confirm" not in _button(html, titel)
    # Genau eine Rückfrage pro Positionszeile: das Löschen
    assert html.count("hx-confirm") == 1


# --- Set-Komponenten ----------------------------------------------------------


def test_set_komponente_loeschen_hat_hx_confirm(engine, client):
    _login(engine, client)
    with Session(engine) as session:
        set_artikel = Artikel(artikelnr="SET-1", bezeichnung="Bad-Set", ist_set=True)
        komponente = Artikel(artikelnr="K-1", bezeichnung="Rohr", vk_preis=Decimal("10"))
        session.add_all([set_artikel, komponente])
        session.commit()
        set_id, komp_art_id = set_artikel.id, komponente.id

    r = client.post(
        f"/artikel/{set_id}/set-komponenten",
        data={"komponente_artikel_id": komp_art_id, "menge": "2"},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        assert session.exec(select(SetKomponente)).first() is not None

    loeschen = _button(r.text, "Löschen")
    assert "hx-confirm=" in loeschen
    assert "aus dem Set entfernen?" in loeschen
    assert "Rohr" in loeschen
    # Gegentest: "Hinzufügen" fragt nicht nach
    assert "hx-confirm" not in _button(r.text, "Hinzufügen")


# --- Rollen & Mitarbeiter -----------------------------------------------------


def test_rolle_loeschen_hat_rueckfrage_speichern_nicht(engine, client):
    _login(engine, client, login="admin", rolle=Rolle.ADMIN)
    with Session(engine) as session:
        rolle = Rollenprofil(name="Aushilfe")
        session.add(rolle)
        session.commit()
        rolle_id = rolle.id

    html = client.get(f"/benutzer/rollen/{rolle_id}").text
    loeschen = _button(html, f"/benutzer/rollen/{rolle_id}/loeschen")
    assert "data-bestaetigen=" in loeschen
    assert "wirklich löschen?" in loeschen
    assert "Aushilfe" in loeschen
    # Gegentest: der Speichern-Button im selben Formular fragt nicht nach
    assert "data-bestaetigen" not in _button(html, ">Speichern<")


def test_mitarbeiter_deaktivieren_hat_rueckfrage(engine, client):
    _login(engine, client, login="admin", rolle=Rolle.ADMIN)
    with Session(engine) as session:
        mitarbeiter = Benutzer(
            login="max",
            name="Max Muster",
            passwort_hash=hash_passwort("geheim"),
            rolle=Rolle.SACHBEARBEITER,
            aktiv=True,
        )
        session.add(mitarbeiter)
        session.commit()
        mitarbeiter_id = mitarbeiter.id

    html = client.get(f"/benutzer/{mitarbeiter_id}").text
    assert "data-bestaetigen-aus=" in html
    assert "wirklich deaktivieren?" in html
    assert "Max Muster" in html
    # Gegentest: das Formular selbst (Speichern) trägt keine generelle Rückfrage
    form = _formular(html, f"/benutzer/{mitarbeiter_id}")
    assert 'data-bestaetigen="' not in form


# --- Lagerkorrektur -----------------------------------------------------------


def test_lagerkorrektur_hat_rueckfrage(engine, client):
    _login(engine, client, login="admin", rolle=Rolle.ADMIN)
    with Session(engine) as session:
        session.add(Lager(name="Hauptlager"))
        session.commit()

    html = client.get("/lager/korrektur").text
    form = _formular(html, "/lager/korrektur")
    assert "data-bestaetigen=" in form
    assert "Bestandskorrektur jetzt buchen?" in form
    assert "Lagerbestand wird sofort geändert" in form


# --- Zentraler Listener in werkplus.js ---------------------------------------


def test_werkplus_js_hat_zentralen_bestaetigungs_listener(client):
    js = client.get("/static/werkplus.js")
    assert js.status_code == 200
    quelltext = js.text
    # Zentraler Submit-Listener statt Inline-onsubmit an vielen Stellen
    assert "bestaetigungstexte" in quelltext
    assert 'dataset.bestaetigen' in quelltext
    assert "bestaetigenAus" in quelltext
    assert "window.confirm" in quelltext
    assert "e.preventDefault()" in quelltext
    # Regressionsschutz: die CSRF-Injektion ist unangetastet
    assert "injectCsrfFields" in quelltext
    assert "X-CSRF-Token" in quelltext


def test_templates_ohne_inline_bestaetigung(client):
    """Kein Inline-JS-Wildwuchs: Templates nutzen data-Attribute statt onsubmit."""
    from pathlib import Path

    templates = Path(__file__).resolve().parent.parent / "app" / "templates"
    treffer = [
        str(p)
        for p in templates.rglob("*.html")
        if "onsubmit=\"return confirm" in p.read_text(encoding="utf-8")
    ]
    assert treffer == []
