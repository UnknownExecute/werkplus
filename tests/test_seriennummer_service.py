"""Tests des Seriennummern-Services: Wareneingangs-Erfassung und Lieferschein-Zuweisung."""

from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.adresse import Adresse
from app.models.artikel import Artikel
from app.models.beleg import Belegart, BelegPositionSeriennummer
from app.models.lager import Lager
from app.models.seriennummer import Seriennummer, SnStatus
from app.services.beleg_service import erstelle_beleg, fuege_position_hinzu
from app.services.fehler import SnNichtVerfuegbar, UngueltigeBelegart
from app.services.seriennummer_service import (
    erfasse_wareneingang_sns,
    sns_je_position,
    verfuegbare_sns,
    weise_sns_zu,
)


@pytest.fixture
def adresse(session) -> Adresse:
    adresse = Adresse(firma="Muster GmbH")
    session.add(adresse)
    session.flush()
    return adresse


@pytest.fixture
def lager(session) -> Lager:
    lager = Lager(name="Hauptlager", ist_standard=True)
    session.add(lager)
    session.flush()
    return lager


@pytest.fixture
def sn_artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="WP-8",
        bezeichnung="Wärmepumpe 8 kW",
        seriennummernpflichtig=True,
        vk_preis=Decimal("5000.00"),
    )
    session.add(artikel)
    session.flush()
    return artikel


@pytest.fixture
def anderer_artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="WP-12",
        bezeichnung="Wärmepumpe 12 kW",
        seriennummernpflichtig=True,
        vk_preis=Decimal("7000.00"),
    )
    session.add(artikel)
    session.flush()
    return artikel


def _ek_position(session, adresse, artikel, menge=Decimal("3")):
    beleg = erstelle_beleg(session, Belegart.EK_LIEFERSCHEIN, adresse)
    return beleg, fuege_position_hinzu(session, beleg, artikel, menge=menge)


def _ls_position(session, adresse, artikel, menge=Decimal("2")):
    beleg = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
    return beleg, fuege_position_hinzu(session, beleg, artikel, menge=menge)


def _verfuegbare_sn(session, artikel, lager, sn_text) -> Seriennummer:
    """Legt eine eingebuchte (= verfügbare) Seriennummer direkt an."""
    sn = Seriennummer(
        artikel_id=artikel.id, sn=sn_text, status=SnStatus.AUF_LAGER, lager_id=lager.id
    )
    session.add(sn)
    session.flush()
    return sn


class TestErfasseWareneingangSns:
    def test_legt_sns_und_zuordnung_an(self, session, adresse, sn_artikel):
        _, position = _ek_position(session, adresse, sn_artikel)
        neue = erfasse_wareneingang_sns(session, position, ["SN-3", "SN-1", "SN-2"])
        assert len(neue) == 3
        for sn in neue:
            assert sn.artikel_id == sn_artikel.id
            assert sn.status == SnStatus.AUF_LAGER
            assert sn.lager_id is None  # noch nicht eingebucht
            assert sn.wareneingang_position_id == position.id
        zuordnungen = session.exec(
            select(BelegPositionSeriennummer).where(
                BelegPositionSeriennummer.position_id == position.id
            )
        ).all()
        assert len(zuordnungen) == 3
        # Nicht eingebucht = nicht verfügbar
        assert verfuegbare_sns(session, sn_artikel.id) == []

    def test_ersetzt_fruehere_erfassung(self, session, adresse, sn_artikel):
        _, position = _ek_position(session, adresse, sn_artikel)
        erfasse_wareneingang_sns(session, position, ["ALT-1", "ALT-2", "ALT-3"])
        erfasse_wareneingang_sns(session, position, ["NEU-1", "NEU-2", "NEU-3"])
        alle = session.exec(
            select(Seriennummer).where(Seriennummer.artikel_id == sn_artikel.id)
        ).all()
        assert sorted(sn.sn for sn in alle) == ["NEU-1", "NEU-2", "NEU-3"]
        zuordnungen = session.exec(
            select(BelegPositionSeriennummer).where(
                BelegPositionSeriennummer.position_id == position.id
            )
        ).all()
        assert len(zuordnungen) == 3

    def test_gleiche_sns_erneut_erfassen_erlaubt(self, session, adresse, sn_artikel):
        _, position = _ek_position(session, adresse, sn_artikel)
        erfasse_wareneingang_sns(session, position, ["SN-1", "SN-2", "SN-3"])
        # Ersetzen mit denselben Strings darf nicht an der Duplikatprüfung scheitern
        neue = erfasse_wareneingang_sns(session, position, ["SN-1", "SN-2", "SN-3"])
        assert len(neue) == 3

    def test_leerer_string_abgelehnt(self, session, adresse, sn_artikel):
        _, position = _ek_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            erfasse_wareneingang_sns(session, position, ["SN-1", "  ", "SN-3"])

    def test_duplikat_in_liste_abgelehnt(self, session, adresse, sn_artikel):
        _, position = _ek_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            erfasse_wareneingang_sns(session, position, ["SN-1", "SN-1", "SN-2"])

    def test_bereits_existierende_sn_abgelehnt(self, session, adresse, sn_artikel, lager):
        _verfuegbare_sn(session, sn_artikel, lager, "SN-1")
        _, position = _ek_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            erfasse_wareneingang_sns(session, position, ["SN-1", "SN-2", "SN-3"])

    def test_nur_auf_ek_lieferschein(self, session, adresse, sn_artikel):
        _, position = _ls_position(session, adresse, sn_artikel)
        with pytest.raises(UngueltigeBelegart):
            erfasse_wareneingang_sns(session, position, ["SN-1"])


class TestWeiseSnsZu:
    def test_zuweisung_angelegt(self, session, adresse, sn_artikel, lager):
        sn1 = _verfuegbare_sn(session, sn_artikel, lager, "SN-1")
        sn2 = _verfuegbare_sn(session, sn_artikel, lager, "SN-2")
        _, position = _ls_position(session, adresse, sn_artikel)
        weise_sns_zu(session, position, [sn1.id, sn2.id])
        zugeordnet = sns_je_position(session, [position.id])[position.id]
        assert [sn.sn for sn in zugeordnet] == ["SN-1", "SN-2"]

    def test_ersetzt_bestehende_zuordnung(self, session, adresse, sn_artikel, lager):
        sn_a = _verfuegbare_sn(session, sn_artikel, lager, "SN-A")
        sn_b = _verfuegbare_sn(session, sn_artikel, lager, "SN-B")
        sn_c = _verfuegbare_sn(session, sn_artikel, lager, "SN-C")
        _, position = _ls_position(session, adresse, sn_artikel)
        weise_sns_zu(session, position, [sn_a.id, sn_b.id])
        weise_sns_zu(session, position, [sn_b.id, sn_c.id])
        zugeordnet = sns_je_position(session, [position.id])[position.id]
        assert [sn.sn for sn in zugeordnet] == ["SN-B", "SN-C"]

    def test_fremder_artikel_abgelehnt(
        self, session, adresse, sn_artikel, anderer_artikel, lager
    ):
        fremde_sn = _verfuegbare_sn(session, anderer_artikel, lager, "SN-X")
        _, position = _ls_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            weise_sns_zu(session, position, [fremde_sn.id])

    def test_nicht_eingebuchte_sn_abgelehnt(self, session, adresse, sn_artikel, lager):
        sn = Seriennummer(
            artikel_id=sn_artikel.id, sn="SN-OFFEN", status=SnStatus.AUF_LAGER, lager_id=None
        )
        session.add(sn)
        session.flush()
        _, position = _ls_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            weise_sns_zu(session, position, [sn.id])

    def test_verkaufte_sn_abgelehnt(self, session, adresse, sn_artikel, lager):
        sn = _verfuegbare_sn(session, sn_artikel, lager, "SN-WEG")
        sn.status = SnStatus.VERKAUFT
        session.add(sn)
        session.flush()
        _, position = _ls_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            weise_sns_zu(session, position, [sn.id])

    def test_doppelzuordnung_an_zweite_ls_position_abgelehnt(
        self, session, adresse, sn_artikel, lager
    ):
        sn = _verfuegbare_sn(session, sn_artikel, lager, "SN-1")
        _, position1 = _ls_position(session, adresse, sn_artikel, menge=Decimal("1"))
        _, position2 = _ls_position(session, adresse, sn_artikel, menge=Decimal("1"))
        weise_sns_zu(session, position1, [sn.id])
        with pytest.raises(SnNichtVerfuegbar):
            weise_sns_zu(session, position2, [sn.id])

    def test_doppelte_id_in_zuweisung_abgelehnt(self, session, adresse, sn_artikel, lager):
        sn = _verfuegbare_sn(session, sn_artikel, lager, "SN-1")
        _, position = _ls_position(session, adresse, sn_artikel)
        with pytest.raises(SnNichtVerfuegbar):
            weise_sns_zu(session, position, [sn.id, sn.id])

    def test_nur_auf_lieferschein(self, session, adresse, sn_artikel, lager):
        sn = _verfuegbare_sn(session, sn_artikel, lager, "SN-1")
        _, position = _ek_position(session, adresse, sn_artikel)
        with pytest.raises(UngueltigeBelegart):
            weise_sns_zu(session, position, [sn.id])


class TestVerfuegbareSns:
    def test_nur_eingebuchte_auf_lager_sortiert(self, session, sn_artikel, lager):
        _verfuegbare_sn(session, sn_artikel, lager, "SN-B")
        _verfuegbare_sn(session, sn_artikel, lager, "SN-A")
        verkauft = _verfuegbare_sn(session, sn_artikel, lager, "SN-C")
        verkauft.status = SnStatus.VERKAUFT
        session.add(verkauft)
        session.add(
            Seriennummer(artikel_id=sn_artikel.id, sn="SN-D", lager_id=None)
        )
        session.flush()
        assert [sn.sn for sn in verfuegbare_sns(session, sn_artikel.id)] == ["SN-A", "SN-B"]


class TestSnsJePosition:
    def test_leere_liste_und_position_ohne_sns(self, session, adresse, sn_artikel):
        assert sns_je_position(session, []) == {}
        _, position = _ls_position(session, adresse, sn_artikel)
        assert sns_je_position(session, [position.id]) == {position.id: []}
