from sqlmodel import Field, SQLModel


class Favorit(SQLModel, table=True):
    """Persönlicher Schnellzugriff in der Seitenleiste (Kplus-Desktop „Favoriten")."""

    __tablename__ = "favorit"

    id: int | None = Field(default=None, primary_key=True)
    benutzer_id: int = Field(foreign_key="benutzer.id", index=True)
    titel: str = ""
    url: str = ""
    sort: int = 0
