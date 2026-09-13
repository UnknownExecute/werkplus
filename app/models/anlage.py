from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr

ANLAGE_ARTEN = (
    "", "Heizung", "Klima", "Lüftung", "Elektro", "Sanitär", "Kompressor", "Stromerzeuger",
    "Tank", "Sonstiges",
)


class Anlage(SQLModel, table=True):
    """Beim Kunden installierte Anlage (Kplus „Anlagenstamm").

    Kplus-Zuschnitt: Anlagenummer, Anlagebezeichnung, Kostenstelle, Hersteller,
    Typ, Seriennummer (Text), Kunde, Ansprechpartner, Lieferant, Herstellungs-/
    Installationsdatum, Garantie bis, Standort, Technische Information, Art,
    Anlagengruppe, Untergruppe — plus Wartung (Intervall, nächster Termin) und
    Wartungsvertrag. Artikel und Seriennummer aus dem Lager sind **optional**:
    Altanlagen fremder Herkunft werden frei erfasst.
    """

    __tablename__ = "anlage"

    id: int | None = Field(default=None, primary_key=True)
    anlagenr: str = Field(default="", index=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    artikel_id: int | None = Field(default=None, foreign_key="artikel.id", index=True)
    seriennummer_id: int | None = Field(
        default=None, foreign_key="seriennummer.id", unique=True, index=True
    )
    bezeichnung: str = Field(default="", index=True)  # Anlagebezeichnung (mehrzeilig)
    kostenstelle: str = ""
    hersteller: str = Field(default="", index=True)
    typ: str = ""
    seriennummer_text: str = Field(default="", index=True)  # SN als Text (auch fremde Geräte)
    ansprechpartner_id: int | None = Field(default=None, index=True)
    lieferant_id: int | None = Field(default=None, index=True)
    herstellungsdatum: date | None = None
    standort: str = ""  # Standort/Einbauort beim Kunden
    technische_info: str = ""
    art: str = Field(default="", index=True)
    anlagengruppe: str = Field(default="", index=True)
    untergruppe: str = ""
    inbetriebnahme: date | None = None  # Installationsdatum
    garantieende: date | None = None
    naechste_wartung: date | None = Field(default=None, index=True)
    # Wartungsvertrag: Intervall in Monaten (0 = kein Vertrag/kein Intervall).
    # Ist ein Intervall gesetzt, schreibt der Serviceeinsatz die nächste Wartung
    # automatisch fort — bisher musste das Datum manuell gepflegt werden.
    wartungsintervall_monate: int = 0
    vertrag_nr: str = ""
    vertrag_von: date | None = None
    vertrag_bis: date | None = None
    vertrag_pauschale: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    vertrag_notiz: str = ""
    notizen: str = ""
    aktiv: bool = True

    @property
    def hat_wartungsvertrag(self) -> bool:
        return bool(self.vertrag_nr or self.vertrag_bis or self.vertrag_pauschale > 0)

    @property
    def kurz(self) -> str:
        teile = [self.bezeichnung.splitlines()[0] if self.bezeichnung else "", self.typ]
        return " ".join(t for t in teile if t).strip() or f"Anlage {self.anlagenr}"


class AnlageKomponente(SQLModel, table=True):
    """Bauteil einer Anlage (Kplus-Reiter „Komponenten"), z. B. Brenner, Pumpe, Speicher."""

    __tablename__ = "anlage_komponente"

    id: int | None = Field(default=None, primary_key=True)
    anlage_id: int = Field(foreign_key="anlage.id", index=True)
    bezeichnung: str = ""
    artikel_id: int | None = Field(default=None, index=True)
    hersteller: str = ""
    typ: str = ""
    seriennummer_text: str = ""
    einbaudatum: date | None = None
    notiz: str = ""
    sort: int = 0


class Serviceeinsatz(SQLModel, table=True):
    """Historie der Serviceeinsätze zu einer Anlage."""

    __tablename__ = "serviceeinsatz"

    id: int | None = Field(default=None, primary_key=True)
    anlage_id: int = Field(foreign_key="anlage.id", index=True)
    datum: date = Field(default_factory=date.today)
    beschreibung: str = ""
    benutzer_id: int | None = Field(default=None, foreign_key="benutzer.id")
    erstellt_am: datetime = Field(default_factory=datetime.now)
