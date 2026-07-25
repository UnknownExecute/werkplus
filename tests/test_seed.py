import pytest
from sqlmodel import func, select

from app.models import (
    Adresse,
    AdressTyp,
    Anlage,
    Artikel,
    Beleg,
    Benutzer,
    Lager,
    Lagerbestand,
    Seriennummer,
    SnStatus,
)
from app.services import beleg_service
from scripts import seed


@pytest.fixture(autouse=True)
def _hooks_aufraeumen():
    """seed_demo_durchlauf registriert Lager-Hooks — nach dem Test wieder leeren."""
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


def test_seed_legt_alle_stammdaten_an(session):
    seed.run(session)

    assert session.exec(select(func.count()).select_from(Benutzer)).one() == 2
    kunden = session.exec(
        select(func.count()).select_from(Adresse).where(Adresse.typ == AdressTyp.KUNDE)
    ).one()
    lieferanten = session.exec(
        select(func.count()).select_from(Adresse).where(Adresse.typ == AdressTyp.LIEFERANT)
    ).one()
    assert kunden == 5
    assert lieferanten == 2
    assert session.exec(select(func.count()).select_from(Artikel)).one() == 21  # 20 + Demo-Set

    sn_artikel = session.exec(
        select(Artikel).where(Artikel.seriennummernpflichtig == True)  # noqa: E712
    ).all()
    assert len(sn_artikel) == 5
    for artikel in sn_artikel:
        # 3 Stammdaten-SNs je SN-Artikel ("<nr>-SN000x"); Demo-Anlagen-SNs zählen extra
        stamm_sns = session.exec(
            select(Seriennummer).where(
                Seriennummer.artikel_id == artikel.id,
                Seriennummer.sn.like(f"{artikel.artikelnr}-SN%"),  # type: ignore[attr-defined]
            )
        ).all()
        assert len(stamm_sns) == 3
        bestand = session.exec(
            select(Lagerbestand).where(Lagerbestand.artikel_id == artikel.id)
        ).one()
        if artikel.artikelnr == "HZ-1001":
            # Demo-Durchlauf verkauft eine Gastherme
            assert bestand.menge == 2
            assert sum(sn.status == SnStatus.VERKAUFT for sn in stamm_sns) == 1
        else:
            assert bestand.menge == 3
            assert all(sn.status == SnStatus.AUF_LAGER for sn in stamm_sns)

    standardlager = session.exec(select(Lager).where(Lager.ist_standard == True)).all()  # noqa: E712
    assert len(standardlager) == 1


def test_seed_demo_durchlauf_legt_belegkette_und_anlagen_an(session):
    seed.run(session)

    # Angebot→Auftrag→Lieferschein→Rechnung = 4 Belege
    assert session.exec(select(func.count()).select_from(Beleg)).one() == 4
    # 2 Demo-Anlagen (eine fällig, eine überfällig)
    anlagen = session.exec(select(Anlage)).all()
    assert len(anlagen) == 2
    assert all(a.naechste_wartung is not None for a in anlagen)


def test_seed_ist_idempotent(session):
    seed.run(session)
    seed.run(session)
    assert session.exec(select(func.count()).select_from(Artikel)).one() == 21  # 20 + Demo-Set
    assert session.exec(select(func.count()).select_from(Benutzer)).one() == 2
    # 15 Stammdaten-SNs + 2 Demo-Anlagen-SNs, auch nach zweitem Lauf
    assert session.exec(select(func.count()).select_from(Seriennummer)).one() == 17
    assert session.exec(select(func.count()).select_from(Beleg)).one() == 4
    assert session.exec(select(func.count()).select_from(Anlage)).one() == 2
