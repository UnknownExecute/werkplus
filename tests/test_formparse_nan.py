"""Tests für den NaN/Infinity-Schutz in parse_decimal.

``Decimal("nan")`` ist gültig und wirft keine ``InvalidOperation`` — ohne
Endlichkeitsprüfung würde ein manipulierter POST (menge=nan) Beträge und
Lagerbestände dauerhaft verderben (als TEXT "NaN" gespeichert).
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.formparse import parse_decimal


@pytest.mark.parametrize("text", ["nan", "NaN", "-nan", "sNaN", "Infinity", "-Infinity", "inf"])
def test_nicht_endliche_werte_abgelehnt(text):
    with pytest.raises(HTTPException) as exc:
        parse_decimal(text)
    assert exc.value.status_code == 422


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("1.234,56", Decimal("1234.56")),
        ("12.50", Decimal("12.50")),
        ("100", Decimal("100")),
        ("", Decimal("0")),
        (None, Decimal("0")),
        ("-5,5", Decimal("-5.5")),
    ],
)
def test_gueltige_werte_unveraendert(text, erwartet):
    """Gegenprobe: die bestehende Formatlogik bleibt unangetastet."""
    assert parse_decimal(text) == erwartet


def test_unsinn_weiterhin_422():
    with pytest.raises(HTTPException) as exc:
        parse_decimal("abc")
    assert exc.value.status_code == 422
