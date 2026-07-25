"""Produktions-Erstinitialisierung (KEINE Demo-Daten).

Legt idempotent an: die zwei Standardrollen, ein Standardlager und — falls noch
kein Benutzer existiert — einen Admin-Benutzer. Passwort aus der Umgebungsvariable
WERKPLUS_ADMIN_PASSWORT (Fallback "admin" mit deutlicher Warnung).

Aufruf im Container: python -m scripts.init_prod
"""

import os

from sqlmodel import Session, select

from app.database import engine
from app.models import Benutzer, Rolle
from app.services.auth_service import hash_passwort
from scripts.seed import seed_lager, seed_rollen


def init(session: Session) -> None:
    seed_rollen(session)
    seed_lager(session)

    if session.exec(select(Benutzer)).first() is None:
        passwort = os.environ.get("WERKPLUS_ADMIN_PASSWORT", "admin")
        session.add(
            Benutzer(
                login="admin",
                name="Administrator",
                passwort_hash=hash_passwort(passwort),
                rolle=Rolle.ADMIN,
            )
        )
        if passwort == "admin":
            print(
                "!!! ACHTUNG: Admin-Benutzer mit Standardpasswort 'admin' angelegt. "
                "Bitte sofort nach dem ersten Login ändern (oder WERKPLUS_ADMIN_PASSWORT setzen)."
            )
        else:
            print("Admin-Benutzer 'admin' mit gesetztem Passwort angelegt.")
    session.commit()


def main() -> None:
    with Session(engine) as session:
        init(session)
    print("Produktions-Initialisierung abgeschlossen.")


if __name__ == "__main__":
    main()
