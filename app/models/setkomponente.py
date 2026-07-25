from decimal import Decimal

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr


class SetKomponente(SQLModel, table=True):
    """Bestandteil eines Set-Artikels (Stückliste): Komponente + Menge je Set."""

    __tablename__ = "setkomponente"

    id: int | None = Field(default=None, primary_key=True)
    set_artikel_id: int = Field(foreign_key="artikel.id", index=True)
    komponente_artikel_id: int = Field(foreign_key="artikel.id", index=True)
    menge: Decimal = Field(default=Decimal("1"), sa_column=Column(DecimalStr, nullable=False))
    sort: int = 0  # Reihenfolge beim Einfügen
