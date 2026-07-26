from decimal import Decimal

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr

# Zulässige österreichische USt-Sätze (Regelbesteuerung)
UST_SAETZE = (20, 10, 13)


class Artikel(SQLModel, table=True):
    __tablename__ = "artikel"

    id: int | None = Field(default=None, primary_key=True)
    artikelnr: str = Field(unique=True, index=True)
    bezeichnung: str = Field(index=True)
    langtext: str = ""
    warengruppe: str = Field(default="", index=True)
    einheit: str = "Stk"
    # Kalkulationskette (wie Kplus): Listenpreis − Rab1 − Rab2 = EK; EK + Aufschlag % = VK
    listenpreis_netto: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    rabatt1_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    rabatt2_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    aufschlag_prozent: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    # EK/VK sind Ergebnis der Kalkulation (VK bleibt manuell überschreibbar)
    ek_preis: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    vk_preis: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    preisliste_id: int | None = Field(default=None)  # vorbereitet für spätere Preislisten
    ust_satz: int = 20
    seriennummernpflichtig: bool = False
    ist_lagerartikel: bool = True  # False = Dienstleistung (keine Lagerführung)
    # Meldebestand für den Bestellvorschlag (0 = keine Überwachung).
    mindestbestand: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    ist_set: bool = False  # Stückliste/Set: fügt beim Einsetzen seine Komponenten ein
    aktiv: bool = True
