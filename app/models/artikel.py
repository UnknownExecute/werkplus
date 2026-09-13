from datetime import date
from decimal import Decimal

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalStr

# Zulässige österreichische USt-Sätze (Regelbesteuerung)
UST_SAETZE = (20, 10, 13)


class Artikel(SQLModel, table=True):
    """Artikelstamm im Kplus-Zuschnitt.

    Suchfelder wie in Kplus „Artikel suchen": Artikelnummer, Matchcode, EAN,
    Alternativ-Nummer/Type, Bezeichnung, Warengruppe/Unterwarengruppe.
    Lieferanten je Artikel und Staffelpreise sind eigene Tabellen; „letzter EK /
    letzter Lieferant" wird beim Buchen einer EK-Rechnung fortgeschrieben.
    """

    __tablename__ = "artikel"

    id: int | None = Field(default=None, primary_key=True)
    artikelnr: str = Field(unique=True, index=True)
    matchcode: str = Field(default="", index=True)
    ean: str = Field(default="", index=True)
    alternativnr: str = ""  # Alternativ-Nummer / Type (Herstellerbezeichnung)
    hersteller: str = ""
    bezeichnung: str = Field(index=True)
    langtext: str = ""
    bemerkung: str = ""  # interne Bemerkung (nicht auf Belegen)
    warengruppe: str = Field(default="", index=True)
    unterwarengruppe: str = ""
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
    # Kplus „Mindest-Preis": unter diesen VK soll nicht verkauft werden (Warnung im Grid)
    mindest_vk: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    preisliste_id: int | None = Field(default=None)  # vorbereitet für spätere Preislisten
    # Letzter Einkauf (aus gebuchter EK-Rechnung) und letzte Preisänderung im Stamm
    letzter_ek: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    letzter_lieferant_id: int | None = Field(default=None, index=True)
    letzter_ek_am: date | None = None
    letzte_preisaenderung: date | None = None
    ust_satz: int = 20
    seriennummernpflichtig: bool = False
    ist_lagerartikel: bool = True  # False = Dienstleistung (keine Lagerführung)
    # Meldebestand für den Bestellvorschlag (0 = keine Überwachung).
    mindestbestand: Decimal = Field(
        default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False)
    )
    ist_set: bool = False  # Stückliste/Set: fügt beim Einsetzen seine Komponenten ein
    aktiv: bool = True

    @property
    def artikelart(self) -> str:
        if self.ist_set:
            return "set"
        return "lager" if self.ist_lagerartikel else "dienstleistung"


ARTIKELART_LABELS: dict[str, str] = {
    "lager": "Lagerartikel",
    "dienstleistung": "Dienstleistung",
    "set": "Set / Stückliste",
}


class ArtikelLieferant(SQLModel, table=True):
    """Bezugsquelle eines Artikels (Kplus-Reiter „Einkauf"/Lieferant je Artikel)."""

    __tablename__ = "artikel_lieferant"
    __table_args__ = (
        UniqueConstraint("artikel_id", "adresse_id", name="uq_artikel_lieferant"),
    )

    id: int | None = Field(default=None, primary_key=True)
    artikel_id: int = Field(foreign_key="artikel.id", index=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)  # Lieferant
    lieferanten_artikelnr: str = ""
    ek_preis: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
    lieferzeit_tage: int = 0
    ist_hauptlieferant: bool = False
    notiz: str = ""


class ArtikelStaffel(SQLModel, table=True):
    """Staffelpreis: ab ``ab_menge`` gilt ``vk_preis`` (netto) statt des Stamm-VK."""

    __tablename__ = "artikel_staffel"
    __table_args__ = (UniqueConstraint("artikel_id", "ab_menge", name="uq_artikel_staffel"),)

    id: int | None = Field(default=None, primary_key=True)
    artikel_id: int = Field(foreign_key="artikel.id", index=True)
    ab_menge: Decimal = Field(default=Decimal("1"), sa_column=Column(DecimalStr, nullable=False))
    vk_preis: Decimal = Field(default=Decimal("0"), sa_column=Column(DecimalStr, nullable=False))
