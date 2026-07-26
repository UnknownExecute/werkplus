from enum import StrEnum

from sqlmodel import Field, SQLModel


class Zugriff(StrEnum):
    """Zugriffsstufe je Bereich."""

    KEIN = "kein"
    ANSEHEN = "ansehen"
    BEARBEITEN = "bearbeiten"


# Rang zum Vergleichen der Stufen
ZUGRIFF_RANG: dict[str, int] = {
    Zugriff.KEIN: 0,
    Zugriff.ANSEHEN: 1,
    Zugriff.BEARBEITEN: 2,
}

# Steuerbare Bereiche (Feldname ohne "recht_"-Präfix) + deutsche Labels
BEREICHE: tuple[str, ...] = (
    "adressen",
    "artikel",
    "verkauf",
    "einkauf",
    "lager",
    "anlagen",
    "zeiterfassung",
    "einstellungen",
    "benutzer",
)
BEREICH_LABELS: dict[str, str] = {
    "adressen": "Adressen / CRM",
    "artikel": "Artikel & Textbausteine",
    "verkauf": "Verkaufsbelege",
    "einkauf": "Einkauf",
    "lager": "Lager",
    "anlagen": "Anlagen & Service",
    "zeiterfassung": "Arbeitszeiten",
    "einstellungen": "Einstellungen",
    "benutzer": "Benutzerverwaltung",
}


class Rollenprofil(SQLModel, table=True):
    """Frei konfigurierbare Rolle mit Bereichsrechten. Verknüpft über ``name``
    mit ``Benutzer.rolle``."""

    __tablename__ = "rollenprofil"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # z. B. "admin", "monteur"
    beschreibung: str = ""
    ist_admin: bool = False  # Vollzugriff (überschreibt alle Bereichsrechte)
    ist_standard: bool = False  # eingebaut, nicht löschbar
    darf_kalkulation_sehen: bool = True  # EK-Preise, Kalkulation, Marge

    recht_adressen: Zugriff = Zugriff.KEIN
    recht_artikel: Zugriff = Zugriff.KEIN
    recht_verkauf: Zugriff = Zugriff.KEIN
    recht_einkauf: Zugriff = Zugriff.KEIN
    recht_lager: Zugriff = Zugriff.KEIN
    recht_anlagen: Zugriff = Zugriff.KEIN
    # Arbeitszeiterfassung: ANSEHEN/BEARBEITEN gelten für die EIGENEN Zeiten.
    # Fremde Zeiten sieht und ändert nur ein Admin (ist_admin) — siehe
    # app/services/zeiterfassung_service.py.
    recht_zeiterfassung: Zugriff = Zugriff.KEIN
    recht_einstellungen: Zugriff = Zugriff.KEIN
    recht_benutzer: Zugriff = Zugriff.KEIN
