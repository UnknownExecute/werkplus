"""Adressmanager: Adressnummern, Ansprechpartner, Lieferadressen, Wiedervorlage.

Keine FastAPI-Abhängigkeit. Funktionen flushen nur — der Aufrufer committed.
"""

from datetime import date, datetime

from sqlmodel import Session, col, select

from app.models.adresse import Adresse, Ansprechpartner, Lieferadresse
from app.models.aktivitaet import Aktivitaet
from app.services import nummernkreis_service
from app.services.fehler import WerkplusFehler

# Adressnummern beginnen bei 10001 (fünfstellig wie in Kplus üblich)
ADRESSNR_BASIS = 10000
ADRESSNR_SCHLUESSEL = "adresse"  # Zähler im Nummernkreis, Jahr 0 (kein Jahreswechsel)


def naechste_adressnr(session: Session) -> str:
    """Zieht atomar die nächste Adressnummer (``10001``, ``10002`` …)."""
    nummer = nummernkreis_service.naechste_nummer(session, ADRESSNR_SCHLUESSEL, 0)
    return str(ADRESSNR_BASIS + nummer)


def adressnr_stand(session: Session) -> int:
    """Letzte vergebene Adressnummer (als Zahl, z. B. 10007; 10000 = keine)."""
    return ADRESSNR_BASIS + nummernkreis_service.stand(session, ADRESSNR_SCHLUESSEL, 0)


def setze_adressnr_stand(session: Session, letzte_adressnr: int) -> None:
    """Startwert für Adressnummern setzen (nur nach oben, Anschluss an Kplus)."""
    if letzte_adressnr < ADRESSNR_BASIS:
        raise WerkplusFehler(f"Adressnummern beginnen bei {ADRESSNR_BASIS + 1}")
    nummernkreis_service.setze_stand(
        session, ADRESSNR_SCHLUESSEL, 0, letzte_adressnr - ADRESSNR_BASIS
    )


def adressnr_vergeben(session: Session, adressnr: str, eigene_id: int | None = None) -> bool:
    """Ist diese Adressnummer schon einer anderen Adresse zugeordnet?"""
    stmt = select(Adresse.id).where(Adresse.adressnr == adressnr.strip())
    if eigene_id is not None:
        stmt = stmt.where(col(Adresse.id) != eigene_id)
    return session.exec(stmt).first() is not None


# ------------------------------------------------------------ Ansprechpartner


def ansprechpartner_liste(session: Session, adresse_id: int) -> list[Ansprechpartner]:
    return list(
        session.exec(
            select(Ansprechpartner)
            .where(Ansprechpartner.adresse_id == adresse_id)
            .order_by(col(Ansprechpartner.ist_hauptkontakt).desc(), Ansprechpartner.nachname)
        ).all()
    )


def synchronisiere_hauptkontakt(session: Session, adresse: Adresse) -> None:
    """Schreibt den Namen des Hauptkontakts denormalisiert in ``adresse.ansprechpartner``.

    Gibt es keinen markierten Hauptkontakt, wird der erste aktive genommen;
    ohne Kontakte bleibt das Feld leer. Listen, Suche und Beleg-PDF lesen nur
    dieses Feld — so bleibt alles ohne Join schnell und der Kontaktstamm
    trotzdem die einzige Quelle.
    """
    kontakte = [k for k in ansprechpartner_liste(session, adresse.id) if k.aktiv]
    haupt = next((k for k in kontakte if k.ist_hauptkontakt), None)
    if haupt is None and kontakte:
        haupt = kontakte[0]
        haupt.ist_hauptkontakt = True
        session.add(haupt)
    adresse.ansprechpartner = haupt.anzeigename if haupt else ""
    session.add(adresse)
    session.flush()


def setze_hauptkontakt(session: Session, adresse: Adresse, kontakt: Ansprechpartner) -> None:
    if kontakt.adresse_id != adresse.id:
        raise WerkplusFehler("Ansprechpartner gehört nicht zu dieser Adresse")
    for k in ansprechpartner_liste(session, adresse.id):
        k.ist_hauptkontakt = k.id == kontakt.id
        session.add(k)
    session.flush()
    synchronisiere_hauptkontakt(session, adresse)


def loesche_ansprechpartner(
    session: Session, adresse: Adresse, kontakt: Ansprechpartner
) -> None:
    if kontakt.adresse_id != adresse.id:
        raise WerkplusFehler("Ansprechpartner gehört nicht zu dieser Adresse")
    session.delete(kontakt)
    session.flush()
    synchronisiere_hauptkontakt(session, adresse)


# ------------------------------------------------------------- Lieferadressen


def lieferadressen(session: Session, adresse_id: int) -> list[Lieferadresse]:
    return list(
        session.exec(
            select(Lieferadresse)
            .where(Lieferadresse.adresse_id == adresse_id)
            .order_by(Lieferadresse.bezeichnung, Lieferadresse.ort)
        ).all()
    )


# ---------------------------------------------------------------- Wiedervorlage


def offene_wiedervorlagen(
    session: Session,
    bis: date | None = None,
    benutzer_id: int | None = None,
) -> list[tuple[Aktivitaet, Adresse]]:
    """Offene Aktivitäten mit Wiedervorlage-Datum, älteste zuerst.

    ``bis`` begrenzt auf fällige (Wiedervorlage ≤ bis); ``benutzer_id`` auf die
    eigenen. Ohne beides: alle offenen Wiedervorlagen.
    """
    stmt = (
        select(Aktivitaet, Adresse)
        .join(Adresse, col(Aktivitaet.adresse_id) == col(Adresse.id))
        .where(col(Aktivitaet.erledigt).is_(False))
        .where(col(Aktivitaet.wiedervorlage_am).is_not(None))
    )
    if bis is not None:
        stmt = stmt.where(col(Aktivitaet.wiedervorlage_am) <= bis)
    if benutzer_id is not None:
        stmt = stmt.where(Aktivitaet.benutzer_id == benutzer_id)
    stmt = stmt.order_by(Aktivitaet.wiedervorlage_am, Aktivitaet.id)
    return list(session.exec(stmt).all())


def setze_erledigt(session: Session, aktivitaet: Aktivitaet, erledigt: bool) -> None:
    aktivitaet.erledigt = erledigt
    aktivitaet.erledigt_am = datetime.now() if erledigt else None
    session.add(aktivitaet)
    session.flush()
