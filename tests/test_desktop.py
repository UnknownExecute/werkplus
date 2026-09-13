"""Kplus-Desktop: Kalender (Wiedervorlagen, Belegtermine, Wartungen), Favoriten,
Schnellzugriff und die Tastatur-Hilfe."""

from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import Adresse, AdressTyp, Aktivitaet, Anlage, Belegart, Favorit
from app.models.benutzer import Benutzer, Rolle
from app.services import beleg_service, favoriten_service, kalender_service
from app.services.auth_service import hash_passwort
from app.services.fehler import WerkplusFehler


def _login(engine, client, login="tester", rolle=Rolle.SACHBEARBEITER) -> int:
    with Session(engine) as session:
        benutzer = Benutzer(
            login=login, name=f"Name {login}", passwort_hash=hash_passwort("geheim"),
            rolle=rolle, aktiv=True,
        )
        session.add(benutzer)
        session.commit()
        benutzer_id = benutzer.id
    r = client.post("/login", data={"login": login, "passwort": "geheim"}, follow_redirects=False)
    assert r.status_code == 302
    return benutzer_id


def _daten(engine, heute: date) -> dict:
    with Session(engine) as session:
        kunde = Adresse(typ=AdressTyp.KUNDE, firma="Kalender Kunde", ort="Lienz")
        session.add(kunde)
        session.commit()
        session.add(Aktivitaet(adresse_id=kunde.id, betreff="Rückruf Angebot",
                               wiedervorlage_am=heute + timedelta(days=2)))
        session.add(Aktivitaet(adresse_id=kunde.id, betreff="Erledigte Sache",
                               wiedervorlage_am=heute, erledigt=True))
        session.add(Anlage(adresse_id=kunde.id, anlagenr="1001", bezeichnung="Ölkessel",
                           naechste_wartung=heute - timedelta(days=3), aktiv=True))
        session.add(Anlage(adresse_id=kunde.id, anlagenr="1002", bezeichnung="Inaktiv",
                           naechste_wartung=heute, aktiv=False))
        auftrag = beleg_service.erstelle_beleg(
            session, Belegart.AUFTRAG, kunde, betreff="Montage", termin=heute + timedelta(days=1)
        )
        muster = beleg_service.erstelle_muster(
            session, Belegart.AUFTRAG, kunde, betreff="Mustertermin", termin=heute
        )
        session.commit()
        return {"kunde": kunde.id, "auftrag": auftrag.id, "muster": muster.id}


# ------------------------------------------------------------------ Kalender


def test_kalender_eintraege_service(engine):
    heute = date.today()
    ids = _daten(engine, heute)
    with Session(engine) as session:
        eintraege = kalender_service.eintraege(session, heute - timedelta(days=10),
                                              heute + timedelta(days=10))
    arten = {(e.art, e.titel) for e in eintraege}
    assert ("wiedervorlage", "Rückruf Angebot") in arten
    assert ("wartung", "Wartung Ölkessel") in arten
    assert any(a == "termin" and "Montage" in t for a, t in arten)
    # erledigte Wiedervorlage, inaktive Anlage und Muster fehlen
    assert not any("Erledigte" in t or "Inaktiv" in t or "Muster" in t for _, t in arten)
    assert [e.datum for e in eintraege] == sorted(e.datum for e in eintraege)
    urls = {e.url for e in eintraege}
    assert f"/belege/detail/{ids['auftrag']}" in urls and "/anlagen/" in "".join(urls)


def test_monatsraster_und_verschieben():
    raster = kalender_service.monatsraster(2026, 9)
    assert raster[0][0].weekday() == 0 and raster[-1][-1].weekday() == 6
    assert any(date(2026, 9, 13) in woche for woche in raster)
    assert kalender_service.monat_verschieben(2026, 12, 1) == (2027, 1)
    assert kalender_service.monat_verschieben(2026, 1, -1) == (2025, 12)


def test_kalender_seite(engine, client):
    _login(engine, client)
    heute = date.today()
    _daten(engine, heute)
    r = client.get("/kalender")
    assert r.status_code == 200
    assert "Rückruf Angebot" in r.text and "Wartung Ölkessel" in r.text and "Montage" in r.text
    assert f'id="tag-{heute.isoformat()}"' in r.text and "wp-kal-heute" in r.text
    # Monatsnavigation
    r = client.get("/kalender", params={"monat": "2026-01"})
    assert "Jänner 2026" in r.text and "monat=2025-12" in r.text and "monat=2026-02" in r.text
    assert client.get("/kalender", params={"monat": "quatsch"}).status_code == 200
    # Dashboard-Miniatur und Schnellzugriff
    html = client.get("/").text
    assert "wp-kalender-mini" in html and "Schnellzugriff" in html and "Favoriten" in html
    assert f"#tag-{heute.isoformat()}" in html or "wp-kal-punkt" in html


def test_kalender_respektiert_rechte(engine, client):
    from app.models import Rollenprofil, Zugriff

    with Session(engine) as session:
        session.add(Rollenprofil(name="nuranlagen", recht_anlagen=Zugriff.ANSEHEN))
        session.commit()
    _login(engine, client, login="monteur", rolle="nuranlagen")
    _daten(engine, date.today())
    html = client.get("/kalender").text
    assert "Wartung Ölkessel" in html
    assert "Rückruf Angebot" not in html and "Montage" not in html


# ------------------------------------------------------------------ Favoriten


def test_favoriten_anlegen_sidebar_loeschen(engine, client):
    benutzer_id = _login(engine, client)
    r = client.post(
        "/favoriten",
        data={"titel": "Belegübersicht VK", "url": "/belege/uebersicht/vk?art=auftrag"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/belege/uebersicht/vk?art=auftrag"
    html = client.get("/").text
    assert "Favoriten" in html and 'href="/belege/uebersicht/vk?art=auftrag"' in html
    assert "wp-nav-favorit" in html
    # doppelt → nicht doppelt
    client.post("/favoriten", data={"titel": "nochmal", "url": "/belege/uebersicht/vk?art=auftrag"})
    with Session(engine) as session:
        favs = favoriten_service.fuer_benutzer(session, benutzer_id)
        assert len(favs) == 1
        fav_id = favs[0].id
        # nur interne Pfade
        try:
            favoriten_service.hinzufuegen(session, benutzer_id, "böse", "https://example.com")
        except WerkplusFehler:
            pass
        else:
            raise AssertionError("externe URL darf kein Favorit sein")
    r = client.post(f"/favoriten/{fav_id}/loeschen", data={"zurueck": "/"}, follow_redirects=False)
    assert r.status_code == 302
    with Session(engine) as session:
        assert session.exec(select(Favorit)).all() == []


def test_favoriten_sind_je_benutzer(engine, client):
    erster = _login(engine, client)
    client.post("/favoriten", data={"titel": "Meins", "url": "/artikel"})
    client.post("/logout")
    zweiter = _login(engine, client, login="zweiter")
    assert "Meins" not in client.get("/").text
    with Session(engine) as session:
        fav = session.exec(select(Favorit)).one()
        assert fav.benutzer_id == erster
        try:
            favoriten_service.entfernen(session, zweiter, fav.id)
        except WerkplusFehler:
            pass
        else:
            raise AssertionError("fremder Favorit darf nicht löschbar sein")


def test_tastaturhilfe_und_navigierbare_listen(engine, client):
    _login(engine, client)
    html = client.get("/adressen").text
    assert "tabelle-navigierbar" in html
    assert 'id="tastenModal"' in html and "Tastenkürzel" in html
    assert 'id="favorit-form"' in html
