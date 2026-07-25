from enum import StrEnum

from sqlmodel import Field, SQLModel


class Rolle(StrEnum):
    """Namen der zwei eingebauten Standardrollen (weitere sind frei anlegbar)."""

    ADMIN = "admin"  # Vollzugriff
    SACHBEARBEITER = "sachbearbeiter"  # alles Operative, keine Einstellungen/Benutzer


class Benutzer(SQLModel, table=True):
    __tablename__ = "benutzer"

    id: int | None = Field(default=None, primary_key=True)
    login: str = Field(unique=True, index=True)
    name: str
    passwort_hash: str
    # Rollenname; verweist auf Rollenprofil.name (StrEnum-Werte bleiben gültig)
    rolle: str = Rolle.SACHBEARBEITER
    aktiv: bool = True
