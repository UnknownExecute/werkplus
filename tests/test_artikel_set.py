"""Tests für Artikel-Sets / Stücklisten: Komponentenverwaltung und Einfüge-Service."""

from decimal import Decimal

from sqlmodel import Session, select

from app.models import Adresse, Artikel, Belegart, SetKomponente
from app.services import artikel_set_service, beleg_service
from tests.test_stammdaten_ui import _login


def _artikel(
    session: Session, nr: str, bez: str, *, ist_set: bool = False, vk: str = "100"
) -> Artikel:
    artikel = Artikel(artikelnr=nr, bezeichnung=bez, ist_set=ist_set, vk_preis=Decimal(vk))
    session.add(artikel)
    session.commit()
    session.refresh(artikel)
    return artikel


def test_set_komponente_hinzufuegen_und_loeschen(engine, client):
    _login(engine, client)
    with Session(engine) as session:
        set_artikel = _artikel(session, "SET-1", "Bad-Set", ist_set=True)
        komponente = _artikel(session, "K-1", "Rohr")
        set_id, komp_art_id = set_artikel.id, komponente.id

    r = client.post(
        f"/artikel/{set_id}/set-komponenten",
        data={"komponente_artikel_id": komp_art_id, "menge": "3"},
    )
    assert r.status_code == 200
    assert "Rohr" in r.text

    with Session(engine) as session:
        zeilen = session.exec(
            select(SetKomponente).where(SetKomponente.set_artikel_id == set_id)
        ).all()
        assert len(zeilen) == 1
        assert zeilen[0].menge == Decimal("3")
        komp_id = zeilen[0].id

    r = client.post(f"/artikel/{set_id}/set-komponenten/{komp_id}/loeschen")
    assert r.status_code == 200
    with Session(engine) as session:
        assert session.exec(select(SetKomponente)).all() == []


def test_set_kann_kein_set_als_komponente_bekommen(engine, client):
    _login(engine, client)
    with Session(engine) as session:
        set_a = _artikel(session, "SET-1", "Set A", ist_set=True)
        set_b = _artikel(session, "SET-2", "Set B", ist_set=True)
        set_a_id, set_b_id = set_a.id, set_b.id

    # Ein Set als Komponente (inkl. Selbstbezug) wird still ignoriert.
    r = client.post(
        f"/artikel/{set_a_id}/set-komponenten",
        data={"komponente_artikel_id": set_b_id, "menge": "1"},
    )
    assert r.status_code == 200
    r = client.post(
        f"/artikel/{set_a_id}/set-komponenten",
        data={"komponente_artikel_id": set_a_id, "menge": "1"},
    )
    assert r.status_code == 200

    with Session(engine) as session:
        assert session.exec(select(SetKomponente)).all() == []


def test_fuege_set_positionen_ein_je_komponente_eine_position(session):
    adresse = Adresse(firma="Kunde GmbH")
    session.add(adresse)
    session.commit()
    session.refresh(adresse)

    set_artikel = _artikel(session, "SET-1", "Heizungs-Set", ist_set=True)
    k1 = _artikel(session, "K-1", "Teil 1", vk="10")
    k2 = _artikel(session, "K-2", "Teil 2", vk="20")
    session.add(
        SetKomponente(
            set_artikel_id=set_artikel.id,
            komponente_artikel_id=k1.id,
            menge=Decimal("2"),
            sort=0,
        )
    )
    session.add(
        SetKomponente(
            set_artikel_id=set_artikel.id,
            komponente_artikel_id=k2.id,
            menge=Decimal("3"),
            sort=1,
        )
    )
    session.commit()

    beleg = beleg_service.erstelle_beleg(session, Belegart.ANGEBOT, adresse)
    positionen = artikel_set_service.fuege_set_positionen_ein(
        session, beleg, set_artikel, menge_faktor=Decimal("2")
    )
    session.commit()

    assert len(positionen) == 2
    # Reihenfolge nach sort; Menge = Komponentenmenge * menge_faktor
    assert positionen[0].artikel_id == k1.id
    assert positionen[0].menge == Decimal("4")
    assert positionen[0].einzelpreis == Decimal("10")
    assert positionen[1].artikel_id == k2.id
    assert positionen[1].menge == Decimal("6")
