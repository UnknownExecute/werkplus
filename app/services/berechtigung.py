"""Zentrale Berechtigungslogik: Rollenprofile, Bereichsrechte, Route-Wachen.

Die effektiven Rechte eines Benutzers stammen aus seinem Rollenprofil
(``Rollenprofil`` mit ``name == benutzer.rolle``). Existiert kein Profil,
greift ein sicherer Fallback anhand des Rollennamens — dadurch funktionieren
Bestandsdaten und Tests ohne angelegte Profile weiter.

Die Durchsetzung erfolgt pro Router über :func:`bereich_wache` (GET ⇒ ansehen,
schreibende Methoden ⇒ bearbeiten). Für Templates werden die Rechte beim Login
in die Session geschrieben (siehe :func:`rechte_als_dict`).
"""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user
from app.models import (
    BEREICHE,
    ZUGRIFF_RANG,
    Benutzer,
    Rolle,
    Rollenprofil,
    Zugriff,
)

# HTTP-Methoden, die als "bearbeiten" gelten (sonst "ansehen")
_SCHREIB_METHODEN = {"POST", "PUT", "PATCH", "DELETE"}


def _fallback_profil(rolle_name: str) -> Rollenprofil:
    """Rechte, wenn kein Rollenprofil in der DB existiert (Bestand/Tests).

    ``admin`` ⇒ Vollzugriff; alles andere ⇒ operatives Sachbearbeiter-Profil
    (alle Bereiche außer Einstellungen/Benutzerverwaltung bearbeitbar).
    """
    if rolle_name == Rolle.ADMIN:
        return Rollenprofil(
            name=Rolle.ADMIN, ist_admin=True, ist_standard=True,
            darf_kalkulation_sehen=True,
        )
    operativ = {
        "recht_adressen": Zugriff.BEARBEITEN,
        "recht_artikel": Zugriff.BEARBEITEN,
        "recht_verkauf": Zugriff.BEARBEITEN,
        "recht_einkauf": Zugriff.BEARBEITEN,
        "recht_lager": Zugriff.BEARBEITEN,
        "recht_anlagen": Zugriff.BEARBEITEN,
        "recht_zeiterfassung": Zugriff.BEARBEITEN,
        "recht_einstellungen": Zugriff.KEIN,
        "recht_benutzer": Zugriff.KEIN,
    }
    return Rollenprofil(
        name=rolle_name or Rolle.SACHBEARBEITER, darf_kalkulation_sehen=True, **operativ
    )


def lade_profil(session: Session, benutzer: Benutzer) -> Rollenprofil:
    """Rollenprofil des Benutzers laden; Fallback, wenn keins existiert."""
    profil = session.exec(
        select(Rollenprofil).where(Rollenprofil.name == benutzer.rolle)
    ).first()
    return profil if profil is not None else _fallback_profil(benutzer.rolle)


def darf(profil: Rollenprofil, bereich: str, mindestens: Zugriff = Zugriff.ANSEHEN) -> bool:
    """Prüft, ob das Profil im Bereich mindestens die geforderte Stufe hat."""
    if profil.ist_admin:
        return True
    stufe = getattr(profil, f"recht_{bereich}", Zugriff.KEIN)
    return ZUGRIFF_RANG[stufe] >= ZUGRIFF_RANG[mindestens]


def rechte_als_dict(profil: Rollenprofil) -> dict:
    """Rechte als schlichtes dict für Session/Template (kein DB-Zugriff nötig)."""
    return {
        "ist_admin": profil.ist_admin,
        "darf_kalkulation": profil.ist_admin or profil.darf_kalkulation_sehen,
        "bereiche": {
            bereich: (
                Zugriff.BEARBEITEN
                if profil.ist_admin
                else getattr(profil, f"recht_{bereich}", Zugriff.KEIN)
            )
            for bereich in BEREICHE
        },
    }


def _rechte_aus_session(request: Request) -> dict:
    """Template-Helfer: die beim Login gespeicherten Rechte (leer, wenn nicht da)."""
    return request.session.get("rechte") or {
        "ist_admin": False, "darf_kalkulation": False, "bereiche": {}
    }


def darf_bereich(request: Request, bereich: str, mindestens: str = "ansehen") -> bool:
    """Template-Global: darf der angemeldete Benutzer den Bereich (an)sehen/bearbeiten?"""
    rechte = _rechte_aus_session(request)
    if rechte.get("ist_admin"):
        return True
    stufe = rechte.get("bereiche", {}).get(bereich, Zugriff.KEIN)
    return ZUGRIFF_RANG.get(stufe, 0) >= ZUGRIFF_RANG.get(mindestens, 1)


def darf_kalkulation(request: Request) -> bool:
    """Template-Global: darf der Benutzer EK-Preise/Kalkulation/Marge sehen?"""
    return bool(_rechte_aus_session(request).get("darf_kalkulation"))


def bereich_wache(*bereiche: str) -> Callable:
    """Router-Dependency: erzwingt Bereichsrecht (GET ⇒ ansehen, sonst bearbeiten).

    Mehrere Bereiche = ODER-Verknüpfung (Zugriff genügt in EINEM davon) — genutzt
    für den generischen Belege-Router (verkauf/einkauf).
    """

    def wache(
        request: Request,
        benutzer: Benutzer = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> Benutzer:
        mindestens = (
            Zugriff.BEARBEITEN if request.method in _SCHREIB_METHODEN else Zugriff.ANSEHEN
        )
        profil = lade_profil(session, benutzer)
        if not any(darf(profil, b, mindestens) for b in bereiche):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Keine Berechtigung für: {', '.join(bereiche)}.",
            )
        return benutzer

    return wache
