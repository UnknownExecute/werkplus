"""Tests für die Beleg-UI (Liste, Anlage, Editor, Positionen, Status, Folgebeleg)."""

from decimal import Decimal

from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Artikel,
    Beleg,
    Belegart,
    BelegPosition,
    BelegStatus,
    Textbaustein,
    TextbausteinKategorie,
)
from app.models.benutzer import Benutzer, Rolle
from app.services.auth_service import hash_passwort


def _login(engine, client, login="tester", passwort="geheim", rolle=Rolle.SACHBEARBEITER):
    """Legt einen Benutzer an und meldet den Client an."""
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login,
            name="Tester",
            passwort_hash=hash_passwort(passwort),
            rolle=rolle,
            aktiv=True,
        )
        session.add(benutzer)
        session.commit()
    r = client.post(
        "/login", data={"login": login, "passwort": passwort}, follow_redirects=False
    )
    assert r.status_code == 302


def _stammdaten(engine) -> tuple[int, int]:
    """Minimal-Fixture: ein Kunde und ein Artikel (VK 100, 20 % USt)."""
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Muster GmbH", ort="Linz")
        artikel = Artikel(
            artikelnr="HZ-1",
            bezeichnung="Gastherme Testino",
            einheit="Stk",
            vk_preis=Decimal("100.00"),
            ust_satz=20,
        )
        session.add(adresse)
        session.add(artikel)
        session.commit()
        return adresse.id, artikel.id


def _beleg_anlegen(client, adresse_id: int, belegart: str = "angebot") -> int:
    """Legt über die UI einen Beleg an und liefert dessen ID."""
    r = client.post(
        f"/belege/{belegart}/neu",
        data={"adresse_id": adresse_id},
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("/belege/detail/")
    return int(location.rsplit("/", 1)[-1])


def _position_hinzufuegen(client, beleg_id: int, artikel_id: int) -> str:
    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    assert r.status_code == 200
    return r.text


def test_liste_200_fuer_alle_belegarten(engine, client):
    _login(engine, client)
    for belegart in Belegart:
        r = client.get(f"/belege/{belegart}")
        assert r.status_code == 200


def test_liste_404_bei_unbekannter_belegart(engine, client):
    _login(engine, client)
    r = client.get("/belege/quatsch")
    assert r.status_code == 404


def test_beleg_anlegen_editor_mit_belegnr(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)

    r = client.get(f"/belege/detail/{beleg_id}")
    assert r.status_code == 200
    assert "AN-" in r.text  # Nummernkreis-Präfix Angebot
    assert "Muster GmbH" in r.text

    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert beleg.belegart == Belegart.ANGEBOT
        assert beleg.status == BelegStatus.ENTWURF
        assert beleg.belegnr.startswith("AN-")


def test_artikelsuche_partial_liefert_treffer(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)

    r = client.get(f"/belege/detail/{beleg_id}/artikelsuche", params={"q": "Gasth"})
    assert r.status_code == 200
    assert "HZ-1" in r.text
    assert "Gastherme Testino" in r.text

    r = client.get(f"/belege/detail/{beleg_id}/artikelsuche", params={"q": "gibtsnicht"})
    assert "HZ-1" not in r.text


def test_position_hinzufuegen_zeigt_summen(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)

    html = _position_hinzufuegen(client, beleg_id, artikel_id)
    assert "Gastherme Testino" in html
    assert "100,00" in html  # Netto 20 %
    assert "20,00" in html  # USt
    assert "120,00" in html  # Brutto

    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert beleg.summe_brutto == Decimal("120.00")


def test_set_artikel_fuegt_komponenten_als_positionen_ein(engine, client):
    """Ein Set-Artikel fügt beim Einsetzen seine Komponenten als Positionen ein."""
    from app.models import SetKomponente

    _login(engine, client)
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Set Kunde")
        set_artikel = Artikel(artikelnr="SET-1", bezeichnung="Heizungspaket", ist_set=True)
        k1 = Artikel(artikelnr="K-1", bezeichnung="Therme", vk_preis=Decimal("1000"), ust_satz=20)
        k2 = Artikel(artikelnr="K-2", bezeichnung="Speicher", vk_preis=Decimal("500"), ust_satz=20)
        session.add_all([adresse, set_artikel, k1, k2])
        session.commit()
        session.add(SetKomponente(set_artikel_id=set_artikel.id, komponente_artikel_id=k1.id,
                                  menge=Decimal("1"), sort=0))
        session.add(SetKomponente(set_artikel_id=set_artikel.id, komponente_artikel_id=k2.id,
                                  menge=Decimal("2"), sort=1))
        session.commit()
        adresse_id, set_id = adresse.id, set_artikel.id

    beleg_id = _beleg_anlegen(client, adresse_id)
    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": set_id})
    assert r.status_code == 200

    with Session(engine) as session:
        positionen = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).all()
        assert len(positionen) == 2  # beide Komponenten, nicht der Set-Artikel selbst
        bezeichnungen = {p.bezeichnung for p in positionen}
        assert bezeichnungen == {"Therme", "Speicher"}
        speicher = next(p for p in positionen if p.bezeichnung == "Speicher")
        assert speicher.menge == Decimal("2")


def test_position_aendern_menge_neue_summe(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)

    with Session(engine) as session:
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).one()

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position.id}",
        data={
            "bezeichnung": position.bezeichnung,
            "menge": "3",
            "einheit": "Stk",
            "einzelpreis": "100",
            "rabatt_prozent": "0",
            "ust_satz": "20",
        },
    )
    assert r.status_code == 200
    assert "300,00" in r.text  # neues Netto
    assert "360,00" in r.text  # neues Brutto

    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert beleg.summe_netto == Decimal("300.00")
        assert beleg.summe_brutto == Decimal("360.00")


def _erste_position_id(engine, beleg_id: int) -> int:
    with Session(engine) as session:
        return session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).one().id


def test_position_kalkulation_berechnet_vk(engine, client):
    """Kalkulationsfeld geändert (HX-Trigger lp-…) → EK und VK werden neu berechnet."""
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}",
        data={
            "bezeichnung": "Gerät", "menge": "1", "einheit": "Stk",
            "listenpreis": "100", "rabatt1_prozent": "10", "rabatt2_prozent": "5",
            "aufschlag_prozent": "40", "einzelpreis": "0", "rabatt_prozent": "0", "ust_satz": "20",
        },
        headers={"HX-Trigger": f"lp-{pos_id}"},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        pos = session.get(BelegPosition, pos_id)
        assert pos.ek_preis == Decimal("85.50")  # 100 − 10 % − 5 %
        assert pos.einzelpreis == Decimal("119.70")  # 85,50 + 40 %


def test_position_vk_ueberschreiben_rechnet_aufschlag_zurueck(engine, client):
    """VK-Feld direkt geändert (HX-Trigger vk-…) → VK übernommen, Aufschlag rückgerechnet."""
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}",
        data={
            "bezeichnung": "Gerät", "menge": "1", "einheit": "Stk",
            "listenpreis": "100", "rabatt1_prozent": "0", "rabatt2_prozent": "0",
            "aufschlag_prozent": "40", "einzelpreis": "150",
            "rabatt_prozent": "0", "ust_satz": "20",
        },
        headers={"HX-Trigger": f"vk-{pos_id}"},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        pos = session.get(BelegPosition, pos_id)
        assert pos.einzelpreis == Decimal("150.00")  # manuell übernommen
        assert pos.aufschlag_prozent == Decimal("50.00")  # (150/100 − 1) × 100


def test_position_mehrzeiliger_langtext(engine, client):
    """Mehrzeiliger Langtext wird gespeichert; VK bleibt bei Nicht-Kalk-Änderung erhalten."""
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}",
        data={
            "bezeichnung": "Gerät", "langtext": "Zeile 1\nZeile 2\nZeile 3",
            "menge": "2", "einheit": "Stk", "listenpreis": "0",
            "rabatt1_prozent": "0", "rabatt2_prozent": "0", "aufschlag_prozent": "0",
            "einzelpreis": "100", "rabatt_prozent": "0", "ust_satz": "20",
        },
        headers={"HX-Trigger": f"menge-{pos_id}"},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        pos = session.get(BelegPosition, pos_id)
        assert pos.langtext == "Zeile 1\nZeile 2\nZeile 3"
        assert pos.einzelpreis == Decimal("100.00")  # kein Kalk-Trigger → VK unverändert


def test_position_loeschen(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)

    with Session(engine) as session:
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).one()

    r = client.delete(f"/belege/detail/{beleg_id}/positionen/{position.id}")
    assert r.status_code == 200
    assert "Keine Positionen" in r.text

    with Session(engine) as session:
        assert (
            session.exec(
                select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
            ).first()
            is None
        )
        assert session.get(Beleg, beleg_id).summe_brutto == Decimal("0.00")


def test_status_gedruckt_sperrt_editor_und_positionen(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)

    r = client.post(f"/belege/detail/{beleg_id}/status/gedruckt")
    assert r.status_code == 200
    assert "Beleg ist gebucht und unveränderlich" in r.text
    assert "disabled" in r.text

    with Session(engine) as session:
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)
        ).one()

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{position.id}",
        data={"menge": "5", "ust_satz": "20"},
    )
    assert r.status_code == 409


def test_folgebeleg_aus_gedrucktem_angebot(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    r = client.post(f"/belege/detail/{beleg_id}/folgebeleg", follow_redirects=False)
    assert r.status_code == 302
    neu_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    assert neu_id != beleg_id

    with Session(engine) as session:
        neu = session.get(Beleg, neu_id)
        assert neu.belegart == Belegart.AUFTRAG
        assert neu.belegnr.startswith("AB-")
        assert neu.vorgaenger_beleg_id == beleg_id
        positionen = list(
            session.exec(select(BelegPosition).where(BelegPosition.beleg_id == neu_id)).all()
        )
        assert len(positionen) == 1
        assert positionen[0].bezeichnung == "Gastherme Testino"
        assert positionen[0].menge == Decimal("1")
        # Vorgänger ist jetzt erledigt
        assert session.get(Beleg, beleg_id).status == BelegStatus.ERLEDIGT

    r = client.get(f"/belege/detail/{neu_id}")
    assert r.status_code == 200
    assert "Auftrag" in r.text


def _menge_setzen(client, beleg_id: int, pos_id: int, menge: str) -> None:
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}",
        data={
            "bezeichnung": "Gastherme Testino", "menge": menge, "einheit": "Stk",
            "einzelpreis": "100", "rabatt_prozent": "0", "ust_satz": "20",
        },
    )
    assert r.status_code == 200


def test_folgebeleg_dialog_zeigt_restmengen(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)
    _menge_setzen(client, beleg_id, pos_id, "4")
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    r = client.get(f"/belege/detail/{beleg_id}/folgebeleg-dialog")
    assert r.status_code == 200
    assert f'name="menge_{pos_id}"' in r.text
    assert "Gastherme Testino" in r.text
    assert "Restmenge" in r.text


def test_folgebeleg_teilmenge_erzeugt_folgebeleg_mit_gewaehlter_menge(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)
    _menge_setzen(client, beleg_id, pos_id, "4")
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    r = client.post(
        f"/belege/detail/{beleg_id}/folgebeleg",
        data={f"menge_{pos_id}": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    neu_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])

    with Session(engine) as session:
        neu_pos = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == neu_id)
        ).one()
        assert neu_pos.menge == Decimal("1")
        # Vorgänger bleibt offen (noch Restmenge)
        assert session.get(Beleg, beleg_id).status == BelegStatus.GEDRUCKT


def test_folgebeleg_ohne_mengen_uebernimmt_voll(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)
    _menge_setzen(client, beleg_id, pos_id, "4")
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    r = client.post(f"/belege/detail/{beleg_id}/folgebeleg", follow_redirects=False)
    assert r.status_code == 302
    neu_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])

    with Session(engine) as session:
        neu_pos = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == neu_id)
        ).one()
        assert neu_pos.menge == Decimal("4")  # volle Übernahme
        assert session.get(Beleg, beleg_id).status == BelegStatus.ERLEDIGT


def test_bezahlt_offen_toggle_setzt_und_loescht_bezahlt_am(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id, belegart="rechnung")
    _position_hinzufuegen(client, beleg_id, artikel_id)
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    # Editor zeigt den "Als bezahlt markieren"-Button (offen)
    r = client.get(f"/belege/detail/{beleg_id}")
    assert "Als bezahlt markieren" in r.text

    r = client.post(f"/belege/detail/{beleg_id}/bezahlt", follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).bezahlt_am is not None

    # Jetzt bezahlt -> Editor zeigt den "Als offen markieren"-Button
    r = client.get(f"/belege/detail/{beleg_id}")
    assert "Als offen markieren" in r.text

    r = client.post(f"/belege/detail/{beleg_id}/offen", follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).bezahlt_am is None


def test_bezahlt_bei_nicht_zahlungsart_404(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id, belegart="angebot")
    r = client.post(f"/belege/detail/{beleg_id}/bezahlt")
    assert r.status_code == 404


def test_position_verschieben_aendert_reihenfolge_im_grid(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)

    with Session(engine) as session:
        positionen = list(
            session.exec(
                select(BelegPosition)
                .where(BelegPosition.beleg_id == beleg_id)
                .order_by(BelegPosition.pos_nr)
            ).all()
        )
    erste_id, zweite_id = positionen[0].id, positionen[1].id
    assert (positionen[0].pos_nr, positionen[1].pos_nr) == (1, 2)

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{erste_id}/verschieben",
        params={"richtung": "runter"},
    )
    assert r.status_code == 200

    with Session(engine) as session:
        erste = session.get(BelegPosition, erste_id)
        zweite = session.get(BelegPosition, zweite_id)
        assert erste.pos_nr == 2
        assert zweite.pos_nr == 1


def test_position_verschieben_gesperrt_bei_gedruckt(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    pos_id = _erste_position_id(engine, beleg_id)
    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}/verschieben",
        params={"richtung": "hoch"},
    )
    assert r.status_code == 409


def test_storno_als_nicht_admin_403(engine, client):
    _login(engine, client, rolle=Rolle.SACHBEARBEITER)
    adresse_id, _ = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)

    r = client.post(f"/belege/detail/{beleg_id}/status/storniert")
    assert r.status_code == 403

    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).status == BelegStatus.ENTWURF


def test_position_detail_panel_laedt_mit_feldern(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)

    r = client.get(f"/belege/detail/{beleg_id}/positionen/{pos_id}/detail")
    assert r.status_code == 200
    assert 'id="position-detail"' in r.text
    assert 'name="bezeichnung"' in r.text
    assert 'name="langtext"' in r.text
    assert f'id="lp-{pos_id}"' in r.text
    assert f'id="vk-{pos_id}"' in r.text
    assert 'name="listenpreis"' in r.text
    assert "Gastherme Testino" in r.text


def test_kalkulation_ueber_panel_bleibt_gruen(engine, client):
    """Kalkulation über das Detail-Panel (HX-Trigger lp-…) rechnet weiterhin korrekt.

    Die Antwort liefert das Grid (Summen) plus das Out-of-band Detail-Panel.
    """
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}",
        data={
            "bezeichnung": "Gerät", "menge": "1", "einheit": "Stk",
            "listenpreis": "100", "rabatt1_prozent": "10", "rabatt2_prozent": "5",
            "aufschlag_prozent": "40", "einzelpreis": "0", "rabatt_prozent": "0", "ust_satz": "20",
        },
        headers={"HX-Trigger": f"lp-{pos_id}"},
    )
    assert r.status_code == 200
    assert 'id="positionsbereich"' in r.text
    assert 'id="position-detail"' in r.text
    assert 'hx-swap-oob="true"' in r.text
    with Session(engine) as session:
        pos = session.get(BelegPosition, pos_id)
        assert pos.ek_preis == Decimal("85.50")
        assert pos.einzelpreis == Decimal("119.70")


def test_beleg_duplizieren_erzeugt_entwurf(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")

    r = client.post(f"/belege/detail/{beleg_id}/duplizieren", follow_redirects=False)
    assert r.status_code == 302
    neu_id = int(r.headers["location"].rsplit("/", 1)[-1])
    assert neu_id != beleg_id

    with Session(engine) as session:
        neu = session.get(Beleg, neu_id)
        assert neu.status == BelegStatus.ENTWURF
        assert neu.vorgaenger_beleg_id is None
        positionen = list(
            session.exec(select(BelegPosition).where(BelegPosition.beleg_id == neu_id)).all()
        )
        assert len(positionen) == 1
        assert positionen[0].bezeichnung == "Gastherme Testino"


def test_textbaustein_in_position_einfuegen(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    pos_id = _erste_position_id(engine, beleg_id)

    with Session(engine) as session:
        baustein = Textbaustein(
            kuerzel="GAR",
            titel="Garantie",
            text="2 Jahre Garantie.",
            kategorie=TextbausteinKategorie.POSITION,
        )
        session.add(baustein)
        session.commit()
        baustein_id = baustein.id

    # Dialog listet den aktiven Baustein
    r = client.get(f"/belege/detail/{beleg_id}/positionen/{pos_id}/textbausteine")
    assert r.status_code == 200
    assert "Garantie" in r.text

    r = client.post(
        f"/belege/detail/{beleg_id}/positionen/{pos_id}/textbaustein-einfuegen",
        data={"baustein_id": baustein_id},
    )
    assert r.status_code == 200
    with Session(engine) as session:
        pos = session.get(BelegPosition, pos_id)
        assert "2 Jahre Garantie." in pos.langtext


def test_textbaustein_in_kopftext_einfuegen(engine, client):
    _login(engine, client)
    adresse_id, _ = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)

    with Session(engine) as session:
        baustein = Textbaustein(
            kuerzel="GRUSS",
            titel="Begrüßung",
            text="Sehr geehrte Damen und Herren,",
            kategorie=TextbausteinKategorie.KOPF,
        )
        session.add(baustein)
        session.commit()
        baustein_id = baustein.id

    r = client.get(f"/belege/detail/{beleg_id}/textbausteine", params={"ziel": "kopf"})
    assert r.status_code == 200
    assert "Begrüßung" in r.text

    r = client.post(
        f"/belege/detail/{beleg_id}/textbaustein-kopf-einfuegen",
        data={"baustein_id": baustein_id, "ziel": "kopf"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        assert "Sehr geehrte Damen und Herren," in session.get(Beleg, beleg_id).kopftext


def _gedruckter_beleg(engine, client, adresse_id, artikel_id, belegart):
    """Legt über die UI einen Beleg mit einer Position an und bucht ihn (gedruckt)."""
    beleg_id = _beleg_anlegen(client, adresse_id, belegart=belegart)
    _position_hinzufuegen(client, beleg_id, artikel_id)
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")
    return beleg_id


def test_abschlag_dialog_zeigt_eingaben(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    auftrag_id = _gedruckter_beleg(engine, client, adresse_id, artikel_id, "auftrag")

    r = client.get(f"/belege/detail/{auftrag_id}/abschlag-dialog")
    assert r.status_code == 200
    assert 'name="prozent"' in r.text
    assert 'name="betrag"' in r.text
    assert "Auftrags-Nettosumme" in r.text


def test_abschlagsrechnung_post_erzeugt_abschlag(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    auftrag_id = _gedruckter_beleg(engine, client, adresse_id, artikel_id, "auftrag")

    r = client.post(
        f"/belege/detail/{auftrag_id}/abschlagsrechnung",
        data={"prozent": "20"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    neu_id = int(r.headers["location"].rsplit("/", 1)[-1])
    assert neu_id != auftrag_id

    with Session(engine) as session:
        neu = session.get(Beleg, neu_id)
        assert neu.belegart == Belegart.RECHNUNG
        assert neu.ist_abschlag is True
        assert neu.auftrag_id == auftrag_id
        # Auftrag netto 100 → 20 % = 20 netto
        assert neu.summe_netto == Decimal("20.00")


def test_schlussrechnung_post(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    auftrag_id = _gedruckter_beleg(engine, client, adresse_id, artikel_id, "auftrag")
    client.post(
        f"/belege/detail/{auftrag_id}/abschlagsrechnung", data={"prozent": "20"}
    )

    r = client.post(
        f"/belege/detail/{auftrag_id}/schlussrechnung", follow_redirects=False
    )
    assert r.status_code == 302
    neu_id = int(r.headers["location"].rsplit("/", 1)[-1])

    with Session(engine) as session:
        neu = session.get(Beleg, neu_id)
        assert neu.belegart == Belegart.RECHNUNG
        assert neu.auftrag_id == auftrag_id
        # 100 − 20 (Abschlag) = 80 netto
        assert neu.summe_netto == Decimal("80.00")
        positionen = list(
            session.exec(select(BelegPosition).where(BelegPosition.beleg_id == neu_id)).all()
        )
        assert len(positionen) == 2  # Auftragsposition + Abzug


def test_sammelrechnung_aus_zwei_lieferscheinen(engine, client):
    _login(engine, client)
    adresse_id, artikel_id = _stammdaten(engine)
    ls1 = _gedruckter_beleg(engine, client, adresse_id, artikel_id, "lieferschein")
    ls2 = _gedruckter_beleg(engine, client, adresse_id, artikel_id, "lieferschein")

    r = client.post(
        "/belege/sammelrechnung",
        data={"lieferschein_ids": [ls1, ls2]},
        follow_redirects=False,
    )
    assert r.status_code == 302
    neu_id = int(r.headers["location"].rsplit("/", 1)[-1])

    with Session(engine) as session:
        neu = session.get(Beleg, neu_id)
        assert neu.belegart == Belegart.RECHNUNG
        positionen = list(
            session.exec(select(BelegPosition).where(BelegPosition.beleg_id == neu_id)).all()
        )
        assert len(positionen) == 2
        assert session.get(Beleg, ls1).status == BelegStatus.ERLEDIGT
        assert session.get(Beleg, ls2).status == BelegStatus.ERLEDIGT


def test_sammelrechnung_ohne_auswahl_redirect_mit_fehler(engine, client):
    _login(engine, client)
    r = client.post("/belege/sammelrechnung", data={}, follow_redirects=False)
    assert r.status_code == 302
    assert "/belege/lieferschein" in r.headers["location"]
    assert "fehler=" in r.headers["location"]


def test_storno_als_admin_200(engine, client):
    _login(engine, client, login="chef", rolle=Rolle.ADMIN)
    adresse_id, _ = _stammdaten(engine)
    beleg_id = _beleg_anlegen(client, adresse_id)

    r = client.post(f"/belege/detail/{beleg_id}/status/storniert")
    assert r.status_code == 200
    assert "Storniert" in r.text

    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).status == BelegStatus.STORNIERT
