"""Textbaustein-Stammdaten: Suche/Filter, Laden, Speichern (anlegen/aktualisieren)."""

from sqlmodel import Session, select

from app.models import Textbaustein, TextbausteinKategorie


def suche_bausteine(
    session: Session,
    q: str = "",
    kategorie: TextbausteinKategorie | str | None = None,
) -> list[Textbaustein]:
    """Textbausteine sortiert nach Kategorie und Titel.

    - Freitextsuche über kuerzel/titel/text (case-insensitive via ilike).
    - Optionaler Kategorie-Filter; ungültige/leere Werte werden ignoriert.
    """
    stmt = select(Textbaustein)
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            Textbaustein.kuerzel.ilike(muster)  # type: ignore[attr-defined]
            | Textbaustein.titel.ilike(muster)  # type: ignore[attr-defined]
            | Textbaustein.text.ilike(muster)  # type: ignore[attr-defined]
        )
    if kategorie in (
        TextbausteinKategorie.POSITION,
        TextbausteinKategorie.KOPF,
        TextbausteinKategorie.FUSS,
    ):
        stmt = stmt.where(Textbaustein.kategorie == kategorie)
    stmt = stmt.order_by(Textbaustein.kategorie, Textbaustein.titel)
    return list(session.exec(stmt).all())


def lade(session: Session, baustein_id: int) -> Textbaustein | None:
    return session.get(Textbaustein, baustein_id)


def speichere(  # noqa: PLR0913
    session: Session,
    baustein_id: int | None,
    kuerzel: str,
    titel: str,
    text: str,
    kategorie: TextbausteinKategorie | str,
    aktiv: bool,
) -> Textbaustein:
    """Legt einen Textbaustein an (baustein_id is None) oder aktualisiert ihn."""
    baustein = session.get(Textbaustein, baustein_id) if baustein_id else None
    if baustein is None:
        baustein = Textbaustein()
    baustein.kuerzel = kuerzel.strip()
    baustein.titel = titel.strip()
    baustein.text = text.strip()
    baustein.kategorie = TextbausteinKategorie(kategorie)
    baustein.aktiv = aktiv
    session.add(baustein)
    session.commit()
    session.refresh(baustein)
    return baustein
