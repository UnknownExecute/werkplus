"""Tests der reinen Rechenfunktionen (Rundung, Rabatt, Summen je Steuersatz)."""

from decimal import Decimal

from app.models.beleg import BelegPosition
from app.services.berechnung import (
    beleg_marge,
    beleg_summen,
    parse_steuer_details,
    positions_netto,
    runde,
    steuer_details_json,
)


def _pos(menge: str, preis: str, rabatt: str = "0", ust: int = 20) -> BelegPosition:
    return BelegPosition(
        menge=Decimal(menge),
        einzelpreis=Decimal(preis),
        rabatt_prozent=Decimal(rabatt),
        ust_satz=ust,
    )


class TestRunde:
    def test_kaufmaennisch_halb_aufrunden(self):
        assert runde(Decimal("0.005")) == Decimal("0.01")
        assert runde(Decimal("1.115")) == Decimal("1.12")
        assert runde(Decimal("2.675")) == Decimal("2.68")

    def test_abrunden_unter_halb(self):
        assert runde(Decimal("0.004")) == Decimal("0.00")
        assert runde(Decimal("1.114")) == Decimal("1.11")

    def test_zwei_nachkommastellen(self):
        assert str(runde(Decimal("5"))) == "5.00"
        assert str(runde(Decimal("5.1"))) == "5.10"


class TestPositionsNetto:
    def test_ohne_rabatt(self):
        assert positions_netto(Decimal("3"), Decimal("19.90"), Decimal("0")) == Decimal("59.70")

    def test_mit_rabatt(self):
        # 3 * 19.90 = 59.70; -10 % = 53.73
        assert positions_netto(Decimal("3"), Decimal("19.90"), Decimal("10")) == Decimal("53.73")

    def test_rundung_der_position(self):
        # 1.5 * 0.99 = 1.485 -> 1.49 (HALF_UP)
        assert positions_netto(Decimal("1.5"), Decimal("0.99"), Decimal("0")) == Decimal("1.49")

    def test_krummer_rabatt(self):
        # 1 * 100 * (1 - 0.335) = 66.50
        assert positions_netto(Decimal("1"), Decimal("100"), Decimal("33.5")) == Decimal("66.50")


class TestBelegSummen:
    def test_leerer_beleg(self):
        summen = beleg_summen([])
        assert summen.netto_gesamt == Decimal("0.00")
        assert summen.ust_gesamt == Decimal("0.00")
        assert summen.brutto_gesamt == Decimal("0.00")
        assert summen.je_steuersatz == {}

    def test_ust_auf_summenbasis_nicht_je_position(self):
        # Zwei Positionen à 0.33 netto (20 %):
        # je Position gerundet wäre 0.07 + 0.07 = 0.14,
        # auf Summenbasis: 0.66 * 20 % = 0.132 -> 0.13.
        summen = beleg_summen([_pos("1", "0.33"), _pos("1", "0.33")])
        assert summen.je_steuersatz[20].netto == Decimal("0.66")
        assert summen.je_steuersatz[20].ust == Decimal("0.13")
        assert summen.ust_gesamt == Decimal("0.13")
        assert summen.brutto_gesamt == Decimal("0.79")

    def test_gemischte_steuersaetze(self):
        positionen = [
            _pos("2", "50.00", ust=20),  # netto 100.00 -> USt 20.00
            _pos("1", "55.55", ust=10),  # netto  55.55 -> USt 5.555 -> 5.56
            _pos("1", "33.33", ust=13),  # netto  33.33 -> USt 4.3329 -> 4.33
        ]
        summen = beleg_summen(positionen)
        assert summen.je_steuersatz[20].netto == Decimal("100.00")
        assert summen.je_steuersatz[20].ust == Decimal("20.00")
        assert summen.je_steuersatz[10].netto == Decimal("55.55")
        assert summen.je_steuersatz[10].ust == Decimal("5.56")
        assert summen.je_steuersatz[13].netto == Decimal("33.33")
        assert summen.je_steuersatz[13].ust == Decimal("4.33")
        assert summen.netto_gesamt == Decimal("188.88")
        assert summen.ust_gesamt == Decimal("29.89")
        assert summen.brutto_gesamt == Decimal("218.77")

    def test_rabatt_fliesst_in_summen(self):
        # 2 * 100 = 200; -5 % = 190.00; USt 38.00
        summen = beleg_summen([_pos("2", "100.00", rabatt="5")])
        assert summen.netto_gesamt == Decimal("190.00")
        assert summen.ust_gesamt == Decimal("38.00")
        assert summen.brutto_gesamt == Decimal("228.00")


def _pos_marge(menge: str, ek: str, vk: str, rabatt: str = "0") -> BelegPosition:
    return BelegPosition(
        menge=Decimal(menge),
        ek_preis=Decimal(ek),
        einzelpreis=Decimal(vk),
        rabatt_prozent=Decimal(rabatt),
        ust_satz=20,
    )


class TestBelegMarge:
    def test_leerer_beleg(self):
        marge = beleg_marge([])
        assert marge.ek_gesamt == Decimal("0.00")
        assert marge.vk_netto_gesamt == Decimal("0.00")
        assert marge.deckungsbeitrag == Decimal("0.00")
        assert marge.marge_prozent == Decimal("0.00")  # VK 0 -> keine Division

    def test_zwei_positionen(self):
        # Pos1: 2 * EK 60 = 120 EK; 2 * VK 100 = 200 VK-Netto
        # Pos2: 1 * EK 30 = 30 EK;  1 * VK 50  = 50 VK-Netto
        marge = beleg_marge([_pos_marge("2", "60", "100"), _pos_marge("1", "30", "50")])
        assert marge.ek_gesamt == Decimal("150.00")
        assert marge.vk_netto_gesamt == Decimal("250.00")
        assert marge.deckungsbeitrag == Decimal("100.00")
        assert marge.marge_prozent == Decimal("40.00")  # 100 / 250 * 100

    def test_mit_positionsrabatt(self):
        # 1 * EK 80 = 80 EK; 1 * VK 100, -20 % = 80.00 VK-Netto -> DB 0, Marge 0
        marge = beleg_marge([_pos_marge("1", "80", "100", rabatt="20")])
        assert marge.ek_gesamt == Decimal("80.00")
        assert marge.vk_netto_gesamt == Decimal("80.00")
        assert marge.deckungsbeitrag == Decimal("0.00")
        assert marge.marge_prozent == Decimal("0.00")


class TestSteuerDetails:
    def test_roundtrip(self):
        summen = beleg_summen([_pos("1", "100.00", ust=20), _pos("1", "50.00", ust=10)])
        text = steuer_details_json(summen)
        gelesen = parse_steuer_details(text)
        assert gelesen == summen.je_steuersatz
        assert gelesen[20].netto == Decimal("100.00")
        assert gelesen[10].ust == Decimal("5.00")

    def test_leer(self):
        assert parse_steuer_details("{}") == {}
        assert parse_steuer_details("") == {}
