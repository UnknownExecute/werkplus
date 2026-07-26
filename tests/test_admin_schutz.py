"""Tests gegen Privilegien-Eskalation und Admin-Aussperrung.

Zwei Invarianten:
1. ``ist_admin`` an einer Rolle darf nur ein echter Admin setzen — sonst könnte
   sich jemand mit bloßem Benutzer-Bearbeitungsrecht selbst erhöhen.
2. Der letzte aktive Admin darf nicht deaktiviert oder herabgestuft werden.
"""

from sqlmodel import Session, select

from app.models import Rolle, Rollenprofil, Zugriff
from app.models.benutzer import Benutzer
from app.services.auth_service import hash_passwort


def _benutzer(session, login: str, rolle: str) -> Benutzer:
    b = Benutzer(
        login=login,
        name=login.title(),
        passwort_hash=hash_passwort("geheim"),
        rolle=rolle,
        aktiv=True,
    )
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def _anmelden(client, login: str) -> None:
    r = client.post(
        "/login", data={"login": login, "passwort": "geheim"}, follow_redirects=False
    )
    assert r.status_code == 302


# --- S7a: keine Eskalation über eine selbst angelegte Admin-Rolle ------------


def test_nicht_admin_kann_keine_admin_rolle_anlegen(engine, client):
    """Benutzerverwaltungsrecht ohne Admin-Status darf ist_admin nicht setzen."""
    with Session(engine) as session:
        profil = Rollenprofil(
            name="personaler",
            beschreibung="darf Mitarbeiter pflegen, ist aber kein Admin",
            recht_benutzer=Zugriff.BEARBEITEN,
        )
        session.add(profil)
        session.commit()
        _benutzer(session, "personaler", "personaler")
    _anmelden(client, "personaler")

    r = client.post(
        "/benutzer/rollen/neu",
        data={"name": "hintertuer", "ist_admin": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 302

    with Session(engine) as session:
        neu = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == "hintertuer")
        ).one()
        # Rolle wurde angelegt, aber OHNE Admin-Recht
        assert neu.ist_admin is False


def test_admin_kann_admin_rolle_anlegen(engine, client):
    """Gegenprobe: ein echter Admin darf ist_admin setzen."""
    with Session(engine) as session:
        _benutzer(session, "chef", Rolle.ADMIN)
    _anmelden(client, "chef")

    r = client.post(
        "/benutzer/rollen/neu",
        data={"name": "zweitadmin", "ist_admin": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        neu = session.exec(
            select(Rollenprofil).where(Rollenprofil.name == "zweitadmin")
        ).one()
        assert neu.ist_admin is True


# --- S7b: letzter Admin bleibt handlungsfähig -------------------------------


def test_letzter_admin_kann_nicht_herabgestuft_werden(engine, client):
    with Session(engine) as session:
        session.add(Rollenprofil(name="monteur", recht_anlagen=Zugriff.BEARBEITEN))
        session.commit()
        chef = _benutzer(session, "chef", Rolle.ADMIN)
        chef_id = chef.id
    _anmelden(client, "chef")

    r = client.post(
        f"/benutzer/{chef_id}",
        data={"name": "Chef", "rolle": "monteur", "aktiv": "true"},
    )
    assert r.status_code == 200
    assert "letzte aktive Administrator" in r.text

    with Session(engine) as session:
        assert session.get(Benutzer, chef_id).rolle == Rolle.ADMIN


def test_admin_herabstufen_klappt_wenn_zweiter_admin_existiert(engine, client):
    with Session(engine) as session:
        session.add(Rollenprofil(name="monteur", recht_anlagen=Zugriff.BEARBEITEN))
        session.commit()
        chef = _benutzer(session, "chef", Rolle.ADMIN)
        _benutzer(session, "chef2", Rolle.ADMIN)
        chef_id = chef.id
    _anmelden(client, "chef2")

    r = client.post(
        f"/benutzer/{chef_id}",
        data={"name": "Chef", "rolle": "monteur", "aktiv": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    with Session(engine) as session:
        assert session.get(Benutzer, chef_id).rolle == "monteur"
