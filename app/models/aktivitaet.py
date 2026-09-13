from datetime import date, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class AktivitaetTyp(StrEnum):
    NOTIZ = "notiz"
    TELEFONAT = "telefonat"
    EMAIL = "email"
    TERMIN = "termin"
    AUFGABE = "aufgabe"


AKTIVITAET_LABELS: dict[str, str] = {
    AktivitaetTyp.NOTIZ: "Notiz",
    AktivitaetTyp.TELEFONAT: "Telefonat",
    AktivitaetTyp.EMAIL: "E-Mail",
    AktivitaetTyp.TERMIN: "Termin",
    AktivitaetTyp.AUFGABE: "Aufgabe",
}


class Aktivitaet(SQLModel, table=True):
    """CRM-Aktivität zu einer Adresse (Kplus-Reiter „Aktivitäten").

    Mit **Wiedervorlage** (Datum, an dem die Sache wieder hochkommt) und
    **Status** offen/erledigt; optional mit Belegbezug. ``beleg_id`` ist
    bewusst ohne FK-Constraint (SQLite-Batch-Migrationen, siehe alembic/env.py).
    """

    __tablename__ = "aktivitaet"

    id: int | None = Field(default=None, primary_key=True)
    adresse_id: int = Field(foreign_key="adresse.id", index=True)
    typ: AktivitaetTyp = AktivitaetTyp.NOTIZ
    betreff: str = ""
    text: str = ""
    benutzer_id: int | None = Field(default=None, foreign_key="benutzer.id")
    zeitstempel: datetime = Field(default_factory=datetime.now)
    wiedervorlage_am: date | None = Field(default=None, index=True)
    erledigt: bool = Field(default=False, index=True)
    erledigt_am: datetime | None = None
    beleg_id: int | None = Field(default=None, index=True)

    def ist_faellig(self, heute: date) -> bool:
        return (
            not self.erledigt
            and self.wiedervorlage_am is not None
            and self.wiedervorlage_am <= heute
        )
