"""Mitarbeiter- & Rollenverwaltung — nur für Benutzer-Berechtigte.

Mitarbeiter (``Benutzer``) werden angelegt/bearbeitet und deaktiviert statt
gelöscht. Rollen (``Rollenprofil``) sind frei konfigurierbar; Standard-Rollen
sind vor Umbenennung und Löschung geschützt.

Routen-Reihenfolge: die statischen ``/rollen``-Routen stehen VOR ``/{benutzer_id}``,
sonst würde ``/benutzer/rollen`` vom int-Pfadparameter geschluckt (422).
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user, ist_admin
from app.models import (
    BEREICH_LABELS,
    BEREICHE,
    Benutzer,
    Rollenprofil,
    Zugriff,
)
from app.services.auth_service import hash_passwort
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.templating import templates

router = APIRouter(
    prefix="/benutzer",
    tags=["benutzer"],
    dependencies=[Depends(bereich_wache("benutzer"))],
)


def _rollen(session: Session) -> list[Rollenprofil]:
    return list(session.exec(select(Rollenprofil).order_by(Rollenprofil.name)).all())


def _zugriff(wert: str) -> Zugriff:
    """String aus dem Formular sicher in eine Zugriffsstufe wandeln."""
    try:
        return Zugriff(wert)
    except ValueError:
        return Zugriff.KEIN


def _rolle_in_benutzung(session: Session, name: str) -> bool:
    return session.exec(select(Benutzer).where(Benutzer.rolle == name)).first() is not None


# --- A) Mitarbeiter (Benutzer): Liste + Anlegen -------------------------------


@router.get("")
def benutzer_liste(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    liste = list(session.exec(select(Benutzer).order_by(Benutzer.login)).all())
    return templates.TemplateResponse(
        request,
        "benutzer/liste.html",
        {"benutzer_liste": liste, "aktueller": benutzer},
    )


@router.get("/neu")
def benutzer_neu_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "benutzer/form.html",
        {
            "benutzer": Benutzer(login="", name="", passwort_hash=""),
            "neu": True,
            "rollen": _rollen(session),
        },
    )


@router.post("/neu")
def benutzer_neu(
    request: Request,
    login: str = Form(...),
    name: str = Form(...),
    rolle: str = Form(""),
    passwort: str = Form(...),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    login = login.strip()
    anzeige = Benutzer(login=login, name=name.strip(), passwort_hash="", rolle=rolle, aktiv=True)
    if session.exec(select(Benutzer).where(Benutzer.login == login)).first() is not None:
        return templates.TemplateResponse(
            request,
            "benutzer/form.html",
            {
                "benutzer": anzeige,
                "neu": True,
                "rollen": _rollen(session),
                "fehler": f"Login „{login}“ ist bereits vergeben.",
            },
        )
    neuer = Benutzer(
        login=login,
        name=name.strip(),
        passwort_hash=hash_passwort(passwort),
        rolle=rolle,
        aktiv=True,
    )
    session.add(neuer)
    session.commit()
    session.refresh(neuer)
    return RedirectResponse(f"/benutzer/{neuer.id}", status_code=status.HTTP_302_FOUND)


# --- B) Rollen (Rollenprofile) — VOR den /{benutzer_id}-Routen ----------------


@router.get("/rollen")
def rollen_liste(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "benutzer/rollen.html",
        {"rollen": _rollen(session)},
    )


def _rolle_kontext(rolle: Rollenprofil, neu: bool, **extra) -> dict:
    return {
        "rolle": rolle,
        "neu": neu,
        "bereiche": BEREICHE,
        "bereich_labels": BEREICH_LABELS,
        "zugriffe": list(Zugriff),
        **extra,
    }


@router.get("/rollen/neu")
def rolle_neu_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request,
        "benutzer/rolle_form.html",
        _rolle_kontext(Rollenprofil(name=""), neu=True),
    )


def _rolle_aus_form(rolle: Rollenprofil, form, darf_admin_setzen: bool) -> Rollenprofil:
    """Rollenfelder aus dem Formular übernehmen.

    ``ist_admin`` darf nur ein echter Admin setzen — sonst könnte sich jemand mit
    bloßem Benutzer-Bearbeitungsrecht über eine neue Admin-Rolle selbst erhöhen.
    """
    rolle.beschreibung = (form.get("beschreibung") or "").strip()
    if darf_admin_setzen:
        rolle.ist_admin = form.get("ist_admin") is not None
    rolle.darf_kalkulation_sehen = form.get("darf_kalkulation_sehen") is not None
    for bereich in BEREICHE:
        setattr(rolle, f"recht_{bereich}", _zugriff(form.get(f"recht_{bereich}", "")))
    return rolle


def _aktive_admins(session: Session) -> list[Benutzer]:
    """Aktive Benutzer, deren Rolle Admin-Rechte hat (für die Lockout-Sperre)."""
    admin_rollen = {
        r.name for r in session.exec(select(Rollenprofil).where(Rollenprofil.ist_admin)).all()
    }
    # Fallback für Bestände ohne Rollenprofil: Rollenname "admin" gilt als Admin.
    admin_rollen.add("admin")
    return [
        b
        for b in session.exec(select(Benutzer).where(Benutzer.aktiv)).all()
        if b.rolle in admin_rollen
    ]


@router.post("/rollen/neu")
async def rolle_neu(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    form = await request.form()
    name = (form.get("name") or "").strip()
    rolle = _rolle_aus_form(
        Rollenprofil(name=name), form, darf_admin_setzen=ist_admin(session, benutzer)
    )
    fehler = None
    if not name:
        fehler = "Name darf nicht leer sein."
    elif session.exec(select(Rollenprofil).where(Rollenprofil.name == name)).first() is not None:
        fehler = f"Rolle „{name}“ existiert bereits."
    if fehler:
        return templates.TemplateResponse(
            request,
            "benutzer/rolle_form.html",
            _rolle_kontext(rolle, neu=True, fehler=fehler),
        )
    session.add(rolle)
    session.commit()
    session.refresh(rolle)
    return RedirectResponse(f"/benutzer/rollen/{rolle.id}", status_code=status.HTTP_302_FOUND)


def _lade_rolle(session: Session, rolle_id: int) -> Rollenprofil:
    obj = session.get(Rollenprofil, rolle_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rolle nicht gefunden"
        )
    return obj


@router.get("/rollen/{rolle_id}")
def rolle_bearbeiten_form(
    request: Request,
    rolle_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rolle = _lade_rolle(session, rolle_id)
    return templates.TemplateResponse(
        request,
        "benutzer/rolle_form.html",
        _rolle_kontext(
            rolle, neu=False, in_benutzung=_rolle_in_benutzung(session, rolle.name)
        ),
    )


@router.post("/rollen/{rolle_id}")
async def rolle_bearbeiten(
    request: Request,
    rolle_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rolle = _lade_rolle(session, rolle_id)
    form = await request.form()
    # Standard-Rollen: Name nicht änderbar; Rechte dürfen angepasst werden.
    if not rolle.ist_standard:
        neuer_name = (form.get("name") or "").strip()
        if neuer_name and neuer_name != rolle.name:
            konflikt = session.exec(
                select(Rollenprofil).where(Rollenprofil.name == neuer_name)
            ).first()
            if konflikt is not None and konflikt.id != rolle.id:
                return templates.TemplateResponse(
                    request,
                    "benutzer/rolle_form.html",
                    _rolle_kontext(
                        rolle, neu=False, fehler=f"Rolle „{neuer_name}“ existiert bereits."
                    ),
                )
            rolle.name = neuer_name
    _rolle_aus_form(rolle, form, darf_admin_setzen=ist_admin(session, benutzer))
    session.add(rolle)
    session.commit()
    return RedirectResponse(
        f"/benutzer/rollen/{rolle_id}", status_code=status.HTTP_302_FOUND
    )


@router.post("/rollen/{rolle_id}/loeschen")
def rolle_loeschen(
    request: Request,
    rolle_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rolle = _lade_rolle(session, rolle_id)
    if rolle.ist_standard or _rolle_in_benutzung(session, rolle.name):
        grund = (
            "Standard-Rollen können nicht gelöscht werden."
            if rolle.ist_standard
            else "Rolle wird noch von Mitarbeitern verwendet."
        )
        return templates.TemplateResponse(
            request,
            "benutzer/rollen.html",
            {"rollen": _rollen(session), "fehler": grund},
        )
    session.delete(rolle)
    session.commit()
    return RedirectResponse("/benutzer/rollen", status_code=status.HTTP_302_FOUND)


# --- A) Mitarbeiter: Bearbeiten (dynamische /{benutzer_id}-Routen zuletzt) -----


def _lade_benutzer(session: Session, benutzer_id: int) -> Benutzer:
    obj = session.get(Benutzer, benutzer_id)
    if obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Benutzer nicht gefunden"
        )
    return obj


@router.get("/{benutzer_id}")
def benutzer_bearbeiten_form(
    request: Request,
    benutzer_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    obj = _lade_benutzer(session, benutzer_id)
    return templates.TemplateResponse(
        request,
        "benutzer/form.html",
        {
            "benutzer": obj,
            "neu": False,
            "rollen": _rollen(session),
            "ist_selbst": obj.id == benutzer.id,
        },
    )


@router.post("/{benutzer_id}")
def benutzer_bearbeiten(
    request: Request,
    benutzer_id: int,
    name: str = Form(...),
    rolle: str = Form(""),
    passwort: str = Form(""),
    aktiv: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    obj = _lade_benutzer(session, benutzer_id)
    neu_aktiv = True if obj.id == benutzer.id else aktiv
    # Aussperr-Schutz: der letzte aktive Admin darf weder deaktiviert noch auf
    # eine Rolle ohne Admin-Rechte umgestellt werden.
    admins = _aktive_admins(session)
    ist_letzter_admin = len(admins) == 1 and admins[0].id == obj.id
    verliert_admin = ist_letzter_admin and (not neu_aktiv or rolle != obj.rolle)
    if verliert_admin:
        return templates.TemplateResponse(
            request,
            "benutzer/form.html",
            {
                "benutzer": obj,
                "neu": False,
                "rollen": _rollen(session),
                "ist_selbst": obj.id == benutzer.id,
                "fehler": (
                    "Das ist der letzte aktive Administrator — Rolle und Status "
                    "können nicht geändert werden. Zuerst einen weiteren Admin anlegen."
                ),
            },
        )
    obj.name = name.strip()
    obj.rolle = rolle
    # Sich selbst nicht deaktivieren — eigene Deaktivierung wird ignoriert.
    obj.aktiv = neu_aktiv
    if passwort.strip():
        obj.passwort_hash = hash_passwort(passwort)
    session.add(obj)
    session.commit()
    return RedirectResponse(f"/benutzer/{benutzer_id}", status_code=status.HTTP_302_FOUND)
