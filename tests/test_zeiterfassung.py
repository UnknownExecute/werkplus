"""Tests für die Arbeitszeit-/Regieerfassung (Service, UI, Rechte, Verrechnung).

Schwerpunkte:

* **Erfassen/Ändern/Löschen** über die echten Routen (``/zeiterfassung``).
* **Summen exakt in ``Decimal``** — kein float, auch nicht auf Umwegen.
* **Verrechnung**: je Zeile eine Position (Menge = Stunden, Einzelpreis = Satz),
  Zeilen werden als ``verrechnet`` markiert, ein zweiter Klick legt NICHTS mehr an
  (Schutz gegen doppelte Verrechnung), gedruckte Belege werden fachlich abgewiesen.
* **Rechte**: ``recht_zeiterfassung`` KEIN/ANSEHEN/BEARBEITEN.
* **IDOR**: eine fremde Zeitzeile ist für einen Nicht-Admin über ihre ID weder
  lesbar noch änderbar noch löschbar; ein Admin darf beides.
* **Stundensatz** wird in der Mitarbeiterverwaltung gepflegt (er war bisher
  nirgends bearbeitbar und damit immer 0).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models import (
    Adresse,
    AdressTyp,
    Beleg,
    Belegart,
    BelegPosition,
    BelegStatus,
    Benutzer,
    Rolle,
    Rollenprofil,
    Zeiterfassung,
    Zugriff,
)
from app.services import beleg_service, zeiterfassung_service
from app.services.auth_service import hash_passwort
from app.services.fehler import WerkplusFehler, ZeitBereitsVerrechnet

OK = 200
REDIRECT = 302
FORBIDDEN = 403
NOT_FOUND = 404


# --- Hilfen -------------------------------------------------------------------


def _benutzer_anlegen(
    engine, *, login: str, rolle: str, stundensatz: str = "70.00"
) -> int:
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login,
            name=login.title(),
            passwort_hash=hash_passwort("geheim"),
            rolle=rolle,
            aktiv=True,
            stundensatz=Decimal(stundensatz),
        )
        session.add(benutzer)
        session.commit()
        session.refresh(benutzer)
        return benutzer.id


def _anmelden(client, login: str) -> None:
    r = client.post(
        "/login", data={"login": login, "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == REDIRECT, f"Login {login!r} fehlgeschlagen: {r.status_code}"


def _login_mit_rechten(engine, client, *, login: str, anmelden: bool = True, **rechte) -> int:
    """Rollenprofil + Benutzer anlegen und (optional) anmelden; liefert die Benutzer-ID."""
    rollenname = f"rolle_{login}"
    with Session(engine) as session:
        vorhanden = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == rollenname)
        ).first()
        if vorhanden is None:
            session.add(Rollenprofil(name=rollenname, beschreibung="Testrolle", **rechte))
            session.commit()
    benutzer_id = _benutzer_anlegen(engine, login=login, rolle=rollenname)
    if anmelden:
        _anmelden(client, login)
    return benutzer_id


def _zeile_anlegen(  # noqa: PLR0913
    engine,
    *,
    benutzer_id: int,
    stunden: str = "2",
    stundensatz: str = "70.00",
    taetigkeit: str = "Montage",
    beleg_id: int | None = None,
    tage_zurueck: int = 0,
    verrechnet: bool = False,
) -> int:
    with Session(engine) as session:
        zeile = Zeiterfassung(
            benutzer_id=benutzer_id,
            beleg_id=beleg_id,
            datum=date.today() - timedelta(days=tage_zurueck),
            stunden=Decimal(stunden),
            stundensatz=Decimal(stundensatz),
            taetigkeit=taetigkeit,
            verrechnet=verrechnet,
        )
        session.add(zeile)
        session.commit()
        session.refresh(zeile)
        return zeile.id


def _auftrag_anlegen(engine, *, gedruckt: bool = False) -> int:
    with Session(engine) as session:
        adresse = Adresse(typ=AdressTyp.KUNDE, firma="Regie Kunde GmbH", ort="Wien")
        session.add(adresse)
        session.flush()
        auftrag = beleg_service.erstelle_beleg(session, Belegart.AUFTRAG, adresse)
        if gedruckt:
            beleg_service.setze_status(session, auftrag, BelegStatus.GEDRUCKT)
        session.commit()
        session.refresh(auftrag)
        return auftrag.id


def _positionen(engine, beleg_id: int) -> list[BelegPosition]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(BelegPosition)
                .where(BelegPosition.beleg_id == beleg_id)
                .order_by(BelegPosition.pos_nr)  # type: ignore[arg-type]
            ).all()
        )


ALLE_RECHTE = {
    "recht_zeiterfassung": Zugriff.BEARBEITEN,
    "recht_verkauf": Zugriff.BEARBEITEN,
}


# --- 1) Erfassen / Ändern / Löschen über die UI -------------------------------


def test_ohne_anmeldung_redirect(client):
    r = client.get("/zeiterfassung", follow_redirects=False)
    assert r.status_code == REDIRECT
    assert r.headers["location"] == "/login"


def test_zeit_erfassen(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="mo1", **ALLE_RECHTE)
    auftrag_id = _auftrag_anlegen(engine)

    r = client.post(
        "/zeiterfassung/neu",
        data={
            "datum": date.today().isoformat(),
            "stunden": "3,5",
            "stundensatz": "78,50",
            "taetigkeit": "Therme montiert",
            "beleg_id": str(auftrag_id),
        },
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT

    with Session(engine) as session:
        zeile = session.exec(select(Zeiterfassung)).one()
        assert zeile.benutzer_id == benutzer_id
        assert zeile.beleg_id == auftrag_id
        assert zeile.stunden == Decimal("3.5")
        assert zeile.stundensatz == Decimal("78.50")
        assert zeile.taetigkeit == "Therme montiert"
        assert zeile.verrechnet is False

    assert "Therme montiert" in client.get("/zeiterfassung").text


def test_erfassungsformular_belegt_stundensatz_aus_dem_mitarbeiterstamm(engine, client):
    _login_mit_rechten(engine, client, login="mo_satz", **ALLE_RECHTE)
    with Session(engine) as session:
        benutzer = session.exec(select(Benutzer).where(Benutzer.login == "mo_satz")).one()
        benutzer.stundensatz = Decimal("88.00")
        session.add(benutzer)
        session.commit()
    _anmelden(client, "mo_satz")

    text = client.get("/zeiterfassung/neu").text
    assert 'name="stundensatz"' in text
    assert "88,00" in text


def test_null_stunden_wird_fachlich_abgewiesen(engine, client):
    _login_mit_rechten(engine, client, login="mo_null", **ALLE_RECHTE)
    r = client.post(
        "/zeiterfassung/neu",
        data={"datum": date.today().isoformat(), "stunden": "0", "stundensatz": "70"},
        follow_redirects=False,
    )
    assert r.status_code == OK  # Formular mit Meldung, kein 500 und kein Datensatz
    assert "größer als 0" in r.text
    with Session(engine) as session:
        assert session.exec(select(Zeiterfassung)).first() is None


def test_zeit_aendern(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="mo2", **ALLE_RECHTE)
    zeile_id = _zeile_anlegen(engine, benutzer_id=benutzer_id)

    r = client.post(
        f"/zeiterfassung/{zeile_id}",
        data={
            "datum": date.today().isoformat(),
            "stunden": "6,75",
            "stundensatz": "80",
            "taetigkeit": "Nachbesserung",
            "beleg_id": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        zeile = session.get(Zeiterfassung, zeile_id)
        assert zeile.stunden == Decimal("6.75")
        assert zeile.stundensatz == Decimal("80")
        assert zeile.taetigkeit == "Nachbesserung"


def test_zeit_loeschen(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="mo3", **ALLE_RECHTE)
    zeile_id = _zeile_anlegen(engine, benutzer_id=benutzer_id)

    r = client.post(f"/zeiterfassung/{zeile_id}/loeschen", follow_redirects=False)
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        assert session.get(Zeiterfassung, zeile_id) is None


def test_loeschen_verlangt_bestaetigung_im_markup(engine, client):
    """Unumkehrbare Aktion ⇒ Rückfrage über ``data-bestaetigen`` (werkplus.js)."""
    benutzer_id = _login_mit_rechten(engine, client, login="mo_conf", **ALLE_RECHTE)
    _zeile_anlegen(engine, benutzer_id=benutzer_id)

    text = client.get("/zeiterfassung").text
    assert "data-bestaetigen=" in text
    assert "wirklich löschen" in text


def test_verrechnete_zeile_ist_gesperrt(engine, client):
    """Eine verrechnete Zeile darf nicht mehr geändert oder gelöscht werden."""
    benutzer_id = _login_mit_rechten(engine, client, login="mo_ver", **ALLE_RECHTE)
    zeile_id = _zeile_anlegen(engine, benutzer_id=benutzer_id, verrechnet=True)

    r = client.post(
        f"/zeiterfassung/{zeile_id}",
        data={"datum": date.today().isoformat(), "stunden": "9", "stundensatz": "1"},
        follow_redirects=False,
    )
    assert r.status_code == OK
    assert "verrechnet" in r.text

    assert client.post(
        f"/zeiterfassung/{zeile_id}/loeschen", follow_redirects=False
    ).status_code == REDIRECT
    with Session(engine) as session:
        zeile = session.get(Zeiterfassung, zeile_id)
        assert zeile is not None, "Verrechnete Zeile darf nicht gelöscht werden"
        assert zeile.stunden == Decimal("2"), "Verrechnete Zeile darf nicht geändert werden"


def test_uebersicht_paginiert(engine, client):
    """Mehr Zeilen als eine Seite ⇒ Blätter-Navigation, Seite 2 zeigt den Rest."""
    benutzer_id = _login_mit_rechten(engine, client, login="mo_pag", **ALLE_RECHTE)
    anzahl = zeiterfassung_service_seitengroesse() + 5
    for i in range(anzahl):
        _zeile_anlegen(engine, benutzer_id=benutzer_id, taetigkeit=f"Job {i:03d}", tage_zurueck=i)

    erste = client.get("/zeiterfassung")
    assert erste.status_code == OK
    assert "seite=2" in erste.text
    zweite = client.get("/zeiterfassung?seite=2")
    assert zweite.status_code == OK
    assert f"Job {anzahl - 1:03d}" in zweite.text


def zeiterfassung_service_seitengroesse() -> int:
    from app.routers.zeiterfassung import SEITENGROESSE

    return SEITENGROESSE


# --- 2) Summen: ausschließlich Decimal ---------------------------------------


def test_summe_ist_exakt_decimal(session):
    """Drittelstunden summieren sich exakt — mit float wäre 0.1*3 != 0.3."""
    zeilen = [
        zeiterfassung_service.erfasse_zeit(
            session, benutzer_id=1, stunden=Decimal("0.1"), stundensatz=Decimal("33.33")
        )
        for _ in range(3)
    ]
    summe = zeiterfassung_service.summiere(zeilen)

    assert isinstance(summe.stunden, Decimal)
    assert isinstance(summe.betrag, Decimal)
    assert summe.stunden == Decimal("0.3")
    assert str(summe.stunden) == "0.3"
    # je Zeile runde(0.1 * 33.33) = 3.33 ⇒ 9.99 (nicht 9.999)
    assert summe.betrag == Decimal("9.99")
    assert summe.anzahl == 3
    assert summe.offene_anzahl == 3


def test_summe_je_filter_nur_eigener_mitarbeiter(session):
    zeiterfassung_service.erfasse_zeit(
        session, benutzer_id=1, stunden=Decimal("2"), stundensatz=Decimal("50")
    )
    zeiterfassung_service.erfasse_zeit(
        session, benutzer_id=2, stunden=Decimal("3"), stundensatz=Decimal("50")
    )
    session.commit()

    assert zeiterfassung_service.summe_je_filter(session, benutzer_id=1).betrag == Decimal("100.00")
    assert zeiterfassung_service.summe_je_filter(session, benutzer_id=2).stunden == Decimal("3")
    assert zeiterfassung_service.summe_je_filter(session).stunden == Decimal("5")


def test_uebersicht_zeigt_stunden_und_betragssumme(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="mo_summe", **ALLE_RECHTE)
    _zeile_anlegen(engine, benutzer_id=benutzer_id, stunden="3", stundensatz="100.00")
    _zeile_anlegen(engine, benutzer_id=benutzer_id, stunden="1.5", stundensatz="100.00")

    text = client.get("/zeiterfassung").text
    assert "4,5 Std" in text
    assert "450,00" in text


# --- 3) Verrechnung: Positionen erzeugen, verrechnet markieren ----------------


def test_uebernahme_erzeugt_positionen_und_markiert_verrechnet(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="sb_ueb", **ALLE_RECHTE)
    auftrag_id = _auftrag_anlegen(engine)
    _zeile_anlegen(
        engine, benutzer_id=benutzer_id, beleg_id=auftrag_id,
        stunden="3.5", stundensatz="78.50", taetigkeit="Montage", tage_zurueck=1,
    )
    _zeile_anlegen(
        engine, benutzer_id=benutzer_id, beleg_id=auftrag_id,
        stunden="1.25", stundensatz="95.00", taetigkeit="Inbetriebnahme",
    )

    r = client.post(
        f"/belege/detail/{auftrag_id}/zeiten-uebernehmen", follow_redirects=False
    )
    assert r.status_code == REDIRECT

    positionen = _positionen(engine, auftrag_id)
    assert len(positionen) == 2, "je Zeitzeile genau eine Position"
    erste, zweite = positionen
    assert erste.menge == Decimal("3.5")
    assert erste.einzelpreis == Decimal("78.50")
    assert erste.einheit == "Std"
    assert erste.rabatt_prozent == Decimal("0")
    assert "Montage" in erste.bezeichnung
    assert "Sb_Ueb" in erste.bezeichnung  # Mitarbeitername
    assert zweite.menge == Decimal("1.25")
    assert zweite.einzelpreis == Decimal("95.00")

    with Session(engine) as session:
        zeilen = session.exec(select(Zeiterfassung)).all()
        assert all(z.verrechnet for z in zeilen)
        # Belegsumme = Summe der Zeitbeträge (3,5*78,50 + 1,25*95,00)
        beleg = session.get(Beleg, auftrag_id)
        assert beleg.summe_netto == Decimal("393.50")


def test_zweite_uebernahme_legt_nichts_mehr_an(engine, client):
    """Kernschutz gegen doppelte Verrechnung: der zweite Klick ist wirkungslos."""
    benutzer_id = _login_mit_rechten(engine, client, login="sb_dop", **ALLE_RECHTE)
    auftrag_id = _auftrag_anlegen(engine)
    _zeile_anlegen(engine, benutzer_id=benutzer_id, beleg_id=auftrag_id, stunden="2")

    assert client.post(
        f"/belege/detail/{auftrag_id}/zeiten-uebernehmen", follow_redirects=False
    ).status_code == REDIRECT
    assert len(_positionen(engine, auftrag_id)) == 1

    r = client.post(
        f"/belege/detail/{auftrag_id}/zeiten-uebernehmen", follow_redirects=False
    )
    assert r.status_code == REDIRECT
    assert len(_positionen(engine, auftrag_id)) == 1, "zweiter Klick hat erneut gebucht"

    # Die fachliche Meldung landet im Editor, nicht als 500
    folge = client.get(r.headers["location"])
    assert folge.status_code == OK
    assert "keine offenen" in folge.text.lower()


def test_uebernahme_bei_gedrucktem_beleg_wird_fachlich_abgewiesen(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="sb_gedr", **ALLE_RECHTE)
    auftrag_id = _auftrag_anlegen(engine, gedruckt=True)
    _zeile_anlegen(engine, benutzer_id=benutzer_id, beleg_id=auftrag_id, stunden="2")

    r = client.post(
        f"/belege/detail/{auftrag_id}/zeiten-uebernehmen", follow_redirects=False
    )
    assert r.status_code == REDIRECT, "gesperrter Beleg darf keinen 500 ergeben"
    folge = client.get(r.headers["location"])
    assert folge.status_code == OK
    assert "kann nicht geändert werden" in folge.text
    assert _positionen(engine, auftrag_id) == []
    with Session(engine) as session:
        assert session.exec(select(Zeiterfassung)).one().verrechnet is False


def test_uebernahme_ohne_zeiten_meldet_fachlich(session):
    adresse = Adresse(typ=AdressTyp.KUNDE, firma="Leer GmbH")
    session.add(adresse)
    session.flush()
    auftrag = beleg_service.erstelle_beleg(session, Belegart.AUFTRAG, adresse)
    with pytest.raises(WerkplusFehler):
        zeiterfassung_service.uebernehme_in_beleg(session, auftrag)


def test_service_sperrt_aenderung_verrechneter_zeile(session):
    zeile = zeiterfassung_service.erfasse_zeit(
        session, benutzer_id=1, stunden=Decimal("1"), stundensatz=Decimal("10")
    )
    zeile.verrechnet = True
    session.add(zeile)
    session.flush()
    with pytest.raises(ZeitBereitsVerrechnet):
        zeiterfassung_service.aendere_zeit(
            session, zeile, stunden=Decimal("2"), stundensatz=Decimal("10")
        )
    with pytest.raises(ZeitBereitsVerrechnet):
        zeiterfassung_service.loesche_zeit(session, zeile)


def test_auftragseditor_zeigt_zeiten_und_summe(engine, client):
    benutzer_id = _login_mit_rechten(engine, client, login="sb_edit", **ALLE_RECHTE)
    auftrag_id = _auftrag_anlegen(engine)
    _zeile_anlegen(
        engine, benutzer_id=benutzer_id, beleg_id=auftrag_id,
        stunden="4", stundensatz="90.00", taetigkeit="Regiearbeit",
    )

    text = client.get(f"/belege/detail/{auftrag_id}").text
    assert "Erfasste Arbeitszeiten" in text
    assert "Regiearbeit" in text
    assert "360,00" in text
    assert "Zeiten übernehmen" in text


def test_auftragseditor_ohne_zeiterfassungsrecht_zeigt_keine_zeiten(engine, client):
    """Die Zeiten-Karte enthält fremde Mitarbeiterdaten ⇒ ohne Recht unsichtbar."""
    fremder_id = _benutzer_anlegen(engine, login="fremd_edit", rolle="egal")
    auftrag_id = _auftrag_anlegen(engine)
    _zeile_anlegen(
        engine, benutzer_id=fremder_id, beleg_id=auftrag_id, taetigkeit="Geheime Regie"
    )
    _login_mit_rechten(
        engine, client, login="nurverkauf_edit",
        recht_verkauf=Zugriff.BEARBEITEN, recht_zeiterfassung=Zugriff.KEIN,
    )

    text = client.get(f"/belege/detail/{auftrag_id}").text
    assert "Geheime Regie" not in text
    assert "Erfasste Arbeitszeiten" not in text


def test_uebernahme_ohne_zeiterfassungsrecht_verboten(engine, client):
    fremder_id = _benutzer_anlegen(engine, login="fremd_ueb", rolle="egal")
    auftrag_id = _auftrag_anlegen(engine)
    _zeile_anlegen(engine, benutzer_id=fremder_id, beleg_id=auftrag_id)
    _login_mit_rechten(
        engine, client, login="nurverkauf_ueb",
        recht_verkauf=Zugriff.BEARBEITEN, recht_zeiterfassung=Zugriff.KEIN,
    )

    r = client.post(
        f"/belege/detail/{auftrag_id}/zeiten-uebernehmen", follow_redirects=False
    )
    assert r.status_code == FORBIDDEN
    assert _positionen(engine, auftrag_id) == []


# --- 4) Rechte: KEIN / ANSEHEN / BEARBEITEN ----------------------------------


def test_recht_kein_sperrt_alles(engine, client):
    _login_mit_rechten(
        engine, client, login="ohne_zeit",
        recht_zeiterfassung=Zugriff.KEIN, recht_adressen=Zugriff.ANSEHEN,
    )
    assert client.get("/zeiterfassung", follow_redirects=False).status_code == FORBIDDEN
    assert client.get("/zeiterfassung/neu", follow_redirects=False).status_code == FORBIDDEN
    assert client.post(
        "/zeiterfassung/neu", data={}, follow_redirects=False
    ).status_code == FORBIDDEN


def test_recht_ansehen_erlaubt_lesen_aber_kein_schreiben(engine, client):
    benutzer_id = _login_mit_rechten(
        engine, client, login="leser_zeit", recht_zeiterfassung=Zugriff.ANSEHEN
    )
    zeile_id = _zeile_anlegen(engine, benutzer_id=benutzer_id, taetigkeit="Nur lesen")

    assert client.get("/zeiterfassung").status_code == OK
    assert "Nur lesen" in client.get("/zeiterfassung").text
    assert client.get(f"/zeiterfassung/{zeile_id}").status_code == OK

    for pfad, daten in (
        ("/zeiterfassung/neu", {"stunden": "1", "stundensatz": "1"}),
        (f"/zeiterfassung/{zeile_id}", {"stunden": "1", "stundensatz": "1"}),
        (f"/zeiterfassung/{zeile_id}/loeschen", {}),
    ):
        r = client.post(pfad, data=daten, follow_redirects=False)
        assert r.status_code == FORBIDDEN, f"POST {pfad} müsste mit ansehen 403 sein"
    with Session(engine) as session:
        assert session.get(Zeiterfassung, zeile_id) is not None


def test_recht_bearbeiten_erlaubt_beides(engine, client):
    _login_mit_rechten(engine, client, login="schreiber_zeit", **ALLE_RECHTE)
    assert client.get("/zeiterfassung").status_code == OK
    r = client.post(
        "/zeiterfassung/neu",
        data={"datum": date.today().isoformat(), "stunden": "1", "stundensatz": "50"},
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        assert session.exec(select(Zeiterfassung)).one().stunden == Decimal("1")


def test_menuepunkt_nur_mit_recht(engine, client):
    _login_mit_rechten(engine, client, login="menue_ja", recht_zeiterfassung=Zugriff.ANSEHEN)
    assert "/zeiterfassung" in client.get("/").text
    client.post("/logout")

    _login_mit_rechten(
        engine, client, login="menue_nein",
        recht_zeiterfassung=Zugriff.KEIN, recht_adressen=Zugriff.ANSEHEN,
    )
    assert "/zeiterfassung" not in client.get("/").text


# --- 5) IDOR: fremde Zeilen sind für Nicht-Admins unerreichbar ----------------


@pytest.fixture
def fremde_zeile(engine, client) -> int:
    """Zeile eines ANDEREN Mitarbeiters; angemeldet ist ein Nicht-Admin mit Vollrecht."""
    fremder_id = _benutzer_anlegen(engine, login="kollege", rolle="egal")
    zeile_id = _zeile_anlegen(
        engine, benutzer_id=fremder_id, taetigkeit="Fremde Regiestunde", stunden="8"
    )
    _login_mit_rechten(engine, client, login="neugierig", **ALLE_RECHTE)
    return zeile_id


def test_idor_fremde_zeile_nicht_lesbar(client, fremde_zeile):
    r = client.get(f"/zeiterfassung/{fremde_zeile}", follow_redirects=False)
    assert r.status_code == NOT_FOUND, "fremde Zeile darf nicht lesbar sein"


def test_idor_fremde_zeile_nicht_aenderbar(engine, client, fremde_zeile):
    r = client.post(
        f"/zeiterfassung/{fremde_zeile}",
        data={"datum": date.today().isoformat(), "stunden": "99", "stundensatz": "1"},
        follow_redirects=False,
    )
    assert r.status_code == NOT_FOUND
    with Session(engine) as session:
        assert session.get(Zeiterfassung, fremde_zeile).stunden == Decimal("8")


def test_idor_fremde_zeile_nicht_loeschbar(engine, client, fremde_zeile):
    r = client.post(f"/zeiterfassung/{fremde_zeile}/loeschen", follow_redirects=False)
    assert r.status_code == NOT_FOUND
    with Session(engine) as session:
        assert session.get(Zeiterfassung, fremde_zeile) is not None


def test_uebersicht_zeigt_nur_eigene_zeilen(client, fremde_zeile):
    text = client.get("/zeiterfassung").text
    assert "Fremde Regiestunde" not in text
    assert "ausschließlich Ihre eigenen" in text


def test_mitarbeiterfilter_wird_fuer_nichtadmin_ignoriert(engine, client, fremde_zeile):
    """Auch ein manipulierter Filter-Parameter darf keine fremden Zeilen liefern."""
    with Session(engine) as session:
        fremder_id = session.get(Zeiterfassung, fremde_zeile).benutzer_id

    text = client.get(f"/zeiterfassung?mitarbeiter={fremder_id}").text
    assert "Fremde Regiestunde" not in text
    text_partial = client.get(f"/zeiterfassung/zeilen?mitarbeiter={fremder_id}").text
    assert "Fremde Regiestunde" not in text_partial


def test_admin_sieht_und_aendert_fremde_zeiten(engine, client):
    fremder_id = _benutzer_anlegen(engine, login="kollege2", rolle="egal")
    zeile_id = _zeile_anlegen(
        engine, benutzer_id=fremder_id, taetigkeit="Fremde Regiestunde", stunden="8"
    )
    _login_mit_rechten(engine, client, login="chefin", ist_admin=True)

    uebersicht = client.get("/zeiterfassung")
    assert uebersicht.status_code == OK
    assert "Fremde Regiestunde" in uebersicht.text
    assert client.get(f"/zeiterfassung/{zeile_id}").status_code == OK

    r = client.post(
        f"/zeiterfassung/{zeile_id}",
        data={
            "datum": date.today().isoformat(),
            "stunden": "7",
            "stundensatz": "70",
            "taetigkeit": "Korrigiert",
        },
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        zeile = session.get(Zeiterfassung, zeile_id)
        assert zeile.stunden == Decimal("7")
        assert zeile.taetigkeit == "Korrigiert"
        assert zeile.benutzer_id == fremder_id, "Der Eigentümer darf sich nicht ändern"


def test_admin_kann_nach_mitarbeiter_filtern(engine, client):
    a_id = _benutzer_anlegen(engine, login="mitarb_a", rolle="egal")
    b_id = _benutzer_anlegen(engine, login="mitarb_b", rolle="egal")
    _zeile_anlegen(engine, benutzer_id=a_id, taetigkeit="Zeile von A")
    _zeile_anlegen(engine, benutzer_id=b_id, taetigkeit="Zeile von B")
    _login_mit_rechten(engine, client, login="chefin_filter", ist_admin=True)

    alle = client.get("/zeiterfassung").text
    assert "Zeile von A" in alle and "Zeile von B" in alle

    nur_a = client.get(f"/zeiterfassung?mitarbeiter={a_id}").text
    assert "Zeile von A" in nur_a
    assert "Zeile von B" not in nur_a


def test_admin_kann_zeit_fuer_anderen_mitarbeiter_buchen(engine, client):
    ziel_id = _benutzer_anlegen(engine, login="mitarb_ziel", rolle="egal")
    _login_mit_rechten(engine, client, login="chefin_buch", ist_admin=True)

    r = client.post(
        "/zeiterfassung/neu",
        data={
            "datum": date.today().isoformat(),
            "stunden": "2",
            "stundensatz": "60",
            "benutzer_id": str(ziel_id),
        },
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        assert session.exec(select(Zeiterfassung)).one().benutzer_id == ziel_id


def test_nichtadmin_kann_nicht_fuer_andere_buchen(engine, client):
    """Ein untergeschobenes ``benutzer_id`` landet trotzdem auf der eigenen ID."""
    fremder_id = _benutzer_anlegen(engine, login="opfer", rolle="egal")
    eigene_id = _login_mit_rechten(engine, client, login="untersteller", **ALLE_RECHTE)

    r = client.post(
        "/zeiterfassung/neu",
        data={
            "datum": date.today().isoformat(),
            "stunden": "2",
            "stundensatz": "60",
            "benutzer_id": str(fremder_id),
        },
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        assert session.exec(select(Zeiterfassung)).one().benutzer_id == eigene_id


def test_eigentuemerpruefung_liest_frisch_aus_der_db(engine, client):
    """Wird die Admin-Rolle entzogen, sind fremde Zeilen sofort tabu (ohne Re-Login)."""
    fremder_id = _benutzer_anlegen(engine, login="kollege3", rolle="egal")
    zeile_id = _zeile_anlegen(engine, benutzer_id=fremder_id)
    _login_mit_rechten(engine, client, login="exchefin", ist_admin=True)
    assert client.get(f"/zeiterfassung/{zeile_id}").status_code == OK

    with Session(engine) as session:
        profil = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == "rolle_exchefin")
        ).one()
        profil.ist_admin = False
        profil.recht_zeiterfassung = Zugriff.BEARBEITEN
        session.add(profil)
        session.commit()

    r = client.get(f"/zeiterfassung/{zeile_id}", follow_redirects=False)
    assert r.status_code == NOT_FOUND, "Entzogene Admin-Rechte müssen sofort wirken"


# --- 6) Stundensatz in der Mitarbeiterverwaltung ------------------------------


def test_stundensatz_wird_beim_anlegen_gespeichert(engine, client):
    _benutzer_anlegen(engine, login="chef_neu", rolle=Rolle.ADMIN)
    _anmelden(client, "chef_neu")

    r = client.post(
        "/benutzer/neu",
        data={
            "login": "monteur9", "name": "Moritz Monteur",
            "rolle": "monteur", "passwort": "start123", "stundensatz": "82,50",
        },
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        neuer = session.exec(select(Benutzer).where(Benutzer.login == "monteur9")).one()
        assert neuer.stundensatz == Decimal("82.50")


def test_stundensatz_wird_beim_bearbeiten_gespeichert(engine, client):
    _benutzer_anlegen(engine, login="chef_edit", rolle=Rolle.ADMIN)
    ziel_id = _benutzer_anlegen(engine, login="monteur8", rolle="monteur", stundensatz="0")
    _anmelden(client, "chef_edit")

    r = client.post(
        f"/benutzer/{ziel_id}",
        data={"name": "Monteur Acht", "rolle": "monteur", "aktiv": "true",
              "stundensatz": "1.234,50"},
        follow_redirects=False,
    )
    assert r.status_code == REDIRECT
    with Session(engine) as session:
        assert session.get(Benutzer, ziel_id).stundensatz == Decimal("1234.50")


def test_stundensatz_feld_im_formular(engine, client):
    _benutzer_anlegen(engine, login="chef_form", rolle=Rolle.ADMIN)
    ziel_id = _benutzer_anlegen(engine, login="monteur7", rolle="monteur", stundensatz="65.00")
    _anmelden(client, "chef_form")

    text = client.get(f"/benutzer/{ziel_id}").text
    assert 'name="stundensatz"' in text
    assert "65,00" in text
    assert "Stundensatz" in text
