from enum import StrEnum

from sqlmodel import Field, SQLModel


class TextbausteinKategorie(StrEnum):
    POSITION = "position"  # für Positions-Langtexte
    KOPF = "kopf"  # Belegkopftext
    FUSS = "fuss"  # Belegfußtext


class Textbaustein(SQLModel, table=True):
    """Wiederverwendbarer Standardtext (Position/Kopf/Fuß), per Kürzel einfügbar."""

    __tablename__ = "textbaustein"

    id: int | None = Field(default=None, primary_key=True)
    kuerzel: str = Field(default="", index=True)  # kurzer Code zum schnellen Finden
    titel: str = Field(default="", index=True)
    text: str = ""
    kategorie: TextbausteinKategorie = Field(default=TextbausteinKategorie.POSITION, index=True)
    aktiv: bool = True
