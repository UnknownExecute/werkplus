"""Zahlungen zu einem Beleg — ermöglicht Teilzahlungen und Skontoabzug.

Bisher war der Zahlungsstatus binär (``Beleg.bezahlt_am``). Mit einzelnen
Zahlungssätzen wird „bezahlt" **abgeleitet**: offen = Bruttosumme − Σ Zahlungen.
``Beleg.bezahlt_am`` bleibt als schneller Filter/Anzeige erhalten und wird vom
Zahlungsservice gepflegt (gesetzt, sobald nichts mehr offen ist).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr


class Zahlungsart(StrEnum):
    UEBERWEISUNG = "ueberweisung"
    BAR = "bar"
    KARTE = "karte"
    SONSTIGE = "sonstige"


class Zahlung(SQLModel, table=True):
    """Ein Zahlungseingang (oder -ausgang bei EK-Rechnungen) zu einem Beleg."""

    __tablename__ = "zahlung"

    id: int | None = Field(default=None, primary_key=True)
    beleg_id: int = Field(foreign_key="beleg.id", index=True)
    datum: date = Field(default_factory=date.today, index=True)
    # Zahlbetrag brutto. Negativ zulässig (Rückzahlung/Korrektur einer Zahlung).
    betrag: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    art: Zahlungsart = Field(default=Zahlungsart.UEBERWEISUNG, index=True)
    # Gewährter Skontobetrag, der zusammen mit dieser Zahlung als erledigt gilt.
    skonto_betrag: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    notiz: str = ""
    erstellt_von: int | None = Field(default=None, foreign_key="benutzer.id")
    erstellt_am: datetime = Field(default_factory=datetime.now)
