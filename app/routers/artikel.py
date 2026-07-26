"""Artikel-Stammdaten: Liste mit Livesuche und Bestandsspalte, Anlegen, Detail."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user
from app.formparse import (
    MAX_AUFSCHLAG_PROZENT,
    parse_betrag,
    parse_menge,
    parse_prozent,
)
from app.models import (
    UST_SAETZE,
    Artikel,
    Benutzer,
    Lager,
    Lagerbestand,
    Seriennummer,
    SetKomponente,
)
from app.pagination import Blaetterwerk, blaettere
from app.services import artikel_set_service, berechnung
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.templating import templates

router = APIRouter(
    prefix="/artikel", tags=["artikel"], dependencies=[Depends(bereich_wache("artikel"))]
)

SEITENGROESSE = 50


def _suche_artikel(session: Session, q: str, seite: int = 1) -> tuple[list[Artikel], Blaetterwerk]:
    """Eine Seite Artikel (Filter: Artikelnr, Bezeichnung, Warengruppe) samt Blätterwerk."""
    stmt = select(Artikel)
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            Artikel.artikelnr.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.bezeichnung.ilike(muster)  # type: ignore[attr-defined]
            | Artikel.warengruppe.ilike(muster)  # type: ignore[attr-defined]
        )
    return blaettere(
        session,
        stmt.order_by(Artikel.artikelnr),
        seite,
        groesse=SEITENGROESSE,
        basis_url="/artikel",
        params={"q": q},
        htmx_url="/artikel/suche",
        htmx_target="#artikel-tabelle",
    )


def _bestaende_je_artikel(
    session: Session, artikel_ids: list[int] | None = None
) -> dict[int, Decimal]:
    """Summe Lagerbestand.menge je Artikel — Decimal-sicher in Python summiert.

    Mit ``artikel_ids`` werden nur die Bestände der angezeigten Seite geladen.
    """
    if artikel_ids is not None and not artikel_ids:
        return {}
    stmt = select(Lagerbestand)
    if artikel_ids is not None:
        stmt = stmt.where(Lagerbestand.artikel_id.in_(artikel_ids))  # type: ignore[attr-defined]
    summen: dict[int, Decimal] = {}
    for bestand in session.exec(stmt).all():
        summen[bestand.artikel_id] = summen.get(bestand.artikel_id, Decimal("0")) + bestand.menge
    return summen


def _artikelnr_vergeben(session: Session, artikelnr: str, ausser_id: int | None = None) -> bool:
    """Prüft, ob die Artikelnummer bereits von einem (anderen) Artikel belegt ist."""
    stmt = select(Artikel).where(Artikel.artikelnr == artikelnr)
    if ausser_id is not None:
        stmt = stmt.where(Artikel.id != ausser_id)
    return session.exec(stmt).first() is not None


def _artikel_aus_form(
    artikel: Artikel,
    daten: dict,
) -> Artikel:
    ust_satz = int(daten["ust_satz"])
    if ust_satz not in UST_SAETZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unzulässiger USt-Satz: {ust_satz}",
        )
    artikel.artikelnr = daten["artikelnr"].strip()
    artikel.bezeichnung = daten["bezeichnung"].strip()
    artikel.langtext = daten["langtext"].strip()
    artikel.warengruppe = daten["warengruppe"].strip()
    artikel.einheit = daten["einheit"].strip() or "Stk"
    artikel.ust_satz = ust_satz
    # Kalkulation: EK ist rein abgeleitet (Server autoritativ); VK per Kalkulation
    # vorbelegt, aber überschreibbar — ein eingegebener VK > 0 wird übernommen.
    artikel.listenpreis_netto = parse_betrag(daten["listenpreis_netto"], "Listenpreis")
    artikel.rabatt1_prozent = parse_prozent(daten["rabatt1_prozent"], "EK-Rabatt 1")
    artikel.rabatt2_prozent = parse_prozent(daten["rabatt2_prozent"], "EK-Rabatt 2")
    artikel.aufschlag_prozent = parse_prozent(
        daten["aufschlag_prozent"], "Aufschlag",
        min_wert=Decimal("-100"), max_wert=MAX_AUFSCHLAG_PROZENT,
    )
    artikel.ek_preis = berechnung.kalkuliere_ek(
        artikel.listenpreis_netto, artikel.rabatt1_prozent, artikel.rabatt2_prozent
    )
    eingegeben_vk = parse_betrag(daten["vk_preis"], "Verkaufspreis")
    artikel.vk_preis = (
        eingegeben_vk
        if eingegeben_vk > 0
        else berechnung.kalkuliere_vk(artikel.ek_preis, artikel.aufschlag_prozent)
    )
    artikel.seriennummernpflichtig = daten["seriennummernpflichtig"]
    artikel.ist_lagerartikel = daten["ist_lagerartikel"]
    artikel.ist_set = daten["ist_set"]
    artikel.aktiv = daten["aktiv"]
    return artikel


@router.get("")
def artikel_liste(
    request: Request,
    q: str = "",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel, pag = _suche_artikel(session, q, seite)
    return templates.TemplateResponse(
        request,
        "artikel/liste.html",
        {
            "artikel_liste": artikel,
            "bestaende": _bestaende_je_artikel(session, [a.id for a in artikel]),
            "pag": pag,
            "gefiltert": bool(q.strip()),
            "q": q,
        },
    )


@router.get("/suche")
def artikel_suche(
    request: Request,
    q: str = "",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen (inkl. Blätter-Zeile)."""
    artikel, pag = _suche_artikel(session, q, seite)
    return templates.TemplateResponse(
        request,
        "partials/artikel_zeilen.html",
        {
            "artikel_liste": artikel,
            "bestaende": _bestaende_je_artikel(session, [a.id for a in artikel]),
            "pag": pag,
            "gefiltert": bool(q.strip()),
        },
    )


@router.get("/neu")
def artikel_neu_form(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
):
    leer = Artikel(artikelnr="", bezeichnung="")
    return templates.TemplateResponse(
        request,
        "artikel/form.html",
        {"artikel": leer, "neu": True, "ust_saetze": UST_SAETZE},
    )


@router.post("/neu")
def artikel_neu(  # noqa: PLR0913
    request: Request,
    artikelnr: str = Form(...),
    bezeichnung: str = Form(...),
    langtext: str = Form(""),
    warengruppe: str = Form(""),
    einheit: str = Form("Stk"),
    listenpreis_netto: str = Form("0"),
    rabatt1_prozent: str = Form("0"),
    rabatt2_prozent: str = Form("0"),
    aufschlag_prozent: str = Form("0"),
    vk_preis: str = Form("0"),
    ust_satz: int = Form(20),
    seriennummernpflichtig: bool = Form(False),
    ist_lagerartikel: bool = Form(False),
    ist_set: bool = Form(False),
    aktiv: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    daten = {
        "artikelnr": artikelnr,
        "bezeichnung": bezeichnung,
        "langtext": langtext,
        "warengruppe": warengruppe,
        "einheit": einheit,
        "listenpreis_netto": listenpreis_netto,
        "rabatt1_prozent": rabatt1_prozent,
        "rabatt2_prozent": rabatt2_prozent,
        "aufschlag_prozent": aufschlag_prozent,
        "vk_preis": vk_preis,
        "ust_satz": ust_satz,
        "seriennummernpflichtig": seriennummernpflichtig,
        "ist_lagerartikel": ist_lagerartikel,
        "ist_set": ist_set,
        "aktiv": aktiv,
    }
    # Transientes Objekt — wird bei Duplikat nur zur Formular-Anzeige verwendet
    artikel = _artikel_aus_form(Artikel(artikelnr="", bezeichnung=""), daten)
    if _artikelnr_vergeben(session, artikel.artikelnr):
        return templates.TemplateResponse(
            request,
            "artikel/form.html",
            {
                "artikel": artikel,
                "neu": True,
                "ust_saetze": UST_SAETZE,
                "fehler": f"Artikelnummer {artikel.artikelnr} ist bereits vergeben.",
            },
        )
    session.add(artikel)
    session.commit()
    session.refresh(artikel)
    return RedirectResponse(f"/artikel/{artikel.id}", status_code=status.HTTP_302_FOUND)


def _lade_artikel(session: Session, artikel_id: int) -> Artikel:
    artikel = session.get(Artikel, artikel_id)
    if artikel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden")
    return artikel


def _set_daten(session: Session, set_artikel: Artikel) -> dict:
    """Set-Komponenten (mit Komponentenartikel) und auswählbare Nicht-Set-Artikel."""
    zeilen = [
        (komp, session.get(Artikel, komp.komponente_artikel_id))
        for komp in artikel_set_service.komponenten(session, set_artikel.id)
    ]
    verfuegbare = [
        a
        for a in session.exec(select(Artikel).order_by(Artikel.artikelnr)).all()
        if not a.ist_set and a.id != set_artikel.id
    ]
    return {"set_komponenten": zeilen, "verfuegbare_artikel": verfuegbare}


def _detail_daten(session: Session, artikel_id: int) -> dict:
    """Bestände und Seriennummern für die Detailseite eines Artikels."""
    bestaende = list(
        session.exec(
            select(Lagerbestand, Lager)
            .join(Lager, Lagerbestand.lager_id == Lager.id)  # type: ignore[arg-type]
            .where(Lagerbestand.artikel_id == artikel_id)
        ).all()
    )
    seriennummern = list(
        session.exec(
            select(Seriennummer).where(Seriennummer.artikel_id == artikel_id).order_by(
                Seriennummer.sn
            )
        ).all()
    )
    daten = {"bestaende": bestaende, "seriennummern": seriennummern, "ust_saetze": UST_SAETZE}
    artikel = session.get(Artikel, artikel_id)
    if artikel is not None and artikel.ist_set:
        daten.update(_set_daten(session, artikel))
    return daten


@router.get("/{artikel_id}")
def artikel_detail(
    request: Request,
    artikel_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    return templates.TemplateResponse(
        request,
        "artikel/detail.html",
        {"artikel": artikel, **_detail_daten(session, artikel_id)},
    )


@router.post("/{artikel_id}")
def artikel_bearbeiten(  # noqa: PLR0913
    request: Request,
    artikel_id: int,
    artikelnr: str = Form(...),
    bezeichnung: str = Form(...),
    langtext: str = Form(""),
    warengruppe: str = Form(""),
    einheit: str = Form("Stk"),
    listenpreis_netto: str = Form("0"),
    rabatt1_prozent: str = Form("0"),
    rabatt2_prozent: str = Form("0"),
    aufschlag_prozent: str = Form("0"),
    vk_preis: str = Form("0"),
    ust_satz: int = Form(20),
    seriennummernpflichtig: bool = Form(False),
    ist_lagerartikel: bool = Form(False),
    ist_set: bool = Form(False),
    aktiv: bool = Form(False),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    artikel = _lade_artikel(session, artikel_id)
    daten = {
        "artikelnr": artikelnr,
        "bezeichnung": bezeichnung,
        "langtext": langtext,
        "warengruppe": warengruppe,
        "einheit": einheit,
        "listenpreis_netto": listenpreis_netto,
        "rabatt1_prozent": rabatt1_prozent,
        "rabatt2_prozent": rabatt2_prozent,
        "aufschlag_prozent": aufschlag_prozent,
        "vk_preis": vk_preis,
        "ust_satz": ust_satz,
        "seriennummernpflichtig": seriennummernpflichtig,
        "ist_lagerartikel": ist_lagerartikel,
        "ist_set": ist_set,
        "aktiv": aktiv,
    }
    if _artikelnr_vergeben(session, artikelnr.strip(), ausser_id=artikel_id):
        # Transientes Anzeige-Objekt mit den eingegebenen Werten — nichts wird gespeichert
        anzeige = _artikel_aus_form(Artikel(id=artikel_id, artikelnr="", bezeichnung=""), daten)
        return templates.TemplateResponse(
            request,
            "artikel/detail.html",
            {
                "artikel": anzeige,
                **_detail_daten(session, artikel_id),
                "fehler": f"Artikelnummer {anzeige.artikelnr} ist bereits vergeben.",
            },
        )
    _artikel_aus_form(artikel, daten)
    session.add(artikel)
    session.commit()
    return RedirectResponse(f"/artikel/{artikel_id}", status_code=status.HTTP_302_FOUND)


def _set_komponenten_partial(request: Request, session: Session, set_artikel: Artikel):
    return templates.TemplateResponse(
        request,
        "partials/set_komponenten.html",
        {"artikel": set_artikel, **_set_daten(session, set_artikel)},
    )


@router.post("/{artikel_id}/set-komponenten")
def set_komponente_hinzufuegen(
    request: Request,
    artikel_id: int,
    komponente_artikel_id: int = Form(...),
    menge: str = Form("1"),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Fügt dem Set eine Komponente hinzu — nur Nicht-Set-Artikel, nicht sich selbst."""
    set_artikel = _lade_artikel(session, artikel_id)
    komponente = session.get(Artikel, komponente_artikel_id)
    # Nur echte Nicht-Set-Artikel als Komponente; Sets und Selbstbezug still ignorieren.
    if komponente is not None and not komponente.ist_set and komponente.id != set_artikel.id:
        naechster_sort = (
            max(
                (k.sort for k in artikel_set_service.komponenten(session, artikel_id)),
                default=-1,
            )
            + 1
        )
        session.add(
            SetKomponente(
                set_artikel_id=artikel_id,
                komponente_artikel_id=komponente_artikel_id,
                menge=parse_menge(menge),
                sort=naechster_sort,
            )
        )
        session.commit()
    return _set_komponenten_partial(request, session, set_artikel)


@router.post("/{artikel_id}/set-komponenten/{komp_id}/loeschen")
def set_komponente_loeschen(
    request: Request,
    artikel_id: int,
    komp_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Entfernt eine Komponente aus dem Set."""
    set_artikel = _lade_artikel(session, artikel_id)
    komponente = session.get(SetKomponente, komp_id)
    if komponente is not None and komponente.set_artikel_id == artikel_id:
        session.delete(komponente)
        session.commit()
    return _set_komponenten_partial(request, session, set_artikel)
