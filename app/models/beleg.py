from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr


class Belegart(StrEnum):
    # Verkauf
    ANGEBOT = "angebot"
    AUFTRAG = "auftrag"
    LIEFERSCHEIN = "lieferschein"
    RECHNUNG = "rechnung"
    GUTSCHRIFT = "gutschrift"
    # Einkauf
    BESTELLUNG = "bestellung"
    EK_LIEFERSCHEIN = "ek_lieferschein"
    EK_RECHNUNG = "ek_rechnung"


class BelegStatus(StrEnum):
    ENTWURF = "entwurf"
    GEDRUCKT = "gedruckt"  # gedruckt/versendet
    ERLEDIGT = "erledigt"
    STORNIERT = "storniert"


class Beleg(SQLModel, table=True):
    """Belegkopf — eine Tabelle für alle Belegarten (Discriminator: belegart)."""

    __tablename__ = "beleg"

    id: int | None = Field(default=None, primary_key=True)
    belegart: Belegart = Field(index=True)
    belegnr: str = Field(unique=True, index=True)
    status: BelegStatus = Field(default=BelegStatus.ENTWURF, index=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    datum: date = Field(default_factory=date.today)
    leistungsdatum_von: date | None = None
    leistungsdatum_bis: date | None = None
    vorgaenger_beleg_id: int | None = Field(default=None, foreign_key="beleg.id", index=True)
    zahlungsziel_tage: int = 14
    kopftext: str = ""
    fusstext: str = ""
    # Summen (werden bei jeder Änderung neu berechnet, beim Druck eingefroren)
    summe_netto: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    summe_ust: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    summe_brutto: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    # Aufschlüsselung je Steuersatz als JSON: {"20": {"netto": "...", "ust": "..."}, ...}
    steuer_details: str = "{}"
    erstellt_von: int | None = Field(default=None, foreign_key="benutzer.id")
    erstellt_am: datetime = Field(default_factory=datetime.now)
    gedruckt_am: datetime | None = None
    # Zahlungsstatus (relevant für Rechnung/Gutschrift/EK-Rechnung): None = offen
    bezahlt_am: date | None = Field(default=None, index=True)
    # Abschlagsrechnung (Anzahlung): markiert Abschläge; auftrag_id verknüpft
    # Abschlags- und Schlussrechnungen mit ihrem Ur-Auftrag (für den Abschlagsabzug).
    ist_abschlag: bool = Field(default=False, index=True)
    auftrag_id: int | None = Field(default=None, foreign_key="beleg.id", index=True)
    # Skonto: Abzug in Prozent, wenn innerhalb skonto_tage ab Belegdatum gezahlt
    # wird (0 % = kein Skonto). Wird aus der Adresse vorbelegt.
    skonto_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    skonto_tage: int = 0
    # Mahnwesen: 0 = nicht gemahnt, 1..n = Mahnstufe (Zahlungserinnerung, 1., 2. Mahnung)
    mahnstufe: int = Field(default=0, index=True)
    letzte_mahnung_am: date | None = None


class BelegPosition(SQLModel, table=True):
    __tablename__ = "belegposition"

    id: int | None = Field(default=None, primary_key=True)
    beleg_id: int = Field(foreign_key="beleg.id", index=True)
    pos_nr: int = 1
    artikel_id: int | None = Field(default=None, foreign_key="artikel.id", index=True)
    bezeichnung: str = ""
    langtext: str = ""
    menge: Decimal = Field(default=Decimal("1"), sa_column=Column(DecimalStr, nullable=False))
    einheit: str = "Stk"
    # Kalkulation je Position (aus dem Artikel vorbelegt, hier überschreibbar):
    # listenpreis − rabatt1 − rabatt2 = ek_preis; ek_preis + aufschlag % = einzelpreis (VK)
    listenpreis: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    rabatt1_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    rabatt2_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    ek_preis: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    aufschlag_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    einzelpreis: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    # Positionsrabatt auf den VK (Verkaufsrabatt, unabhängig von der EK-Kalkulation)
    rabatt_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    ust_satz: int = 20
    # Referenz auf die Position des Vorgängerbelegs (Belegübernahme)
    ursprungs_position_id: int | None = Field(default=None, foreign_key="belegposition.id")


class BelegPositionSeriennummer(SQLModel, table=True):
    """Zuordnung Seriennummern ↔ Belegposition (Lieferschein, läuft auf Rechnung mit)."""

    __tablename__ = "belegposition_seriennummer"
    __table_args__ = (UniqueConstraint("position_id", "seriennummer_id", name="uq_pos_sn"),)

    id: int | None = Field(default=None, primary_key=True)
    position_id: int = Field(foreign_key="belegposition.id", index=True)
    seriennummer_id: int = Field(foreign_key="seriennummer.id", index=True)
