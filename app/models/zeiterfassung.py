"""Arbeitszeit-/Regieerfassung je Auftrag und Mitarbeiter.

Kernfunktion jedes Handwerker-ERP: Monteurstunden auf einen Auftrag buchen und
später als Positionen in Lieferschein/Rechnung übernehmen. Der Stundensatz wird
beim Erfassen aus ``Benutzer.stundensatz`` vorbelegt, bleibt aber je Zeile
überschreibbar (Regie-, Notdienst- oder Gewährleistungssätze).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr


class Zeiterfassung(SQLModel, table=True):
    """Eine erfasste Arbeitszeit-Zeile (Stunden × Stundensatz) zu einem Beleg."""

    __tablename__ = "zeiterfassung"

    id: int | None = Field(default=None, primary_key=True)
    # Bezugsbeleg (in der Regel der Auftrag). Ohne Beleg = noch nicht zugeordnet.
    beleg_id: int | None = Field(default=None, foreign_key="beleg.id", index=True)
    benutzer_id: int = Field(foreign_key="benutzer.id", index=True)
    datum: date = Field(default_factory=date.today, index=True)
    stunden: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    stundensatz: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    taetigkeit: str = ""
    # True, sobald die Zeile als Belegposition übernommen wurde (verhindert
    # doppelte Verrechnung).
    verrechnet: bool = Field(default=False, index=True)
    erstellt_am: datetime = Field(default_factory=datetime.now)
