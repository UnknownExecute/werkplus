from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr


class AdressTyp(StrEnum):
    KUNDE = "kunde"
    LIEFERANT = "lieferant"
    BEIDE = "beide"


class Adresse(SQLModel, table=True):
    __tablename__ = "adresse"

    id: int | None = Field(default=None, primary_key=True)
    typ: AdressTyp = Field(default=AdressTyp.KUNDE, index=True)
    firma: str = Field(index=True)
    ansprechpartner: str = ""
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = "Österreich"
    uid: str = ""
    email: str = ""
    telefon: str = ""
    zahlungsziel_tage: int = 14
    rabattgruppe: str = ""  # vorbereitet, im MVP ohne Logik
    standard_rabatt_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    # Skonto-Vereinbarung mit diesem Kunden (0 % = kein Skonto); wird in neue
    # Belege vorbelegt.
    skonto_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    skonto_tage: int = 0
    notizen: str = ""
    aktiv: bool = True

    @property
    def ist_kunde(self) -> bool:
        return self.typ in (AdressTyp.KUNDE, AdressTyp.BEIDE)

    @property
    def ist_lieferant(self) -> bool:
        return self.typ in (AdressTyp.LIEFERANT, AdressTyp.BEIDE)
