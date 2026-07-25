from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import (
    Adresse,
    AdressTyp,
    Artikel,
    Lager,
    Lagerbestand,
    Seriennummer,
    SnStatus,
)


def test_decimal_roundtrip(session):
    """Preise werden verlustfrei als Decimal gespeichert und gelesen."""
    artikel = Artikel(
        artikelnr="T-1", bezeichnung="Test", ek_preis=Decimal("1.10"), vk_preis=Decimal("2.37")
    )
    session.add(artikel)
    session.commit()
    session.expunge_all()

    geladen = session.exec(select(Artikel).where(Artikel.artikelnr == "T-1")).one()
    assert geladen.vk_preis == Decimal("2.37")
    assert isinstance(geladen.vk_preis, Decimal)


def test_artikelnr_unique(session):
    session.add(Artikel(artikelnr="T-1", bezeichnung="A"))
    session.commit()
    session.add(Artikel(artikelnr="T-1", bezeichnung="B"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_seriennummer_unique_je_artikel(session):
    a1 = Artikel(artikelnr="T-1", bezeichnung="A", seriennummernpflichtig=True)
    a2 = Artikel(artikelnr="T-2", bezeichnung="B", seriennummernpflichtig=True)
    session.add_all([a1, a2])
    session.commit()

    session.add(Seriennummer(artikel_id=a1.id, sn="SN-1"))
    # gleiche SN bei anderem Artikel ist erlaubt
    session.add(Seriennummer(artikel_id=a2.id, sn="SN-1"))
    session.commit()

    session.add(Seriennummer(artikel_id=a1.id, sn="SN-1"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_seriennummer_default_status(session):
    artikel = Artikel(artikelnr="T-1", bezeichnung="A", seriennummernpflichtig=True)
    session.add(artikel)
    session.commit()
    sn = Seriennummer(artikel_id=artikel.id, sn="X-1")
    session.add(sn)
    session.commit()
    assert sn.status == SnStatus.AUF_LAGER


def test_lagerbestand_unique_je_artikel_und_lager(session):
    artikel = Artikel(artikelnr="T-1", bezeichnung="A")
    lager = Lager(name="Haupt", ist_standard=True)
    session.add_all([artikel, lager])
    session.commit()

    session.add(Lagerbestand(artikel_id=artikel.id, lager_id=lager.id, menge=Decimal("5")))
    session.commit()
    session.add(Lagerbestand(artikel_id=artikel.id, lager_id=lager.id, menge=Decimal("1")))
    with pytest.raises(IntegrityError):
        session.commit()


def test_adresse_typ_properties(session):
    kunde = Adresse(typ=AdressTyp.KUNDE, firma="K")
    beide = Adresse(typ=AdressTyp.BEIDE, firma="B")
    assert kunde.ist_kunde and not kunde.ist_lieferant
    assert beide.ist_kunde and beide.ist_lieferant
