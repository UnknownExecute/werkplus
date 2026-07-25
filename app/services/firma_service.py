"""Firmenstammdaten aus der DB (Single-Row id=1) laden und pflegen.

Die Tabelle ``firma`` enthält genau einen Datensatz. Existiert er noch nicht,
wird er beim ersten Zugriff einmalig aus den Config-Defaults (``settings.firma``)
angelegt. Danach ist die DB die maßgebliche Quelle für den Belegdruck.
"""

from sqlmodel import Session

from app.config import settings
from app.models import Firma

# Auf diese Felder beschränken sich Erstbefüllung und Aktualisierung.
FIRMA_FELDER = ("name", "strasse", "plz", "ort", "land", "uid", "telefon", "email", "iban")


def lade_firma(session: Session) -> Firma:
    """Lädt den Firmensatz (id=1); legt ihn beim ersten Aufruf aus Defaults an."""
    firma = session.get(Firma, 1)
    if firma is None:
        firma = Firma(id=1, **{feld: getattr(settings.firma, feld) for feld in FIRMA_FELDER})
        session.add(firma)
        session.commit()
        session.refresh(firma)
    return firma


def aktualisiere_firma(session: Session, **felder: str) -> Firma:
    """Überschreibt die angegebenen Firma-Felder persistent und gibt den Satz zurück."""
    firma = lade_firma(session)
    for feld, wert in felder.items():
        if feld in FIRMA_FELDER:
            setattr(firma, feld, wert)
    session.add(firma)
    session.commit()
    session.refresh(firma)
    return firma
