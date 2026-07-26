"""Tests für die Wertebereichsprüfung in app/formparse.py.

Grundsatz: Nur eindeutig unsinnige Eingaben werden abgelehnt. Negative Beträge
und Mengen bleiben erlaubt — die Schlussrechnung hängt fachlich eine
Abzugsposition mit negativem Einzelpreis an (beleg_service.erzeuge_schlussrechnung).
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.formparse import parse_betrag, parse_menge, parse_prozent

# --- Prozentsätze (Rabatte: 0–100 %) ----------------------------------------


@pytest.mark.parametrize("text", ["0", "50", "100", "12,5"])
def test_prozent_im_bereich_ok(text):
    assert parse_prozent(text, "Rabatt 1") == Decimal(text.replace(",", "."))


@pytest.mark.parametrize("text", ["-1", "101", "1000", "-0,5"])
def test_prozent_ausserhalb_abgelehnt(text):
    with pytest.raises(HTTPException) as exc:
        parse_prozent(text, "Rabatt 1")
    assert exc.value.status_code == 422
    assert "Rabatt 1" in exc.value.detail


def test_prozent_eigener_bereich():
    """Aufschlag darf negativ sein (Verkauf unter EK) — eigener Bereich."""
    assert parse_prozent("-50", "Aufschlag", min_wert=Decimal("-100")) == Decimal("-50")
    with pytest.raises(HTTPException):
        parse_prozent("-150", "Aufschlag", min_wert=Decimal("-100"))


# --- Beträge -----------------------------------------------------------------


def test_negativer_betrag_erlaubt():
    """Abzugspositionen (Schlussrechnung) haben einen negativen Einzelpreis."""
    assert parse_betrag("-1234,56", "Einzelpreis") == Decimal("-1234.56")


def test_unplausibel_grosser_betrag_abgelehnt():
    with pytest.raises(HTTPException) as exc:
        parse_betrag("999999999999", "Einzelpreis")
    assert exc.value.status_code == 422
    assert "Einzelpreis" in exc.value.detail


def test_normaler_betrag_ok():
    assert parse_betrag("1.234,56", "Einzelpreis") == Decimal("1234.56")


# --- Mengen ------------------------------------------------------------------


def test_negative_menge_erlaubt():
    assert parse_menge("-3") == Decimal("-3")


def test_unplausibel_grosse_menge_abgelehnt():
    with pytest.raises(HTTPException) as exc:
        parse_menge("99999999")
    assert exc.value.status_code == 422


def test_normale_menge_ok():
    assert parse_menge("2,5") == Decimal("2.5")


# --- NaN-Schutz gilt weiterhin über alle Helfer -----------------------------


def test_nan_in_betrag_abgelehnt():
    with pytest.raises(HTTPException):
        parse_betrag("nan", "Einzelpreis")


def test_nan_in_menge_abgelehnt():
    with pytest.raises(HTTPException):
        parse_menge("nan")


def test_nan_in_prozent_abgelehnt():
    with pytest.raises(HTTPException):
        parse_prozent("nan", "Rabatt 1")
