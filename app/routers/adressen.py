"""Adressmanager (Kplus-Stil): Liste mit Livesuche, Anlegen, Detail mit Reitern,
Ansprechpartner und Lieferadressen als HTMX-Partials."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.formparse import parse_prozent
from app.models import (
    AKTIVITAET_LABELS,
    Adresse,
    AdressTyp,
    Aktivitaet,
    Anlage,
    Ansprechpartner,
    Beleg,
    Belegart,
    BelegStatus,
    Benutzer,
    Lieferadresse,
)
from app.pagination import Blaetterwerk, blaettere
from app.routers.belege import BELEGART_LABELS, STATUS_BADGES, STATUS_LABELS
from app.services import adresse_service
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.services.fehler import WerkplusFehler
from app.templating import templates

router = APIRouter(
    prefix="/adressen", tags=["adressen"], dependencies=[Depends(bereich_wache("adressen"))]
)

SEITENGROESSE = 50

TYP_FILTER = (AdressTyp.KUNDE, AdressTyp.LIEFERANT, AdressTyp.BEIDE)

# Kplus-Adressblöcke: reine Textfelder des Adressformulars (getrimmt übernommen)
TEXTFELDER = (
    "adressnr", "matchcode", "fibunummer", "anrede", "titel", "firma", "name2",
    "briefanrede", "strasse", "plz", "ort", "land", "uid", "email", "telefon",
    "telefon2", "mobil", "fax", "homepage", "branche", "merkmale", "rabattgruppe",
    "notizen",
)
ANREDEN = ("", "Firma", "Herr", "Frau", "Familie", "Herr und Frau")


def _suche_adressen(
    session: Session, q: str, typ: str, seite: int = 1
) -> tuple[list[Adresse], Blaetterwerk]:
    """Eine Seite Adressen: Suche über Nummer, Matchcode, Namen, Ansprechpartner,
    Ort, PLZ, Telefon, E-Mail + Typ-Filter."""
    stmt = select(Adresse)
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            col(Adresse.adressnr).ilike(muster)
            | col(Adresse.matchcode).ilike(muster)
            | col(Adresse.firma).ilike(muster)
            | col(Adresse.name2).ilike(muster)
            | col(Adresse.ansprechpartner).ilike(muster)
            | col(Adresse.ort).ilike(muster)
            | col(Adresse.plz).ilike(muster)
            | col(Adresse.telefon).ilike(muster)
            | col(Adresse.email).ilike(muster)
        )
    if typ in TYP_FILTER:
        stmt = stmt.where(Adresse.typ == typ)
    return blaettere(
        session,
        stmt.order_by(Adresse.firma),
        seite,
        groesse=SEITENGROESSE,
        basis_url="/adressen",
        params={"q": q, "typ": typ},
        htmx_url="/adressen/suche",
        htmx_target="#adressen-tabelle",
    )


def _int(wert, standard: int) -> int:
    try:
        return int(str(wert).strip())
    except (TypeError, ValueError):
        return standard


def _adresse_aus_form(adresse: Adresse, formular) -> Adresse:
    """Formularwerte in die Adresse übernehmen (neu + bearbeiten).

    ``formular`` ist das rohe Starlette-Formular; fehlende Felder bleiben
    beim Bearbeiten leer bzw. auf Standard — genau wie ein leeres Feld.
    """
    typ = str(formular.get("typ", "kunde"))
    adresse.typ = AdressTyp(typ) if typ in set(AdressTyp) else AdressTyp.KUNDE
    for feld in TEXTFELDER:
        setattr(adresse, feld, str(formular.get(feld, "") or "").strip())
    adresse.zahlungsziel_tage = max(0, _int(formular.get("zahlungsziel_tage"), 14))
    adresse.skonto_tage = max(0, _int(formular.get("skonto_tage"), 0))
    # Wird als Positionsrabatt vorbelegt — ein unsinniger Wert würde sich sonst
    # in jeden neuen Beleg fortschreiben.
    adresse.standard_rabatt_prozent = parse_prozent(
        str(formular.get("standard_rabatt_prozent", "0") or "0"), "Standard-Rabatt"
    )
    adresse.skonto_prozent = parse_prozent(
        str(formular.get("skonto_prozent", "0") or "0"), "Skonto"
    )
    adresse.aktiv = str(formular.get("aktiv", "")).lower() in ("true", "on", "1")
    return adresse


def _finde_dubletten(
    session: Session, firma: str, uid: str, eigene_id: int | None
) -> list[Adresse]:
    """Weiche Dublettenprüfung: gleiche Firma (case-insensitive) ODER gleiche, nicht-leere UID.

    Firma ist NICHT unique-constrained → nur Warnung, kein harter Fehler. Beim
    Bearbeiten wird die Adresse selbst über eigene_id ausgenommen.
    """
    firma = firma.strip()
    uid = uid.strip()
    bedingungen = []
    if firma:
        bedingungen.append(col(Adresse.firma).ilike(firma))  # ohne % = exakt, case-insensitive
    if uid:
        bedingungen.append(col(Adresse.uid).ilike(uid))
    if not bedingungen:
        return []
    stmt = select(Adresse).where(or_(*bedingungen))
    if eigene_id is not None:
        stmt = stmt.where(col(Adresse.id) != eigene_id)
    return list(session.exec(stmt.order_by(col(Adresse.firma))).all())


def _form_kontext(adresse: Adresse, **extra) -> dict:
    return {"adresse": adresse, "anreden": ANREDEN, **extra}


@router.get("")
def adressen_liste(
    request: Request,
    q: str = "",
    typ: str = "alle",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adressen, pag = _suche_adressen(session, q, typ, seite)
    return templates.TemplateResponse(
        request,
        "adressen/liste.html",
        {
            "adressen": adressen,
            "pag": pag,
            "gefiltert": bool(q.strip()) or typ in TYP_FILTER,
            "q": q,
            "typ": typ,
        },
    )


@router.get("/suche")
def adressen_suche(
    request: Request,
    q: str = "",
    typ: str = "alle",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen (inkl. Blätter-Zeile)."""
    adressen, pag = _suche_adressen(session, q, typ, seite)
    return templates.TemplateResponse(
        request,
        "partials/adressen_zeilen.html",
        {
            "adressen": adressen,
            "pag": pag,
            "gefiltert": bool(q.strip()) or typ in TYP_FILTER,
        },
    )


@router.get("/neu")
def adresse_neu_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request, "adressen/form.html", _form_kontext(Adresse(firma=""), neu=True)
    )


@router.post("/neu")
async def adresse_neu(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    formular = await request.form()
    adresse = _adresse_aus_form(Adresse(firma=""), formular)
    if not adresse.firma:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name fehlt")
    trotzdem = str(formular.get("trotzdem_speichern", "")).lower() == "true"
    # Adressnummer: leer → automatisch; manuell → muss frei sein (harter Fehler)
    if adresse.adressnr and adresse_service.adressnr_vergeben(session, adresse.adressnr):
        return templates.TemplateResponse(
            request,
            "adressen/form.html",
            _form_kontext(
                adresse, neu=True,
                fehler=f"Adressnummer {adresse.adressnr} ist bereits vergeben",
            ),
            status_code=status.HTTP_409_CONFLICT,
        )
    if not trotzdem:
        dubletten = _finde_dubletten(session, adresse.firma, adresse.uid, None)
        if dubletten:
            return templates.TemplateResponse(
                request, "adressen/form.html", _form_kontext(adresse, neu=True, dubletten=dubletten)
            )
    if not adresse.adressnr:
        adresse.adressnr = adresse_service.naechste_adressnr(session)
    session.add(adresse)
    session.flush()
    # Schnellerfassung: ein Ansprechpartner-Name im Neu-Formular wird zum Hauptkontakt
    kontakt_name = str(formular.get("ansprechpartner", "") or "").strip()
    if kontakt_name:
        session.add(
            Ansprechpartner(adresse_id=adresse.id, nachname=kontakt_name, ist_hauptkontakt=True)
        )
        session.flush()
    adresse_service.synchronisiere_hauptkontakt(session, adresse)
    session.commit()
    return RedirectResponse(f"/adressen/{adresse.id}", status_code=status.HTTP_302_FOUND)


def _lade_adresse(session: Session, adresse_id: int) -> Adresse:
    adresse = session.get(Adresse, adresse_id)
    if adresse is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adresse nicht gefunden")
    return adresse


def _kennzahlen(session: Session, adresse: Adresse) -> dict:
    """Kunden-Cockpit-Kennzahlen für die Detailseite (eigene Queries, kein Import aus belege).

    Bei transienter Adresse ohne id (Dubletten-Bearbeiten-Fehlerpfad kann ein
    Anzeige-Objekt mit gesetzter id haben) werden neutrale Nullwerte geliefert.
    """
    leer = {
        "jahr": date.today().year,
        "jahresumsatz": Decimal("0"),
        "offene_posten_anzahl": 0,
        "offene_posten_summe": Decimal("0"),
        "anlagen_anzahl": 0,
        "letzter_beleg_datum": None,
        "offene_angebote_anzahl": 0,
    }
    if adresse.id is None:
        return leer

    jahr = date.today().year
    verkaufte_status = (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT)

    # Rechnungen dieser Adresse mit "verkauftem" Status
    rechnungen = list(
        session.exec(
            select(Beleg).where(
                Beleg.adresse_id == adresse.id,
                Beleg.belegart == Belegart.RECHNUNG,
                col(Beleg.status).in_(verkaufte_status),
            )
        ).all()
    )

    jahresumsatz = sum(
        (r.summe_netto for r in rechnungen if r.datum.year == jahr), Decimal("0")
    )
    offene = [r for r in rechnungen if r.bezahlt_am is None]
    offene_posten_summe = sum((r.summe_brutto for r in offene), Decimal("0"))

    anlagen_anzahl = len(
        session.exec(
            select(Anlage.id).where(
                Anlage.adresse_id == adresse.id,
                col(Anlage.aktiv).is_(True),
            )
        ).all()
    )

    letzter_beleg = session.exec(
        select(Beleg)
        .where(Beleg.adresse_id == adresse.id)
        .order_by(col(Beleg.datum).desc())
    ).first()

    offene_angebote_anzahl = len(
        session.exec(
            select(Beleg.id).where(
                Beleg.adresse_id == adresse.id,
                Beleg.belegart == Belegart.ANGEBOT,
                col(Beleg.status).in_((BelegStatus.ENTWURF, BelegStatus.GEDRUCKT)),
                col(Beleg.ist_muster).is_(False),
            )
        ).all()
    )

    return {
        "jahr": jahr,
        "jahresumsatz": jahresumsatz,
        "offene_posten_anzahl": len(offene),
        "offene_posten_summe": offene_posten_summe,
        "anlagen_anzahl": anlagen_anzahl,
        "letzter_beleg_datum": letzter_beleg.datum if letzter_beleg else None,
        "offene_angebote_anzahl": offene_angebote_anzahl,
    }


def _aktivitaeten_kontext(session: Session, adresse_id: int) -> dict:
    """Aktivitäten samt Namen der Bearbeiter und Belegnummern (für die Liste)."""
    aktivitaeten = list(
        session.exec(
            select(Aktivitaet)
            .where(Aktivitaet.adresse_id == adresse_id)
            .order_by(col(Aktivitaet.erledigt), col(Aktivitaet.zeitstempel).desc())
        ).all()
    )
    benutzer_ids = {a.benutzer_id for a in aktivitaeten if a.benutzer_id}
    beleg_ids = {a.beleg_id for a in aktivitaeten if a.beleg_id}
    namen: dict[int, str] = {}
    if benutzer_ids:
        stmt = select(Benutzer).where(col(Benutzer.id).in_(benutzer_ids))
        namen = {b.id: b.name for b in session.exec(stmt).all()}
    belegnrn: dict[int, str] = {}
    if beleg_ids:
        stmt = select(Beleg).where(col(Beleg.id).in_(beleg_ids))
        belegnrn = {b.id: b.belegnr for b in session.exec(stmt).all()}
    return {
        "aktivitaeten": aktivitaeten,
        "akt_benutzer": namen,
        "akt_belegnr": belegnrn,
        "aktivitaet_labels": AKTIVITAET_LABELS,
        "heute": date.today(),
        "adresse_id": adresse_id,
    }


def _detail_kontext(session: Session, adresse: Adresse) -> dict:
    belege = list(
        session.exec(
            select(Beleg)
            .where(Beleg.adresse_id == adresse.id, col(Beleg.ist_muster).is_(False))
            .order_by(col(Beleg.datum).desc())
        ).all()
    )
    anlagen = list(
        session.exec(
            select(Anlage).where(Anlage.adresse_id == adresse.id).order_by(col(Anlage.id).desc())
        ).all()
    )
    return {
        **_form_kontext(adresse),
        "belege": belege,
        "anlagen": anlagen,
        "ansprechpartner": adresse_service.ansprechpartner_liste(session, adresse.id),
        "lieferadressen": adresse_service.lieferadressen(session, adresse.id),
        "belegart_labels": BELEGART_LABELS,
        "status_labels": STATUS_LABELS,
        "status_badges": STATUS_BADGES,
        "kennzahlen": _kennzahlen(session, adresse),
        **_aktivitaeten_kontext(session, adresse.id),
    }


@router.get("/{adresse_id}")
def adresse_detail(
    request: Request,
    adresse_id: int,
    tab: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    kontext = _detail_kontext(session, adresse)
    kontext["aktiver_tab"] = tab or "stammdaten"
    return templates.TemplateResponse(request, "adressen/detail.html", kontext)


@router.post("/{adresse_id}")
async def adresse_bearbeiten(
    request: Request,
    adresse_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    formular = await request.form()
    trotzdem = str(formular.get("trotzdem_speichern", "")).lower() == "true"
    # Eingegebene (noch nicht gespeicherte) Werte für eine Wiederanzeige,
    # ohne die geladene Adresse zu verändern (transientes Objekt).
    anzeige = _adresse_aus_form(Adresse(firma=""), formular)
    anzeige.id = adresse_id
    anzeige.ansprechpartner = adresse.ansprechpartner
    if not anzeige.firma:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name fehlt")
    fehler = ""
    if anzeige.adressnr and adresse_service.adressnr_vergeben(
        session, anzeige.adressnr, adresse_id
    ):
        fehler = f"Adressnummer {anzeige.adressnr} ist bereits vergeben"
    dubletten = [] if (trotzdem or fehler) else _finde_dubletten(
        session, anzeige.firma, anzeige.uid, adresse_id
    )
    if fehler or dubletten:
        kontext = _detail_kontext(session, adresse)
        kontext.update({"adresse": anzeige, "dubletten": dubletten, "fehler": fehler,
                        "aktiver_tab": "stammdaten"})
        return templates.TemplateResponse(
            request, "adressen/detail.html", kontext,
            status_code=status.HTTP_409_CONFLICT if fehler else status.HTTP_200_OK,
        )
    _adresse_aus_form(adresse, formular)
    if not adresse.adressnr:
        adresse.adressnr = adresse_service.naechste_adressnr(session)
    session.add(adresse)
    session.commit()
    return RedirectResponse(f"/adressen/{adresse_id}", status_code=status.HTTP_302_FOUND)


# ------------------------------------------------------------ Ansprechpartner


def _ap_liste_response(request: Request, session: Session, adresse: Adresse):
    return templates.TemplateResponse(
        request,
        "partials/ansprechpartner_liste.html",
        {
            "adresse": adresse,
            "ansprechpartner": adresse_service.ansprechpartner_liste(session, adresse.id),
            "anreden": ANREDEN,
        },
    )


def _lade_kontakt(session: Session, adresse: Adresse, kontakt_id: int) -> Ansprechpartner:
    kontakt = session.get(Ansprechpartner, kontakt_id)
    if kontakt is None or kontakt.adresse_id != adresse.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ansprechpartner nicht gefunden"
        )
    return kontakt


def _kontakt_aus_form(kontakt: Ansprechpartner, formular) -> Ansprechpartner:
    for feld in ("anrede", "vorname", "nachname", "funktion", "abteilung", "telefon",
                 "mobil", "email", "notiz"):
        setattr(kontakt, feld, str(formular.get(feld, "") or "").strip())
    kontakt.aktiv = str(formular.get("aktiv", "true")).lower() in ("true", "on", "1")
    return kontakt


@router.get("/{adresse_id}/ansprechpartner/{kontakt_id}/form")
def ansprechpartner_form(
    request: Request,
    adresse_id: int,
    kontakt_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: Bearbeitungsformular eines Ansprechpartners."""
    adresse = _lade_adresse(session, adresse_id)
    kontakt = _lade_kontakt(session, adresse, kontakt_id)
    return templates.TemplateResponse(
        request,
        "partials/ansprechpartner_form.html",
        {"adresse": adresse, "kontakt": kontakt, "anreden": ANREDEN},
    )


@router.post("/{adresse_id}/ansprechpartner")
async def ansprechpartner_anlegen(
    request: Request,
    adresse_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    formular = await request.form()
    kontakt = _kontakt_aus_form(Ansprechpartner(adresse_id=adresse.id), formular)
    if not (kontakt.nachname or kontakt.vorname):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name fehlt")
    kontakt.ist_hauptkontakt = str(formular.get("ist_hauptkontakt", "")).lower() in ("true", "on")
    session.add(kontakt)
    session.flush()
    if kontakt.ist_hauptkontakt:
        adresse_service.setze_hauptkontakt(session, adresse, kontakt)
    else:
        adresse_service.synchronisiere_hauptkontakt(session, adresse)
    session.commit()
    return _ap_liste_response(request, session, adresse)


@router.post("/{adresse_id}/ansprechpartner/{kontakt_id}")
async def ansprechpartner_bearbeiten(
    request: Request,
    adresse_id: int,
    kontakt_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    kontakt = _lade_kontakt(session, adresse, kontakt_id)
    formular = await request.form()
    _kontakt_aus_form(kontakt, formular)
    if not (kontakt.nachname or kontakt.vorname):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name fehlt")
    session.add(kontakt)
    session.flush()
    if str(formular.get("ist_hauptkontakt", "")).lower() in ("true", "on"):
        adresse_service.setze_hauptkontakt(session, adresse, kontakt)
    else:
        adresse_service.synchronisiere_hauptkontakt(session, adresse)
    session.commit()
    return _ap_liste_response(request, session, adresse)


@router.post("/{adresse_id}/ansprechpartner/{kontakt_id}/haupt")
def ansprechpartner_haupt(
    request: Request,
    adresse_id: int,
    kontakt_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    kontakt = _lade_kontakt(session, adresse, kontakt_id)
    adresse_service.setze_hauptkontakt(session, adresse, kontakt)
    session.commit()
    return _ap_liste_response(request, session, adresse)


@router.post("/{adresse_id}/ansprechpartner/{kontakt_id}/loeschen")
def ansprechpartner_loeschen(
    request: Request,
    adresse_id: int,
    kontakt_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    kontakt = _lade_kontakt(session, adresse, kontakt_id)
    try:
        adresse_service.loesche_ansprechpartner(session, adresse, kontakt)
    except WerkplusFehler as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.commit()
    return _ap_liste_response(request, session, adresse)


# ------------------------------------------------------------- Lieferadressen


def _la_liste_response(request: Request, session: Session, adresse: Adresse):
    return templates.TemplateResponse(
        request,
        "partials/lieferadressen_liste.html",
        {"adresse": adresse, "lieferadressen": adresse_service.lieferadressen(session, adresse.id)},
    )


def _lade_lieferadresse(session: Session, adresse: Adresse, la_id: int) -> Lieferadresse:
    la = session.get(Lieferadresse, la_id)
    if la is None or la.adresse_id != adresse.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lieferadresse nicht gefunden"
        )
    return la


def _lieferadresse_aus_form(la: Lieferadresse, formular) -> Lieferadresse:
    for feld in ("bezeichnung", "name", "strasse", "plz", "ort", "land", "notiz"):
        setattr(la, feld, str(formular.get(feld, "") or "").strip())
    if not la.land:
        la.land = "Österreich"
    return la


@router.get("/{adresse_id}/lieferadressen/{la_id}/form")
def lieferadresse_form(
    request: Request,
    adresse_id: int,
    la_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    la = _lade_lieferadresse(session, adresse, la_id)
    return templates.TemplateResponse(
        request, "partials/lieferadresse_form.html", {"adresse": adresse, "la": la}
    )


@router.post("/{adresse_id}/lieferadressen")
async def lieferadresse_anlegen(
    request: Request,
    adresse_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    formular = await request.form()
    la = _lieferadresse_aus_form(Lieferadresse(adresse_id=adresse.id), formular)
    if not (la.bezeichnung or la.name or la.ort):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Bezeichnung oder Ort fehlt"
        )
    session.add(la)
    session.commit()
    return _la_liste_response(request, session, adresse)


@router.post("/{adresse_id}/lieferadressen/{la_id}")
async def lieferadresse_bearbeiten(
    request: Request,
    adresse_id: int,
    la_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    la = _lade_lieferadresse(session, adresse, la_id)
    _lieferadresse_aus_form(la, await request.form())
    session.add(la)
    session.commit()
    return _la_liste_response(request, session, adresse)


@router.post("/{adresse_id}/lieferadressen/{la_id}/loeschen")
def lieferadresse_loeschen(
    request: Request,
    adresse_id: int,
    la_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    la = _lade_lieferadresse(session, adresse, la_id)
    # Belege behalten ihre lieferadresse_id nur, solange die Adresse existiert
    for beleg in session.exec(select(Beleg).where(Beleg.lieferadresse_id == la.id)).all():
        beleg.lieferadresse_id = None
        session.add(beleg)
    session.delete(la)
    session.commit()
    return _la_liste_response(request, session, adresse)
