"""Adress-Stammdaten: Liste mit Livesuche, Anlegen, Detail mit Tabs."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user
from app.formparse import parse_prozent
from app.models import (
    Adresse,
    AdressTyp,
    Aktivitaet,
    Anlage,
    Artikel,
    Beleg,
    Belegart,
    BelegStatus,
    Benutzer,
)
from app.pagination import Blaetterwerk, blaettere
from app.routers.belege import BELEGART_LABELS, STATUS_BADGES, STATUS_LABELS
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.templating import templates

router = APIRouter(
    prefix="/adressen", tags=["adressen"], dependencies=[Depends(bereich_wache("adressen"))]
)

SEITENGROESSE = 50

TYP_FILTER = (AdressTyp.KUNDE, AdressTyp.LIEFERANT, AdressTyp.BEIDE)


def _suche_adressen(
    session: Session, q: str, typ: str, seite: int = 1
) -> tuple[list[Adresse], Blaetterwerk]:
    """Eine Seite Adressen: Suche über firma, ansprechpartner, ort + Typ-Filter."""
    stmt = select(Adresse)
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            Adresse.firma.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.ansprechpartner.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.ort.ilike(muster)  # type: ignore[attr-defined]
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


def _adresse_aus_form(  # noqa: PLR0913
    adresse: Adresse,
    typ: str,
    firma: str,
    ansprechpartner: str,
    strasse: str,
    plz: str,
    ort: str,
    land: str,
    uid: str,
    email: str,
    telefon: str,
    zahlungsziel_tage: int,
    rabattgruppe: str,
    standard_rabatt_prozent: str,
    notizen: str,
    aktiv: bool,
) -> Adresse:
    adresse.typ = AdressTyp(typ)
    adresse.firma = firma.strip()
    adresse.ansprechpartner = ansprechpartner.strip()
    adresse.strasse = strasse.strip()
    adresse.plz = plz.strip()
    adresse.ort = ort.strip()
    adresse.land = land.strip()
    adresse.uid = uid.strip()
    adresse.email = email.strip()
    adresse.telefon = telefon.strip()
    adresse.zahlungsziel_tage = zahlungsziel_tage
    adresse.rabattgruppe = rabattgruppe.strip()
    # Wird als Positionsrabatt vorbelegt — ein unsinniger Wert würde sich sonst
    # in jeden neuen Beleg fortschreiben.
    adresse.standard_rabatt_prozent = parse_prozent(
        standard_rabatt_prozent, "Standard-Rabatt"
    )
    adresse.notizen = notizen.strip()
    adresse.aktiv = aktiv
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
        request, "adressen/form.html", {"adresse": Adresse(firma=""), "neu": True}
    )


@router.post("/neu")
def adresse_neu(  # noqa: PLR0913
    request: Request,
    typ: str = Form("kunde"),
    firma: str = Form(...),
    ansprechpartner: str = Form(""),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    land: str = Form("Österreich"),
    uid: str = Form(""),
    email: str = Form(""),
    telefon: str = Form(""),
    zahlungsziel_tage: int = Form(14),
    rabattgruppe: str = Form(""),
    standard_rabatt_prozent: str = Form("0"),
    notizen: str = Form(""),
    aktiv: bool = Form(True),
    trotzdem_speichern: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _adresse_aus_form(
        Adresse(firma=""),
        typ,
        firma,
        ansprechpartner,
        strasse,
        plz,
        ort,
        land,
        uid,
        email,
        telefon,
        zahlungsziel_tage,
        rabattgruppe,
        standard_rabatt_prozent,
        notizen,
        aktiv,
    )
    if not trotzdem_speichern:
        dubletten = _finde_dubletten(session, firma, uid, None)
        if dubletten:
            return templates.TemplateResponse(
                request,
                "adressen/form.html",
                {"adresse": adresse, "neu": True, "dubletten": dubletten},
            )
    session.add(adresse)
    session.commit()
    session.refresh(adresse)
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


def _detail_kontext(session: Session, adresse: Adresse) -> dict:
    belege = list(
        session.exec(
            select(Beleg).where(Beleg.adresse_id == adresse.id).order_by(Beleg.datum.desc())  # type: ignore[union-attr]
        ).all()
    )
    anlagen = list(
        session.exec(
            select(Anlage, Artikel)
            .join(Artikel, Anlage.artikel_id == Artikel.id)  # type: ignore[arg-type]
            .where(Anlage.adresse_id == adresse.id)
        ).all()
    )
    aktivitaeten = list(
        session.exec(
            select(Aktivitaet)
            .where(Aktivitaet.adresse_id == adresse.id)
            .order_by(Aktivitaet.zeitstempel.desc())  # type: ignore[union-attr]
        ).all()
    )
    return {
        "adresse": adresse,
        "belege": belege,
        "anlagen": anlagen,
        "aktivitaeten": aktivitaeten,
        "belegart_labels": BELEGART_LABELS,
        "status_labels": STATUS_LABELS,
        "status_badges": STATUS_BADGES,
        "kennzahlen": _kennzahlen(session, adresse),
    }


@router.get("/{adresse_id}")
def adresse_detail(
    request: Request,
    adresse_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    return templates.TemplateResponse(
        request, "adressen/detail.html", _detail_kontext(session, adresse)
    )


@router.post("/{adresse_id}")
def adresse_bearbeiten(  # noqa: PLR0913
    request: Request,
    adresse_id: int,
    typ: str = Form("kunde"),
    firma: str = Form(...),
    ansprechpartner: str = Form(""),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    land: str = Form("Österreich"),
    uid: str = Form(""),
    email: str = Form(""),
    telefon: str = Form(""),
    zahlungsziel_tage: int = Form(14),
    rabattgruppe: str = Form(""),
    standard_rabatt_prozent: str = Form("0"),
    notizen: str = Form(""),
    aktiv: bool = Form(False),
    trotzdem_speichern: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    adresse = _lade_adresse(session, adresse_id)
    if not trotzdem_speichern:
        dubletten = _finde_dubletten(session, firma, uid, adresse_id)
        if dubletten:
            # Eingegebene (noch nicht gespeicherte) Werte für die Wiederanzeige,
            # ohne die geladene Adresse zu verändern (transientes Objekt).
            anzeige = _adresse_aus_form(
                Adresse(firma=""),
                typ,
                firma,
                ansprechpartner,
                strasse,
                plz,
                ort,
                land,
                uid,
                email,
                telefon,
                zahlungsziel_tage,
                rabattgruppe,
                standard_rabatt_prozent,
                notizen,
                aktiv,
            )
            anzeige.id = adresse_id
            kontext = _detail_kontext(session, adresse)
            kontext["adresse"] = anzeige
            kontext["dubletten"] = dubletten
            return templates.TemplateResponse(request, "adressen/detail.html", kontext)
    _adresse_aus_form(
        adresse,
        typ,
        firma,
        ansprechpartner,
        strasse,
        plz,
        ort,
        land,
        uid,
        email,
        telefon,
        zahlungsziel_tage,
        rabattgruppe,
        standard_rabatt_prozent,
        notizen,
        aktiv,
    )
    session.add(adresse)
    session.commit()
    return RedirectResponse(f"/adressen/{adresse_id}", status_code=status.HTTP_302_FOUND)
