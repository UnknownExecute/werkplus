"""Belegkette im Kplus-Stil: Preisanfrage, Beleg-Muster, Belegadresse tauschen,
Mailversand mit PDF (SMTP gefälscht)."""

from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    Adresse,
    AdressTyp,
    Aktivitaet,
    Artikel,
    Beleg,
    Belegart,
    BelegPosition,
    BelegStatus,
)
from app.models.benutzer import Benutzer, Rolle
from app.services import beleg_service, mail_service, pdf_service
from app.services.auth_service import hash_passwort
from app.services.fehler import UngueltigerStatusUebergang, WerkplusFehler


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


def _stammdaten(engine) -> tuple[int, int, int, int]:
    """(kunde, zweiter kunde, lieferant, artikel)"""
    with Session(engine) as session:
        kunde = Adresse(typ=AdressTyp.KUNDE, firma="Kunde Eins", ort="Lienz",
                        email="eins@kunde.at", briefanrede="Hallo Eins,", zahlungsziel_tage=14)
        kunde2 = Adresse(typ=AdressTyp.KUNDE, firma="Kunde Zwei", ort="Graz", zahlungsziel_tage=30)
        lieferant = Adresse(typ=AdressTyp.LIEFERANT, firma="Lieferant AG", ort="Wien",
                            email="verkauf@lieferant.at")
        artikel = Artikel(artikelnr="X1", bezeichnung="Ventil", vk_preis=Decimal("50"),
                          ek_preis=Decimal("20"), ust_satz=20)
        session.add_all([kunde, kunde2, lieferant, artikel])
        session.commit()
        return kunde.id, kunde2.id, lieferant.id, artikel.id


def _neu(client, art, adresse_id, **extra) -> int:
    r = client.post(f"/belege/{art}/neu", data={"adresse_id": adresse_id, **extra},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


def _position(client, beleg_id, artikel_id):
    r = client.post(f"/belege/detail/{beleg_id}/positionen", data={"artikel_id": artikel_id})
    assert r.status_code == 200


# ---------------------------------------------------------------- Preisanfrage


def test_preisanfrage_kette_und_pdf(engine, client):
    _login(engine, client)
    _, _, lieferant_id, artikel_id = _stammdaten(engine)
    html = client.get("/belege/uebersicht/ek").text
    assert "?art=preisanfrage" in html and "Preisanfrage" in html

    pa_id = _neu(client, "preisanfrage", lieferant_id, betreff="Anfrage Ventile")
    _position(client, pa_id, artikel_id)
    with Session(engine) as session:
        pa = session.get(Beleg, pa_id)
        assert pa.belegart == Belegart.PREISANFRAGE
        assert pa.belegnr.endswith("0001")
    # PDF ohne Preise, mit Bitte um Angebot
    druck = client.get(f"/belege/detail/{pa_id}/druckvorschau").text
    assert "Preisanfrage" in druck and "Ihr Preis" in druck and "50,00" not in druck
    # Folgebeleg = Bestellung
    client.post(f"/belege/detail/{pa_id}/status/gedruckt")
    r = client.post(f"/belege/detail/{pa_id}/folgebeleg", follow_redirects=False)
    best_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    with Session(engine) as session:
        best = session.get(Beleg, best_id)
        assert best.belegart == Belegart.BESTELLUNG
        assert best.vorgaenger_beleg_id == pa_id and best.betreff == "Anfrage Ventile"
        assert session.get(Beleg, pa_id).status == BelegStatus.ERLEDIGT
    # Übersicht: Preisanfrage-Reiter zeigt sie mit der Bestellung als Folgebeleg
    html = client.get("/belege/uebersicht/ek", params={"art": "preisanfrage"}).text
    assert f"/belege/detail/{best_id}" in html


def test_preisanfrage_braucht_einkaufsrecht(engine, client):
    from app.models import Rollenprofil, Zugriff

    with Session(engine) as session:
        session.add(Rollenprofil(name="nurverkauf", recht_verkauf=Zugriff.BEARBEITEN))
        session.commit()
    _login(engine, client, login="vk", rolle="nurverkauf")
    assert client.get("/belege/preisanfrage").status_code == 403


# -------------------------------------------------------------------- Muster


def test_muster_anlegen_und_beleg_daraus_erzeugen(engine, client):
    _login(engine, client)
    kunde_id, kunde2_id, _, artikel_id = _stammdaten(engine)
    muster_id = _neu(client, "angebot", kunde_id, betreff="Wartung Standard", als_muster="1")
    _position(client, muster_id, artikel_id)
    with Session(engine) as session:
        muster = session.get(Beleg, muster_id)
        assert muster.ist_muster and muster.belegnr == "M0001"
        assert muster.status == BelegStatus.ENTWURF

    html = client.get(f"/belege/detail/{muster_id}").text
    assert "Muster (Vorlage)" in html and "Beleg aus Muster erzeugen" in html
    assert "Drucken/Buchen" not in html and "Auftrag erhalten" not in html

    # Muster können nicht gebucht werden
    r = client.post(f"/belege/detail/{muster_id}/status/gedruckt", follow_redirects=False)
    assert "fehler=" in r.headers["location"]
    with Session(engine) as session:
        assert session.get(Beleg, muster_id).status == BelegStatus.ENTWURF

    # Dialog + Beleg für einen ANDEREN Kunden erzeugen
    assert "Beleg aus Muster" in client.get(f"/belege/detail/{muster_id}/aus-muster-dialog").text
    r = client.post(f"/belege/detail/{muster_id}/aus-muster", data={"adresse_id": kunde2_id},
                    follow_redirects=False)
    neu_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        neu = session.get(Beleg, neu_id)
        assert not neu.ist_muster
        assert neu.belegnr.endswith("0001") and not neu.belegnr.startswith("M")
        assert neu.adresse_id == kunde2_id and neu.zahlungsziel_tage == 30
        assert neu.betreff == "Wartung Standard"
        stmt = select(BelegPosition).where(BelegPosition.beleg_id == neu_id)
        positionen = session.exec(stmt).all()
        assert [p.bezeichnung for p in positionen] == ["Ventil"]
        assert session.get(Beleg, muster_id).ist_muster  # Muster bleibt

    # Muster erscheint nur im Muster-Filter der Übersicht, der echte Beleg nur sonst
    alle = client.get("/belege/uebersicht/vk/zeilen", params={"art": "angebot"}).text
    nur_muster = client.get("/belege/uebersicht/vk/zeilen",
                            params={"art": "angebot", "status_filter": "muster"}).text
    assert f'data-id="{neu_id}"' in alle and f'data-id="{muster_id}"' not in alle
    assert f'data-id="{muster_id}"' in nur_muster and f'data-id="{neu_id}"' not in nur_muster
    # Dashboard ignoriert Muster
    assert "M0001" not in client.get("/").text


def test_als_muster_speichern(engine, client):
    _login(engine, client)
    kunde_id, _, _, artikel_id = _stammdaten(engine)
    beleg_id = _neu(client, "auftrag", kunde_id, betreff="Regie", auftragsart="service")
    _position(client, beleg_id, artikel_id)
    r = client.post(f"/belege/detail/{beleg_id}/als-muster", follow_redirects=False)
    muster_id = int(r.headers["location"].rsplit("/", 1)[-1])
    with Session(engine) as session:
        muster = session.get(Beleg, muster_id)
        assert muster.ist_muster and muster.belegnr == "M0001"
        assert (muster.betreff, muster.auftragsart) == ("Regie", "service")
        assert session.get(Beleg, beleg_id).ist_muster is False
        # zweites Muster derselben Art zählt weiter
        zweites = beleg_service.speichere_als_muster(session, session.get(Beleg, beleg_id))
        assert zweites.belegnr == "M0002"
        with pytest.raises(UngueltigerStatusUebergang):
            beleg_service.setze_status(session, zweites, BelegStatus.GEDRUCKT)
        with pytest.raises(WerkplusFehler):
            beleg_service.erzeuge_aus_muster(session, session.get(Beleg, beleg_id))


# ---------------------------------------------------------- Adresse tauschen


def test_belegadresse_tauschen(engine, client):
    _login(engine, client)
    kunde_id, kunde2_id, lieferant_id, artikel_id = _stammdaten(engine)
    beleg_id = _neu(client, "angebot", kunde_id)
    _position(client, beleg_id, artikel_id)
    assert "Belegadresse tauschen" in client.get(f"/belege/detail/{beleg_id}/adresse-dialog").text

    r = client.post(f"/belege/detail/{beleg_id}/adresse", data={"adresse_id": kunde2_id},
                    follow_redirects=False)
    assert r.status_code == 302 and "fehler" not in r.headers["location"]
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert beleg.adresse_id == kunde2_id and beleg.zahlungsziel_tage == 30
        assert session.exec(select(BelegPosition).where(BelegPosition.beleg_id == beleg_id)).all()
    # Lieferant an einem Verkaufsbeleg → Fehler
    r = client.post(f"/belege/detail/{beleg_id}/adresse", data={"adresse_id": lieferant_id},
                    follow_redirects=False)
    assert "fehler=" in r.headers["location"]
    # gebucht → gesperrt
    client.post(f"/belege/detail/{beleg_id}/status/gedruckt")
    r = client.post(f"/belege/detail/{beleg_id}/adresse", data={"adresse_id": kunde_id},
                    follow_redirects=False)
    assert "fehler=" in r.headers["location"]
    assert client.get(f"/belege/detail/{beleg_id}/adresse-dialog").status_code == 409


# ------------------------------------------------------------------- Mail


class _FakeSmtp:
    gesendet: list = []
    logins: list = []

    def __init__(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, benutzer, passwort):
        _FakeSmtp.logins.append(benutzer)

    def send_message(self, nachricht, from_addr=None, to_addrs=None):
        _FakeSmtp.gesendet.append((nachricht, from_addr, to_addrs))


@pytest.fixture
def smtp_fake(monkeypatch):
    _FakeSmtp.gesendet = []
    _FakeSmtp.logins = []
    monkeypatch.setattr(settings, "smtp_host", "mail.test.local")
    monkeypatch.setattr(settings, "smtp_benutzer", "office@schedl.at")
    monkeypatch.setattr(settings, "smtp_passwort", "geheim")
    monkeypatch.setattr(settings, "smtp_absender", "office@schedl.at")
    monkeypatch.setattr(mail_service, "_smtp_factory", lambda: _FakeSmtp())
    monkeypatch.setattr(pdf_service, "render_beleg_pdf", lambda session, beleg: b"%PDF-1.4 fake")
    return _FakeSmtp


def test_mail_dialog_vorbelegt(engine, client, smtp_fake):
    _login(engine, client)
    kunde_id, _, _, _ = _stammdaten(engine)
    beleg_id = _neu(client, "angebot", kunde_id, betreff="Wartung")
    html = client.get(f"/belege/detail/{beleg_id}/mail-dialog").text
    assert 'value="eins@kunde.at"' in html
    assert "Angebot" in html and "Wartung" in html and "Hallo Eins," in html
    assert "wird beim Versand gebucht" in html
    assert "Mail versenden" in client.get(f"/belege/detail/{beleg_id}").text


def test_mail_versand_bucht_und_protokolliert(engine, client, smtp_fake):
    benutzer_id = _login(engine, client)
    kunde_id, _, _, artikel_id = _stammdaten(engine)
    beleg_id = _neu(client, "angebot", kunde_id, betreff="Wartung")
    _position(client, beleg_id, artikel_id)
    r = client.post(
        f"/belege/detail/{beleg_id}/mail",
        data={"an": "eins@kunde.at; chef@kunde.at", "cc": "intern@schedl.at",
              "betreff": "Ihr Angebot", "text": "Hallo,\nanbei."},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "gesendet=" in r.headers["location"]
    assert len(smtp_fake.gesendet) == 1
    nachricht, von, an = smtp_fake.gesendet[0]
    assert von == "office@schedl.at"
    assert an == ["eins@kunde.at", "chef@kunde.at", "intern@schedl.at"]
    assert nachricht["Subject"] == "Ihr Angebot" and nachricht["Cc"] == "intern@schedl.at"
    anhaenge = [teil.get_filename() for teil in nachricht.iter_attachments()]
    assert anhaenge and anhaenge[0].endswith(".pdf")
    assert smtp_fake.logins == ["office@schedl.at"]
    with Session(engine) as session:
        beleg = session.get(Beleg, beleg_id)
        assert beleg.status == BelegStatus.GEDRUCKT and beleg.gedruckt_am is not None
        akt = session.exec(select(Aktivitaet)).one()
        assert akt.typ == "email" and akt.beleg_id == beleg_id and akt.benutzer_id == benutzer_id
        assert "eins@kunde.at" in akt.text
    # Erfolgsmeldung auf der Editor-Seite
    html = client.get(r.headers["location"]).text
    assert "per Mail versendet an eins@kunde.at" in html


def test_mail_fehler_laesst_beleg_unveraendert(engine, client, smtp_fake, monkeypatch):
    _login(engine, client)
    kunde_id, _, _, artikel_id = _stammdaten(engine)
    beleg_id = _neu(client, "angebot", kunde_id)
    _position(client, beleg_id, artikel_id)

    class _Kaputt(_FakeSmtp):
        def send_message(self, *a, **k):
            raise OSError("Verbindung abgelehnt")

    monkeypatch.setattr(mail_service, "_smtp_factory", lambda: _Kaputt())
    r = client.post(f"/belege/detail/{beleg_id}/mail", data={"an": "x@y.at"},
                    follow_redirects=False)
    assert "fehler=" in r.headers["location"]
    with Session(engine) as session:
        assert session.get(Beleg, beleg_id).status == BelegStatus.ENTWURF
        assert session.exec(select(Aktivitaet)).all() == []
    # ohne Empfänger
    r = client.post(f"/belege/detail/{beleg_id}/mail", data={"an": " "}, follow_redirects=False)
    assert "fehler=" in r.headers["location"]


def test_mail_nicht_konfiguriert(engine, client, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    _login(engine, client)
    kunde_id, _, _, _ = _stammdaten(engine)
    beleg_id = _neu(client, "angebot", kunde_id)
    html = client.get(f"/belege/detail/{beleg_id}").text
    assert "Mailversand nicht konfiguriert" in html
    dialog = client.get(f"/belege/detail/{beleg_id}/mail-dialog").text
    assert "WERKPLUS_SMTP_HOST" in dialog
    r = client.post(f"/belege/detail/{beleg_id}/mail", data={"an": "x@y.at"},
                    follow_redirects=False)
    assert "fehler=" in r.headers["location"]


def test_muster_nicht_per_mail(engine, client, smtp_fake):
    _login(engine, client)
    kunde_id, _, _, _ = _stammdaten(engine)
    muster_id = _neu(client, "angebot", kunde_id, als_muster="1")
    assert client.get(f"/belege/detail/{muster_id}/mail-dialog").status_code == 409
    r = client.post(f"/belege/detail/{muster_id}/mail", data={"an": "x@y.at"},
                    follow_redirects=False)
    assert "fehler=" in r.headers["location"]
    assert smtp_fake.gesendet == []
