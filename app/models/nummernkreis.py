from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Nummernkreis(SQLModel, table=True):
    """Fortlaufende Belegnummern je Belegart und Jahr, z. B. RE-2026-0001."""

    __tablename__ = "nummernkreis"
    __table_args__ = (UniqueConstraint("belegart", "jahr", name="uq_nummernkreis_art_jahr"),)

    id: int | None = Field(default=None, primary_key=True)
    belegart: str = Field(index=True)
    jahr: int
    praefix: str
    letzte_nummer: int = 0
