from sqlmodel import Field, SQLModel


class Firma(SQLModel, table=True):
    """Eigene Betriebsstammdaten für den Belegdruck (§ 11 UStG).

    Genau ein Datensatz (id=1). Wird beim ersten Zugriff aus den Config-Defaults
    angelegt und danach über die Einstellungsseite gepflegt.
    """

    __tablename__ = "firma"

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
    strasse: str = ""
    plz: str = ""
    ort: str = ""
    land: str = "Österreich"
    uid: str = ""
    telefon: str = ""
    email: str = ""
    iban: str = ""
