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
    """Adressstamm im Kplus-Zuschnitt (Adressmanager).

    Drei Blöcke wie in Kplus: **Adresse** (Nummer, Matchcode, Fibu, Anrede,
    Titel, Name 1/2, Straße, Land/PLZ/Ort, Briefanrede), **Kommunikation**
    (Telefon 1/2, Mobil, Fax, E-Mail, Homepage) und **Zuordnung** (Adressart,
    Branche, Merkmale). Ansprechpartner und Lieferadressen sind eigene Tabellen;
    ``ansprechpartner`` hält denormalisiert den Namen des Hauptkontakts für
    Listen und Beleg-PDFs.
    """

    __tablename__ = "adresse"

    id: int | None = Field(default=None, primary_key=True)
    typ: AdressTyp = Field(default=AdressTyp.KUNDE, index=True)
    # Kplus: Adressnummer (automatisch vergeben, eindeutig), Matchcode = Kurzbezeichnung
    adressnr: str = Field(default="", index=True)
    matchcode: str = Field(default="", index=True)
    fibunummer: str = ""
    anrede: str = ""
    titel: str = ""
    firma: str = Field(index=True)  # Name 1
    name2: str = ""  # Name 2 (Zusatz, z. B. Abteilung oder zweiter Name)
    briefanrede: str = ""
    ansprechpartner: str = ""  # Anzeigename des Hauptkontakts (siehe Ansprechpartner)
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = "Österreich"
    uid: str = ""
    email: str = ""
    telefon: str = ""
    telefon2: str = ""
    mobil: str = ""
    fax: str = ""
    homepage: str = ""
    branche: str = ""
    merkmale: str = ""  # kommagetrennte Schlagworte (z. B. "Wartungsvertrag, Großkunde")
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

    @property
    def merkmal_liste(self) -> list[str]:
        return [m.strip() for m in self.merkmale.split(",") if m.strip()]


class Ansprechpartner(SQLModel, table=True):
    """Kontaktperson einer Adresse (Kplus-Reiter „Ansprechpartner")."""

    __tablename__ = "ansprechpartner"

    id: int | None = Field(default=None, primary_key=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    anrede: str = ""
    vorname: str = ""
    nachname: str = ""
    funktion: str = ""  # z. B. Geschäftsführer, Einkauf, Hausmeister
    abteilung: str = ""
    telefon: str = ""
    mobil: str = ""
    email: str = ""
    notiz: str = ""
    ist_hauptkontakt: bool = False
    aktiv: bool = True

    @property
    def anzeigename(self) -> str:
        teile = [self.anrede, self.vorname, self.nachname]
        return " ".join(t for t in teile if t).strip()


class Lieferadresse(SQLModel, table=True):
    """Abweichende Lieferadresse eines Kunden (Kplus „Lieferadressen")."""

    __tablename__ = "lieferadresse"

    id: int | None = Field(default=None, primary_key=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    bezeichnung: str = ""  # z. B. "Baustelle Nord", "Filiale Lienz"
    name: str = ""
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = "Österreich"
    notiz: str = ""

    @property
    def kurz(self) -> str:
        teile = [self.bezeichnung or self.name, f"{self.plz} {self.ort}".strip()]
        return " – ".join(t for t in teile if t)
