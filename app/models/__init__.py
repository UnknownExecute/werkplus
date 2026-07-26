"""Alle Entities — Import hier stellt sicher, dass SQLModel.metadata vollständig ist."""

from app.models.adresse import Adresse, AdressTyp
from app.models.aktivitaet import Aktivitaet, AktivitaetTyp
from app.models.anlage import Anlage, Serviceeinsatz
from app.models.artikel import UST_SAETZE, Artikel
from app.models.beleg import (
    Beleg,
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
    BelegStatus,
)
from app.models.benutzer import Benutzer, Rolle
from app.models.bugreport import BugReport, MeldungTyp
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
    "UST_SAETZE",
    "Adresse",
    "AdressTyp",
    "Aktivitaet",
    "AktivitaetTyp",
    "Anlage",
    "Artikel",
    "Beleg",
    "BelegPosition",
    "BelegPositionSeriennummer",
    "BelegStatus",
    "Belegart",
    "Benutzer",
    "BugReport",
    "Buchungsart",
    "MeldungTyp",
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
