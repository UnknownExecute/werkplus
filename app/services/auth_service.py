"""Benutzer-Authentifizierung: argon2-Hashing und Login-Prüfung."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlmodel import Session, select

from app.models import Benutzer

_hasher = PasswordHasher()


def hash_passwort(passwort: str) -> str:
    return _hasher.hash(passwort)


def verify_passwort(passwort_hash: str, passwort: str) -> bool:
    try:
        return _hasher.verify(passwort_hash, passwort)
    except VerifyMismatchError:
        return False


def authentifiziere(session: Session, login: str, passwort: str) -> Benutzer | None:
    """Gibt den Benutzer zurück, wenn Login + Passwort stimmen und er aktiv ist."""
    benutzer = session.exec(select(Benutzer).where(Benutzer.login == login)).first()
    if benutzer is None or not benutzer.aktiv:
        return None
    if not verify_passwort(benutzer.passwort_hash, passwort):
        return None
    return benutzer
