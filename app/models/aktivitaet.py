from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class AktivitaetTyp(StrEnum):
    NOTIZ = "notiz"
    TELEFONAT = "telefonat"
    EMAIL = "email"


class Aktivitaet(SQLModel, table=True):
    """Mini-CRM: Notiz/Telefonat/E-Mail zu einer Adresse."""

    __tablename__ = "aktivitaet"

    id: int | None = Field(default=None, primary_key=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    typ: AktivitaetTyp = AktivitaetTyp.NOTIZ
    betreff: str = ""
    text: str = ""
    benutzer_id: int | None = Field(default=None, foreign_key="benutzer.id")
    zeitstempel: datetime = Field(default_factory=datetime.now)
