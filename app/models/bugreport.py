from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class MeldungTyp(StrEnum):
    BUG = "bug"
    FEATURE = "feature"


class BugReport(SQLModel, table=True):
    """Bug-/Feature-Meldung aus der App. Wird lokal gespeichert und (wenn ein
    GitHub-Token hinterlegt ist) als GitHub-Issue angelegt."""

    __tablename__ = "bugreport"

    id: int | None = Field(default=None, primary_key=True)
    typ: MeldungTyp = Field(default=MeldungTyp.BUG, index=True)
    titel: str = ""
    beschreibung: str = ""
    screenshot_datei: str = ""  # Dateiname unter static/bugreports/, leer = keiner
    seite: str = ""  # URL/Pfad, von der gemeldet wurde
    browser: str = ""  # User-Agent
    melder_id: int | None = Field(default=None, foreign_key="benutzer.id")
    melder_name: str = ""
    erstellt_am: datetime = Field(default_factory=datetime.now)
    # GitHub-Sync
    gesendet: bool = Field(default=False, index=True)
    github_issue_nr: int | None = None
    github_url: str = ""
    fehler: str = ""  # letzte Fehlermeldung, falls Senden misslang
