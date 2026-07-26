from datetime import date, datetime

from sqlmodel import Field, SQLModel


class Anlage(SQLModel, table=True):
    """Beim Kunden installiertes Gerät (Seriennummer ist Pflicht)."""

    __tablename__ = "anlage"

    id: int | None = Field(default=None, primary_key=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    artikel_id: int = Field(foreign_key="artikel.id", index=True)
    seriennummer_id: int = Field(foreign_key="seriennummer.id", unique=True, index=True)
    standort: str = ""  # Standort/Einbauort beim Kunden
    inbetriebnahme: date | None = None
    garantieende: date | None = None
    naechste_wartung: date | None = Field(default=None, index=True)
    # Wartungsvertrag: Intervall in Monaten (0 = kein Vertrag/kein Intervall).
    # Ist ein Intervall gesetzt, schreibt der Serviceeinsatz die nächste Wartung
    # automatisch fort — bisher musste das Datum manuell gepflegt werden.
    wartungsintervall_monate: int = 0
    vertrag_bis: date | None = None
    notizen: str = ""
    aktiv: bool = True


class Serviceeinsatz(SQLModel, table=True):
    """Historie der Serviceeinsätze zu einer Anlage."""

    __tablename__ = "serviceeinsatz"

    id: int | None = Field(default=None, primary_key=True)
    anlage_id: int = Field(foreign_key="anlage.id", index=True)
    datum: date = Field(default_factory=date.today)
    beschreibung: str = ""
    benutzer_id: int | None = Field(default=None, foreign_key="benutzer.id")
    erstellt_am: datetime = Field(default_factory=datetime.now)
