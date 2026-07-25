from enum import StrEnum

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class SnStatus(StrEnum):
    AUF_LAGER = "auf_lager"
    VERKAUFT = "verkauft"
    BEIM_KUNDEN_VERBAUT = "beim_kunden_verbaut"


class Seriennummer(SQLModel, table=True):
    __tablename__ = "seriennummer"
    __table_args__ = (UniqueConstraint("artikel_id", "sn", name="uq_sn_artikel"),)

    id: int | None = Field(default=None, primary_key=True)
    artikel_id: int = Field(foreign_key="artikel.id", index=True)
    sn: str = Field(index=True)
    status: SnStatus = Field(default=SnStatus.AUF_LAGER, index=True)
    lager_id: int | None = Field(default=None, foreign_key="lager.id")
    # Herkunft/Verbleib für Nachvollziehbarkeit
    wareneingang_position_id: int | None = Field(default=None, foreign_key="belegposition.id")
    lieferschein_position_id: int | None = Field(default=None, foreign_key="belegposition.id")
