from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr


class Lager(SQLModel, table=True):
    __tablename__ = "lager"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    ist_standard: bool = False


class Lagerbestand(SQLModel, table=True):
    __tablename__ = "lagerbestand"
    __table_args__ = (UniqueConstraint("artikel_id", "lager_id", name="uq_bestand_artikel_lager"),)

    id: int | None = Field(default=None, primary_key=True)
    artikel_id: int = Field(foreign_key="artikel.id", index=True)
    lager_id: int = Field(foreign_key="lager.id", index=True)
    menge: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))


class Buchungsart(StrEnum):
    WARENEINGANG = "wareneingang"
    LIEFERSCHEIN = "lieferschein"
    STORNO = "storno"
    KORREKTUR = "korrektur"


class Lagerbewegung(SQLModel, table=True):
    """Journal: jede Zu-/Abbuchung nachvollziehbar (Belegreferenz, Zeitstempel, Benutzer)."""

    __tablename__ = "lagerbewegung"

    id: int | None = Field(default=None, primary_key=True)
    artikel_id: int = Field(foreign_key="artikel.id", index=True)
    lager_id: int = Field(foreign_key="lager.id", index=True)
    menge: Decimal = Field(sa_column=Column(DecimalStr, nullable=False))  # + Zugang, − Abgang
    buchungsart: Buchungsart
    beleg_id: int | None = Field(default=None, foreign_key="beleg.id", index=True)
    position_id: int | None = Field(default=None, foreign_key="belegposition.id")
    seriennummer_id: int | None = Field(default=None, foreign_key="seriennummer.id")
    benutzer_id: int | None = Field(default=None, foreign_key="benutzer.id")
    zeitstempel: datetime = Field(default_factory=datetime.now)
    notiz: str = ""  # z. B. Grund einer manuellen Bestandskorrektur
