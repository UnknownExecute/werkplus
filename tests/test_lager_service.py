"""Tests des Lager-Services: Bestand-Upsert, Journal, Summierung, negative Bestände."""

from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.artikel import Artikel
from app.models.lager import Buchungsart, Lager, Lagerbestand, Lagerbewegung
from app.services.fehler import WerkplusFehler
from app.services.lager_service import bestand, buche, standard_lager


@pytest.fixture
def lager(session) -> Lager:
    lager = Lager(name="Hauptlager", ist_standard=True)
    session.add(lager)
    session.flush()
    return lager


@pytest.fixture
def zweitlager(session) -> Lager:
    lager = Lager(name="Außenlager", ist_standard=False)
    session.add(lager)
    session.flush()
    return lager


@pytest.fixture
def artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="A-200",
        bezeichnung="Kupferrohr 22mm",
        einheit="m",
        ek_preis=Decimal("3.50"),
        vk_preis=Decimal("7.00"),
    )
    session.add(artikel)
    session.flush()
    return artikel


class TestStandardLager:
    def test_liefert_standardlager(self, session, lager, zweitlager):
        assert standard_lager(session).id == lager.id

    def test_fehler_ohne_standardlager(self, session, zweitlager):
        with pytest.raises(WerkplusFehler):
            standard_lager(session)


class TestBuche:
    def test_legt_bestand_und_journal_an(self, session, lager, artikel):
        bewegung = buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("5"),
            buchungsart=Buchungsart.WARENEINGANG,
        )
        assert bewegung.id is not None
        assert bewegung.menge == Decimal("5")
        assert bewegung.zeitstempel is not None
        zeile = session.exec(
            select(Lagerbestand).where(Lagerbestand.artikel_id == artikel.id)
        ).one()
        assert zeile.menge == Decimal("5")

    def test_upsertet_bestand_und_journalisiert_jede_buchung(self, session, lager, artikel):
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("5"),
            buchungsart=Buchungsart.WARENEINGANG,
        )
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("-2"),
            buchungsart=Buchungsart.LIEFERSCHEIN,
        )
        zeilen = session.exec(
            select(Lagerbestand).where(Lagerbestand.artikel_id == artikel.id)
        ).all()
        assert len(zeilen) == 1  # Upsert: genau ein Bestandsdatensatz je Artikel+Lager
        assert zeilen[0].menge == Decimal("3")
        journal = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.artikel_id == artikel.id)
        ).all()
        assert len(journal) == 2

    def test_notiz_wird_gespeichert(self, session, lager, artikel):
        bewegung = buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("2"),
            buchungsart=Buchungsart.KORREKTUR,
            notiz="Inventurdifferenz",
        )
        assert bewegung.notiz == "Inventurdifferenz"
        gespeichert = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.artikel_id == artikel.id)
        ).one()
        assert gespeichert.notiz == "Inventurdifferenz"

    def test_notiz_default_leer(self, session, lager, artikel):
        bewegung = buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("1"),
            buchungsart=Buchungsart.WARENEINGANG,
        )
        assert bewegung.notiz == ""

    def test_negativer_bestand_moeglich(self, session, lager, artikel):
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("-7"),
            buchungsart=Buchungsart.LIEFERSCHEIN,
        )
        assert bestand(session, artikel.id) == Decimal("-7")


class TestBestand:
    def test_null_ohne_datensatz(self, session, artikel):
        assert bestand(session, artikel.id) == Decimal("0")

    def test_summiert_ueber_laeger_und_filtert_je_lager(
        self, session, lager, zweitlager, artikel
    ):
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("4"),
            buchungsart=Buchungsart.WARENEINGANG,
        )
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=zweitlager.id,
            menge=Decimal("2.5"),
            buchungsart=Buchungsart.KORREKTUR,
        )
        assert bestand(session, artikel.id) == Decimal("6.5")
        assert bestand(session, artikel.id, lager_id=lager.id) == Decimal("4")
        assert bestand(session, artikel.id, lager_id=zweitlager.id) == Decimal("2.5")
