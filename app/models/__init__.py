"""Alle Entities — Import hier stellt sicher, dass SQLModel.metadata vollständig ist."""

from app.models.adresse import Adresse, AdressTyp, Ansprechpartner, Lieferadresse
from app.models.aktivitaet import AKTIVITAET_LABELS, Aktivitaet, AktivitaetTyp
from app.models.anlage import ANLAGE_ARTEN, Anlage, AnlageKomponente, Serviceeinsatz
from app.models.artikel import (
    ARTIKELART_LABELS,
    UST_SAETZE,
    Artikel,
    ArtikelLieferant,
    ArtikelStaffel,
)
from app.models.beleg import (
    AUFTRAGSART_LABELS,
    Angebotsergebnis,
    Auftragsart,
    Beleg,
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
    BelegStatus,
)
from app.models.benutzer import Benutzer, Rolle
from app.models.bugreport import BugReport, MeldungTyp
from app.models.favorit import Favorit
from app.models.firma import Firma
from app.models.lager import Buchungsart, Lager, Lagerbestand, Lagerbewegung
from app.models.nummernkreis import Nummernkreis
from app.models.rollenprofil import (
    BEREICH_LABELS,
    BEREICHE,
    ZUGRIFF_RANG,
    Rollenprofil,
    Zugriff,
)
from app.models.seriennummer import Seriennummer, SnStatus
from app.models.setkomponente import SetKomponente
from app.models.textbaustein import Textbaustein, TextbausteinKategorie
from app.models.zahlung import Zahlung, Zahlungsart
from app.models.zeiterfassung import Zeiterfassung

__all__ = [
    "ANLAGE_ARTEN",
    "ARTIKELART_LABELS",
    "AUFTRAGSART_LABELS",
    "UST_SAETZE",
    "Angebotsergebnis",
    "Auftragsart",
    "AKTIVITAET_LABELS",
    "Adresse",
    "AdressTyp",
    "Aktivitaet",
    "AktivitaetTyp",
    "Ansprechpartner",
    "Lieferadresse",
    "Anlage",
    "AnlageKomponente",
    "Artikel",
    "ArtikelLieferant",
    "ArtikelStaffel",
    "Beleg",
    "BelegPosition",
    "BelegPositionSeriennummer",
    "BelegStatus",
    "Belegart",
    "Benutzer",
    "BugReport",
    "Buchungsart",
    "MeldungTyp",
    "Favorit",
    "Firma",
    "Lager",
    "Lagerbestand",
    "Lagerbewegung",
    "BEREICHE",
    "BEREICH_LABELS",
    "ZUGRIFF_RANG",
    "Nummernkreis",
    "Rolle",
    "Rollenprofil",
    "Seriennummer",
    "Zugriff",
    "Serviceeinsatz",
    "SetKomponente",
    "SnStatus",
    "Textbaustein",
    "TextbausteinKategorie",
    "Zahlung",
    "Zahlungsart",
    "Zeiterfassung",
]
