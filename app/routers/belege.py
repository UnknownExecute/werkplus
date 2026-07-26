"""Beleg-UI: generische Liste, Anlage und Editor für alle Belegarten (Verkauf + Einkauf).

Die Routen sind bewusst dünn — sämtliche Belegwesen-Logik liegt in
app.services.beleg_service; hier passiert nur Formular-Parsing und Rendering.
"""

from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user, require_admin
from app.formparse import (
    MAX_AUFSCHLAG_PROZENT,
    parse_betrag,
    parse_decimal,
    parse_menge,
    parse_prozent,
)
from app.models import (
    UST_SAETZE,
    Adresse,
    AdressTyp,
    Artikel,
    Beleg,
    Belegart,
    BelegPosition,
    BelegStatus,
    Benutzer,
    Rolle,
    Seriennummer,
    Textbaustein,
    TextbausteinKategorie,
    Zugriff,
)
from app.pagination import Blaetterwerk, blaettere
from app.services import (
    artikel_set_service,
    beleg_service,
    berechnung,
    lager_service,
    seriennummer_service,
)
from app.services.berechtigung import (  # noqa: E402
    bereich_wache,
    darf,
    lade_profil,
)
from app.services.fehler import (
    BelegGesperrt,
    LagerbuchungFehlgeschlagen,
    SnNichtVerfuegbar,
    SnPflichtVerletzt,
    StornoNichtMoeglich,
    UngueltigeBelegart,
    UngueltigerStatusUebergang,
    WerkplusFehler,
)
from app.templating import templates

router = APIRouter(
    prefix="/belege", tags=["belege"],
    dependencies=[Depends(bereich_wache("verkauf", "einkauf"))],
)

SEITENGROESSE = 50

BELEGART_LABELS: dict[Belegart, str] = {
    Belegart.ANGEBOT: "Angebot",
    Belegart.AUFTRAG: "Auftrag",
    Belegart.LIEFERSCHEIN: "Lieferschein",
    Belegart.RECHNUNG: "Rechnung",
    Belegart.GUTSCHRIFT: "Gutschrift",
    Belegart.BESTELLUNG: "Bestellung",
    Belegart.EK_LIEFERSCHEIN: "EK-Lieferschein/Wareneingang",
    Belegart.EK_RECHNUNG: "EK-Rechnung",
}

BELEGART_LABELS_PLURAL: dict[Belegart, str] = {
    Belegart.ANGEBOT: "Angebote",
    Belegart.AUFTRAG: "Aufträge",
    Belegart.LIEFERSCHEIN: "Lieferscheine",
    Belegart.RECHNUNG: "Rechnungen",
    Belegart.GUTSCHRIFT: "Gutschriften",
    Belegart.BESTELLUNG: "Bestellungen",
    Belegart.EK_LIEFERSCHEIN: "Wareneingänge",
    Belegart.EK_RECHNUNG: "EK-Rechnungen",
}

STATUS_LABELS: dict[BelegStatus, str] = {
    BelegStatus.ENTWURF: "Entwurf",
    BelegStatus.GEDRUCKT: "Gedruckt",
    BelegStatus.ERLEDIGT: "Erledigt",
    BelegStatus.STORNIERT: "Storniert",
}

STATUS_BADGES: dict[BelegStatus, str] = {
    BelegStatus.ENTWURF: "bg-warning text-dark",
    BelegStatus.GEDRUCKT: "bg-primary",
    BelegStatus.ERLEDIGT: "bg-success",
    BelegStatus.STORNIERT: "bg-danger",
}

VERKAUFSARTEN = {
    Belegart.ANGEBOT,
    Belegart.AUFTRAG,
    Belegart.LIEFERSCHEIN,
    Belegart.RECHNUNG,
    Belegart.GUTSCHRIFT,
}

# Belegarten mit Zahlungsstatus (offene Posten)
ZAHLUNGSARTEN = {Belegart.RECHNUNG, Belegart.GUTSCHRIFT, Belegart.EK_RECHNUNG}


ZAHLSTATUS_BADGES: dict[str, str] = {
    "bezahlt": "bg-success",
    "offen": "bg-secondary",
    "ueberfaellig": "bg-danger",
}
ZAHLSTATUS_LABELS: dict[str, str] = {
    "bezahlt": "bezahlt",
    "offen": "offen",
    "ueberfaellig": "überfällig",
}


def _zahlstatus(beleg: Beleg, heute: date) -> tuple[str, date]:
    """Zahlungsstatus ("bezahlt"/"offen"/"ueberfaellig") und Fälligkeitsdatum."""
    faelligkeit = beleg.datum + timedelta(days=beleg.zahlungsziel_tage)
    if beleg.bezahlt_am is not None:
        return "bezahlt", faelligkeit
    if faelligkeit < heute:
        return "ueberfaellig", faelligkeit
    return "offen", faelligkeit


def _zahlung_map(belege: list, art: Belegart) -> dict[int, tuple[str, date]]:
    """beleg_id → (Zahlungsstatus, Fälligkeitsdatum) für zahlungsrelevante Belegarten."""
    if art not in ZAHLUNGSARTEN:
        return {}
    heute = date.today()
    return {beleg.id: _zahlstatus(beleg, heute) for beleg, _ in belege}

# Belegarten, bei denen zugeordnete Seriennummern im Grid sichtbar sind
SN_ANZEIGE_ARTEN = {Belegart.LIEFERSCHEIN, Belegart.EK_LIEFERSCHEIN, Belegart.RECHNUNG}
# Belegarten, bei denen (ab M4) Seriennummern gewählt werden
SN_WAHL_ARTEN = {Belegart.LIEFERSCHEIN, Belegart.EK_LIEFERSCHEIN}


def _belegart_oder_404(belegart: str) -> Belegart:
    try:
        return Belegart(belegart)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unbekannte Belegart: {belegart}"
        ) from None


def _bereich_fuer(belegart: Belegart) -> str:
    """Zuständiger Rechtebereich einer Belegart ("verkauf" oder "einkauf")."""
    return "verkauf" if belegart in VERKAUFSARTEN else "einkauf"


def _pruefe_belegart_recht(request: Request, session: Session, belegart: Belegart) -> None:
    """Erzwingt das Recht am konkreten Bereich der Belegart.

    Die Router-Wache prüft nur "verkauf ODER einkauf". Ohne diese objektbezogene
    Prüfung könnte ein Nutzer mit ausschließlich Verkaufsrecht Einkaufsbelege
    lesen/ändern (und umgekehrt).

    Die Rechte werden bewusst **frisch aus der Datenbank** gelesen (wie in
    ``bereich_wache``) und nicht aus der Session: Eine Durchsetzungsprüfung darf
    einem Rechteentzug nicht bis zum nächsten Login nachhinken. Die Session-
    Variante (``darf_bereich``) bleibt der Template-Anzeige vorbehalten.
    """
    bereich = _bereich_fuer(belegart)
    mindestens = (
        Zugriff.BEARBEITEN
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}
        else Zugriff.ANSEHEN
    )
    benutzer_id = request.session.get("benutzer_id")
    benutzer = session.get(Benutzer, benutzer_id) if benutzer_id else None
    if benutzer is None or not darf(lade_profil(session, benutzer), bereich, mindestens):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Keine Berechtigung für: {bereich}.",
        )


def _belegart_mit_recht(belegart: str, request: Request, session: Session) -> Belegart:
    """Belegart aus dem Pfad prüfen UND das zugehörige Bereichsrecht erzwingen.

    Für die generischen ``/{belegart}``-Routen (Liste, Suche, Neuanlage): dort
    steht die Belegart im Pfad, also muss der Bereich daraus abgeleitet geprüft
    werden — sonst könnte man mit reinem Verkaufsrecht Einkaufsbelege auflisten
    oder anlegen.
    """
    art = _belegart_oder_404(belegart)
    _pruefe_belegart_recht(request, session, art)
    return art


def _lade_beleg(session: Session, beleg_id: int, request: Request | None = None) -> Beleg:
    """Beleg laden; mit ``request`` zusätzlich das Verkauf/Einkauf-Recht prüfen."""
    beleg = session.get(Beleg, beleg_id)
    if beleg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Beleg nicht gefunden")
    if request is not None:
        _pruefe_belegart_recht(request, session, beleg.belegart)
    return beleg


def _lade_position(session: Session, beleg: Beleg, position_id: int) -> BelegPosition:
    position = session.get(BelegPosition, position_id)
    if position is None or position.beleg_id != beleg.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position nicht gefunden"
        )
    return position


def _beleg_filter_aktiv(q: str, status_filter: str) -> bool:
    """Ist die Liste gerade eingeschränkt? (unterscheidet die beiden Leerzustände)"""
    return bool(q.strip()) or status_filter in set(BelegStatus)


def _suche_belege(
    session: Session, art: Belegart, q: str, status_filter: str, seite: int = 1
) -> tuple[list, Blaetterwerk]:
    """Eine Seite Belege einer Belegart samt Adresse (Filter: Status + Text).

    Liefert die Zeilen der gewünschten Seite und das Blätterwerk. Die Lieferschein-
    Liste arbeitet ohne HTMX (klassisches GET-Formular wegen der Sammelrechnungs-
    Checkboxen), dort blättern daher normale Links.
    """
    stmt = (
        select(Beleg, Adresse)
        .join(Adresse, Beleg.adresse_id == Adresse.id)  # type: ignore[arg-type]
        .where(Beleg.belegart == art)
    )
    if status_filter in set(BelegStatus):
        stmt = stmt.where(Beleg.status == BelegStatus(status_filter))
    if q:
        muster = f"%{q}%"
        stmt = stmt.where(
            Beleg.belegnr.ilike(muster)  # type: ignore[attr-defined]
            | Adresse.firma.ilike(muster)  # type: ignore[attr-defined]
        )
    stmt = stmt.order_by(Beleg.id.desc())  # type: ignore[union-attr]
    htmx_url = "" if art == Belegart.LIEFERSCHEIN else f"/belege/{art}/suche"
    return blaettere(
        session,
        stmt,
        seite,
        groesse=SEITENGROESSE,
        basis_url=f"/belege/{art}",
        params={"q": q, "status_filter": status_filter},
        htmx_url=htmx_url,
        htmx_target="#beleg-tabelle",
    )


def _adressen_fuer_belegart(session: Session, art: Belegart) -> list[Adresse]:
    """Kunden für Verkaufsbelege, Lieferanten für Einkaufsbelege; BEIDE erscheint überall."""
    if art in VERKAUFSARTEN:
        typen = [AdressTyp.KUNDE, AdressTyp.BEIDE]
    else:
        typen = [AdressTyp.LIEFERANT, AdressTyp.BEIDE]
    stmt = (
        select(Adresse)
        .where(Adresse.typ.in_(typen))  # type: ignore[attr-defined]
        .where(Adresse.aktiv == True)  # noqa: E712
        .order_by(Adresse.firma)
    )
    return list(session.exec(stmt).all())


def _positionen(session: Session, beleg: Beleg) -> list[BelegPosition]:
    return list(
        session.exec(
            select(BelegPosition)
            .where(BelegPosition.beleg_id == beleg.id)
            .order_by(BelegPosition.pos_nr)  # type: ignore[arg-type]
        ).all()
    )


def _grid_kontext(session: Session, beleg: Beleg, aktiv_id: int | None = None) -> dict:
    """Kontext für das Positionsgrid-Partial (Grid + Summenblock)."""
    positionen = _positionen(session, beleg)
    artikel_ids = [p.artikel_id for p in positionen if p.artikel_id is not None]
    artikel_map: dict[int, Artikel] = {}
    if artikel_ids:
        artikel_map = {
            a.id: a
            for a in session.exec(
                select(Artikel).where(Artikel.id.in_(artikel_ids))  # type: ignore[attr-defined]
            ).all()
        }
    netto_map = {
        p.id: berechnung.positions_netto(p.menge, p.einzelpreis, p.rabatt_prozent)
        for p in positionen
    }
    sn_map: dict[int, list[Seriennummer]] = {}
    position_ids = [p.id for p in positionen]
    if beleg.belegart in SN_ANZEIGE_ARTEN and position_ids:
        sn_map = seriennummer_service.sns_je_position(session, position_ids)
    bearbeitbar = beleg.status == BelegStatus.ENTWURF
    # Bestandswarnungen: nur Lieferschein im Entwurf, Lagerartikel, Bestand < Menge
    bestand_warnungen: dict[int, Decimal] = {}
    if beleg.belegart == Belegart.LIEFERSCHEIN and bearbeitbar:
        for pos in positionen:
            artikel = artikel_map.get(pos.artikel_id)
            if artikel is None or not artikel.ist_lagerartikel:
                continue
            vorhanden = lager_service.bestand(session, artikel.id)
            if vorhanden < pos.menge:
                bestand_warnungen[pos.id] = vorhanden
    marge = berechnung.beleg_marge(positionen) if beleg.belegart in VERKAUFSARTEN else None
    return {
        "beleg": beleg,
        "positionen": positionen,
        "artikel_map": artikel_map,
        "netto_map": netto_map,
        "sn_map": sn_map,
        "bestand_warnungen": bestand_warnungen,
        "summen": berechnung.parse_steuer_details(beleg.steuer_details),
        "marge": marge,
        "bearbeitbar": bearbeitbar,
        "sn_anzeige": beleg.belegart in SN_ANZEIGE_ARTEN,
        "sn_wahl": beleg.belegart in SN_WAHL_ARTEN and bearbeitbar,
        "ust_saetze": UST_SAETZE,
        "aktiv_id": aktiv_id,
    }


def _grid_response(
    request: Request, session: Session, beleg: Beleg, aktiv_id: int | None = None
):
    return templates.TemplateResponse(
        request, "partials/positionsgrid.html", _grid_kontext(session, beleg, aktiv_id)
    )


def _detail_kontext(
    session: Session, beleg: Beleg, position: BelegPosition | None, oob: bool = False
) -> dict:
    """Kontext für das Positions-Detail-Panel (aktive Position)."""
    artikel = (
        session.get(Artikel, position.artikel_id)
        if position is not None and position.artikel_id is not None
        else None
    )
    return {
        "beleg": beleg,
        "position": position,
        "artikel": artikel,
        "bearbeitbar": beleg.status == BelegStatus.ENTWURF,
        "ust_saetze": UST_SAETZE,
        "oob": oob,
    }


def _grid_detail_response(
    request: Request, session: Session, beleg: Beleg, position: BelegPosition | None
):
    """Grid (Primär-Swap #positionsbereich) + Detail-Panel (Out-of-band #position-detail).

    So bleiben nach einer Feldänderung sowohl die Summen im Grid als auch die
    rückgerechneten Kalkulationswerte im Detail-Panel aktuell.
    """
    aktiv_id = position.id if position is not None else None
    grid_ctx = {"request": request, **_grid_kontext(session, beleg, aktiv_id)}
    grid_html = templates.get_template("partials/positionsgrid.html").render(grid_ctx)
    detail_ctx = {
        "request": request,
        **_detail_kontext(session, beleg, position, oob=True),
    }
    detail_html = templates.get_template("partials/position_detail.html").render(detail_ctx)
    return HTMLResponse(grid_html + detail_html)


def _aktive_textbausteine(
    session: Session, kategorie: TextbausteinKategorie
) -> list[Textbaustein]:
    """Aktive Textbausteine einer Kategorie (alphabetisch nach Titel)."""
    return list(
        session.exec(
            select(Textbaustein)
            .where(Textbaustein.kategorie == kategorie)
            .where(Textbaustein.aktiv == True)  # noqa: E712
            .order_by(Textbaustein.titel)
        ).all()
    )


def _text_anfuegen(vorhanden: str, neu: str) -> str:
    """Hängt ``neu`` an ``vorhanden`` an (Zeilenumbruch dazwischen, wenn nötig)."""
    vorhanden = (vorhanden or "").rstrip()
    if vorhanden:
        return f"{vorhanden}\n{neu}"
    return neu


def _meldung(request: Request, text: str, status_code: int):
    return templates.TemplateResponse(
        request, "partials/meldung.html", {"meldung": text}, status_code=status_code
    )


def _editor_redirect(beleg_id: int, fehler: str | None = None) -> RedirectResponse:
    url = f"/belege/detail/{beleg_id}"
    if fehler:
        url += f"?fehler={quote(fehler)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


def _parse_datum(text: str | None) -> date | None:
    if not text or not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Ungültiges Datum: {text!r}",
        ) from exc


# ---------------------------------------------------------------- Liste + Anlage


@router.get("/detail/{beleg_id}")
def beleg_editor(
    request: Request,
    beleg_id: int,
    fehler: str = "",
    gebucht: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    beleg = _lade_beleg(session, beleg_id, request)
    adresse = session.get(Adresse, beleg.adresse_id)
    kontext = _grid_kontext(session, beleg)
    # M5-Vorbereitung: nach dem Buchen eines Lieferscheins mit SN-Positionen
    # auf die (künftige) Anlagen-Anlage hinweisen.
    anlage_hinweis = (
        bool(gebucht)
        and beleg.belegart == Belegart.LIEFERSCHEIN
        and any(kontext["sn_map"].values())
    )
    zahlungsrelevant = beleg.belegart in ZAHLUNGSARTEN
    zahlstatus, faelligkeit = (
        _zahlstatus(beleg, date.today()) if zahlungsrelevant else (None, None)
    )
    kontext.update(
        {
            "adresse": adresse,
            "label": BELEGART_LABELS[beleg.belegart],
            "status_labels": STATUS_LABELS,
            "status_badges": STATUS_BADGES,
            "fehler": fehler,
            "anlage_hinweis": anlage_hinweis,
            "ist_admin": benutzer.rolle == Rolle.ADMIN,
            "folgebeleg_moeglich": beleg.belegart in beleg_service.FOLGEBELEGART
            and beleg.status in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT),
            "zahlungsrelevant": zahlungsrelevant,
            "zahlstatus": zahlstatus,
            "faelligkeit": faelligkeit,
            "zahlbar": zahlungsrelevant
            and beleg.status in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT),
            "zahlstatus_badges": ZAHLSTATUS_BADGES,
            "zahlstatus_labels": ZAHLSTATUS_LABELS,
        }
    )
    return templates.TemplateResponse(request, "belege/editor.html", kontext)


@router.get("/detail/{beleg_id}/artikelsuche")
def beleg_artikelsuche(
    request: Request,
    beleg_id: int,
    q: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: Trefferliste der Artikel-Schnellsuche im Editor."""
    beleg = _lade_beleg(session, beleg_id, request)
    treffer: list[Artikel] = []
    if q.strip():
        muster = f"%{q.strip()}%"
        stmt = (
            select(Artikel)
            .where(Artikel.aktiv == True)  # noqa: E712
            .where(
                Artikel.artikelnr.ilike(muster)  # type: ignore[attr-defined]
                | Artikel.bezeichnung.ilike(muster)  # type: ignore[attr-defined]
            )
            .order_by(Artikel.artikelnr)
            .limit(10)
        )
        treffer = list(session.exec(stmt).all())
    return templates.TemplateResponse(
        request, "partials/artikel_treffer.html", {"beleg": beleg, "treffer": treffer}
    )


@router.post("/detail/{beleg_id}/positionen")
def position_hinzufuegen(
    request: Request,
    beleg_id: int,
    artikel_id: int | None = Form(None),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Fügt eine Artikelposition (Menge 1) oder eine freie Position hinzu; liefert das Grid."""
    beleg = _lade_beleg(session, beleg_id, request)
    artikel = None
    if artikel_id is not None:
        artikel = session.get(Artikel, artikel_id)
        if artikel is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Artikel nicht gefunden"
            )
    try:
        if artikel is not None and artikel_set_service.ist_set(artikel):
            # Set-Artikel: seine Komponenten als Positionen einfügen; Panel auf die erste
            neue = artikel_set_service.fuege_set_positionen_ein(session, beleg, artikel)
            position = neue[0] if neue else None
        elif artikel is not None:
            position = beleg_service.fuege_position_hinzu(session, beleg, artikel)
        else:
            position = beleg_service.fuege_position_hinzu(
                session, beleg, None, bezeichnung="Freie Position"
            )
    except BelegGesperrt as exc:
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    session.commit()
    return _grid_detail_response(request, session, beleg, position)


@router.post("/detail/{beleg_id}/positionen/{position_id}")
def position_bearbeiten(  # noqa: PLR0913
    request: Request,
    beleg_id: int,
    position_id: int,
    bezeichnung: str = Form(""),
    langtext: str = Form(""),
    menge: str = Form("1"),
    einheit: str = Form("Stk"),
    listenpreis: str = Form("0"),
    rabatt1_prozent: str = Form("0"),
    rabatt2_prozent: str = Form("0"),
    aufschlag_prozent: str = Form("0"),
    einzelpreis: str = Form("0"),
    rabatt_prozent: str = Form("0"),
    ust_satz: int = Form(20),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Übernimmt geänderte Zeilenwerte (nur im Entwurf) und liefert das Grid samt Summen.

    Kalkulation: EK = Listenpreis − Rab1 − Rab2 (rein abgeleitet). Der VK
    (einzelpreis) wird aus EK + Aufschlag berechnet — außer der Benutzer hat
    das VK-Feld selbst geändert (erkannt am HX-Trigger ``vk-<id>``), dann wird
    der eingegebene VK übernommen und der Aufschlag daraus rückgerechnet.
    """
    beleg = _lade_beleg(session, beleg_id, request)
    position = _lade_position(session, beleg, position_id)
    if ust_satz not in UST_SAETZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unzulässiger USt-Satz: {ust_satz}",
        )
    try:
        beleg_service.pruefe_bearbeitbar(beleg)
    except BelegGesperrt as exc:
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    position.bezeichnung = bezeichnung.strip()
    position.langtext = langtext.strip()
    position.menge = parse_menge(menge)
    position.einheit = einheit.strip() or "Stk"
    position.listenpreis = parse_betrag(listenpreis, "Listenpreis")
    position.rabatt1_prozent = parse_prozent(rabatt1_prozent, "Rabatt 1")
    position.rabatt2_prozent = parse_prozent(rabatt2_prozent, "Rabatt 2")
    position.ek_preis = berechnung.kalkuliere_ek(
        position.listenpreis, position.rabatt1_prozent, position.rabatt2_prozent
    )
    # Aufschlag darf negativ sein (Verkauf unter EK), aber nicht unter −100 %
    # (das wäre ein negativer Verkaufspreis aus einem positiven EK).
    position.aufschlag_prozent = parse_prozent(
        aufschlag_prozent, "Aufschlag", min_wert=Decimal("-100"),
        max_wert=MAX_AUFSCHLAG_PROZENT,
    )
    ausloeser = request.headers.get("HX-Trigger", "")
    kalk_felder = {f"{feld}-{position_id}" for feld in ("lp", "rab1", "rab2", "aufschlag")}
    if ausloeser == f"vk-{position_id}":
        # VK manuell überschrieben → übernehmen, Aufschlag rückrechnen
        position.einzelpreis = parse_betrag(einzelpreis, "Verkaufspreis")
        position.aufschlag_prozent = berechnung.aufschlag_aus_vk(
            position.ek_preis, position.einzelpreis
        )
    elif ausloeser in kalk_felder:
        # Kalkulationsfeld geändert → VK aus EK + Aufschlag neu berechnen
        position.einzelpreis = berechnung.kalkuliere_vk(
            position.ek_preis, position.aufschlag_prozent
        )
    else:
        # Menge/Bezeichnung/… geändert → VK unverändert übernehmen (auch Freitext)
        position.einzelpreis = parse_betrag(einzelpreis, "Verkaufspreis")
    position.rabatt_prozent = parse_prozent(rabatt_prozent, "Positionsrabatt")
    position.ust_satz = ust_satz
    session.add(position)
    session.flush()
    beleg_service.aktualisiere_summen(session, beleg)
    session.commit()
    return _grid_detail_response(request, session, beleg, position)


@router.delete("/detail/{beleg_id}/positionen/{position_id}")
def position_loeschen(
    request: Request,
    beleg_id: int,
    position_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    beleg = _lade_beleg(session, beleg_id, request)
    position = _lade_position(session, beleg, position_id)
    try:
        beleg_service.entferne_position(session, beleg, position)
    except BelegGesperrt as exc:
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    session.commit()
    return _grid_response(request, session, beleg)


@router.post("/detail/{beleg_id}/positionen/{position_id}/verschieben")
def position_verschieben(
    request: Request,
    beleg_id: int,
    position_id: int,
    richtung: str = "hoch",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Verschiebt eine Position hoch/runter (tauscht pos_nr) und liefert das Grid."""
    beleg = _lade_beleg(session, beleg_id, request)
    position = _lade_position(session, beleg, position_id)
    try:
        beleg_service.verschiebe_position(session, beleg, position, richtung)
    except BelegGesperrt as exc:
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    session.commit()
    return _grid_response(request, session, beleg, aktiv_id=position.id)


# --------------------------------------------------- Positions-Detail-Panel (Kplus-Style)


@router.get("/detail/{beleg_id}/positionen/{position_id}/detail")
def position_detail(
    request: Request,
    beleg_id: int,
    position_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: Detail-/Kalkulationspanel der aktiven Position."""
    beleg = _lade_beleg(session, beleg_id, request)
    position = _lade_position(session, beleg, position_id)
    return templates.TemplateResponse(
        request, "partials/position_detail.html", _detail_kontext(session, beleg, position)
    )


# --------------------------------------------------------------------- Textbausteine


@router.get("/detail/{beleg_id}/positionen/{position_id}/textbausteine")
def position_textbausteine(
    request: Request,
    beleg_id: int,
    position_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: aktive Positions-Textbausteine zur Auswahl."""
    beleg = _lade_beleg(session, beleg_id, request)
    position = _lade_position(session, beleg, position_id)
    return templates.TemplateResponse(
        request,
        "partials/textbaustein_dialog.html",
        {
            "bausteine": _aktive_textbausteine(session, TextbausteinKategorie.POSITION),
            "aktion_url": (
                f"/belege/detail/{beleg.id}/positionen/{position.id}/textbaustein-einfuegen"
            ),
            "extra": {},
            "verwende_htmx": True,
            "ziel_target": "#positionsbereich",
            "dialog_id": f"textbaustein-dialog-{position.id}",
        },
    )


@router.post("/detail/{beleg_id}/positionen/{position_id}/textbaustein-einfuegen")
def position_textbaustein_einfuegen(
    request: Request,
    beleg_id: int,
    position_id: int,
    baustein_id: int = Form(...),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Hängt den Textbaustein-Text an den Langtext der Position an; liefert Grid + Panel."""
    beleg = _lade_beleg(session, beleg_id, request)
    position = _lade_position(session, beleg, position_id)
    try:
        beleg_service.pruefe_bearbeitbar(beleg)
    except BelegGesperrt as exc:
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    baustein = session.get(Textbaustein, baustein_id)
    if baustein is not None:
        position.langtext = _text_anfuegen(position.langtext, baustein.text)
        session.add(position)
        session.commit()
    return _grid_detail_response(request, session, beleg, position)


@router.get("/detail/{beleg_id}/textbausteine")
def kopf_textbausteine(
    request: Request,
    beleg_id: int,
    ziel: str = "kopf",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: Kopf-/Fuß-Textbausteine zur Auswahl."""
    beleg = _lade_beleg(session, beleg_id, request)
    kategorie = (
        TextbausteinKategorie.FUSS if ziel == "fuss" else TextbausteinKategorie.KOPF
    )
    return templates.TemplateResponse(
        request,
        "partials/textbaustein_dialog.html",
        {
            "bausteine": _aktive_textbausteine(session, kategorie),
            "aktion_url": f"/belege/detail/{beleg.id}/textbaustein-kopf-einfuegen",
            "extra": {"ziel": "fuss" if ziel == "fuss" else "kopf"},
            "verwende_htmx": False,
            "dialog_id": f"tb-{'fuss' if ziel == 'fuss' else 'kopf'}",
        },
    )


@router.post("/detail/{beleg_id}/textbaustein-kopf-einfuegen")
def kopf_textbaustein_einfuegen(
    request: Request,
    beleg_id: int,
    baustein_id: int = Form(...),
    ziel: str = Form("kopf"),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Hängt den Textbaustein-Text an Kopf- bzw. Fußtext an; leitet zum Editor zurück."""
    beleg = _lade_beleg(session, beleg_id, request)
    try:
        beleg_service.pruefe_bearbeitbar(beleg)
    except BelegGesperrt as exc:
        return _editor_redirect(beleg_id, fehler=str(exc))
    baustein = session.get(Textbaustein, baustein_id)
    if baustein is not None:
        if ziel == "fuss":
            beleg.fusstext = _text_anfuegen(beleg.fusstext, baustein.text)
        else:
            beleg.kopftext = _text_anfuegen(beleg.kopftext, baustein.text)
        session.add(beleg)
        session.commit()
    return _editor_redirect(beleg_id)


# --------------------------------------------------------------- Beleg duplizieren


@router.post("/detail/{beleg_id}/duplizieren")
def beleg_duplizieren(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Dupliziert den Beleg als frischen Entwurf (Vorlage) und öffnet dessen Editor."""
    beleg = _lade_beleg(session, beleg_id, request)
    neu = beleg_service.dupliziere_beleg(session, beleg, benutzer_id=benutzer.id)
    session.commit()
    return _editor_redirect(neu.id)


# ------------------------------------------------------------- Seriennummern (M4)


def _lade_sn_position(
    session: Session, beleg: Beleg, position_id: int, belegart: Belegart
) -> tuple[BelegPosition, Artikel]:
    """Position samt SN-pflichtigem Artikel; 404 bei falscher Belegart/Position."""
    if beleg.belegart != belegart:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Seriennummern-Aktion für Belegart {beleg.belegart} nicht verfügbar",
        )
    position = _lade_position(session, beleg, position_id)
    artikel = session.get(Artikel, position.artikel_id) if position.artikel_id else None
    if artikel is None or not artikel.seriennummernpflichtig:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Position hat keinen seriennummernpflichtigen Artikel",
        )
    return position, artikel


@router.get("/detail/{beleg_id}/positionen/{position_id}/sn-dialog")
def sn_dialog(
    request: Request,
    beleg_id: int,
    position_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: SN-Auswahl (Checkboxen) für eine Lieferschein-Position im Entwurf."""
    beleg = _lade_beleg(session, beleg_id, request)
    position, artikel = _lade_sn_position(session, beleg, position_id, Belegart.LIEFERSCHEIN)
    if beleg.status != BelegStatus.ENTWURF:
        return _meldung(
            request, "Beleg ist nicht mehr im Entwurf", status.HTTP_409_CONFLICT
        )
    zugeordnet = seriennummer_service.sns_je_position(session, [position.id]).get(
        position.id, []
    )
    zugeordnet_ids = {sn.id for sn in zugeordnet}
    # Verfügbare + bereits dieser Position zugeordnete SNs (die zählen nicht mehr
    # als "verfügbar", müssen aber wählbar bleiben).
    sns = zugeordnet + [
        sn
        for sn in seriennummer_service.verfuegbare_sns(session, artikel.id)
        if sn.id not in zugeordnet_ids
    ]
    sns.sort(key=lambda sn: sn.sn)
    return templates.TemplateResponse(
        request,
        "partials/sn_dialog.html",
        {"beleg": beleg, "position": position, "sns": sns, "zugeordnet_ids": zugeordnet_ids},
    )


@router.post("/detail/{beleg_id}/positionen/{position_id}/sn-zuweisen")
def sn_zuweisen(
    request: Request,
    beleg_id: int,
    position_id: int,
    seriennummer_ids: list[int] = Form([]),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Übernimmt die SN-Auswahl (ersetzt die bisherige Zuordnung) und liefert das Grid."""
    beleg = _lade_beleg(session, beleg_id, request)
    position, _ = _lade_sn_position(session, beleg, position_id, Belegart.LIEFERSCHEIN)
    try:
        seriennummer_service.weise_sns_zu(session, position, seriennummer_ids)
    except (SnNichtVerfuegbar, BelegGesperrt) as exc:
        session.rollback()
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    session.commit()
    return _grid_response(request, session, beleg)


@router.get("/detail/{beleg_id}/positionen/{position_id}/sn-erfassung")
def sn_erfassung(
    request: Request,
    beleg_id: int,
    position_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: SN-Erfassung (Textfelder) für eine EK-Lieferschein-Position im Entwurf."""
    beleg = _lade_beleg(session, beleg_id, request)
    position, _ = _lade_sn_position(session, beleg, position_id, Belegart.EK_LIEFERSCHEIN)
    if beleg.status != BelegStatus.ENTWURF:
        return _meldung(
            request, "Beleg ist nicht mehr im Entwurf", status.HTTP_409_CONFLICT
        )
    erfasst = seriennummer_service.sns_je_position(session, [position.id]).get(position.id, [])
    werte = [sn.sn for sn in erfasst]
    anzahl = max(int(position.menge), len(werte), 1)
    werte += [""] * (anzahl - len(werte))
    return templates.TemplateResponse(
        request,
        "partials/sn_erfassung.html",
        {"beleg": beleg, "position": position, "werte": werte},
    )


@router.post("/detail/{beleg_id}/positionen/{position_id}/sn-erfassen")
def sn_erfassen(
    request: Request,
    beleg_id: int,
    position_id: int,
    sns: list[str] = Form([]),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Übernimmt erfasste Wareneingangs-SNs (leere Felder ignoriert) und liefert das Grid."""
    beleg = _lade_beleg(session, beleg_id, request)
    position, _ = _lade_sn_position(session, beleg, position_id, Belegart.EK_LIEFERSCHEIN)
    bereinigt = [sn.strip() for sn in sns if sn.strip()]
    try:
        seriennummer_service.erfasse_wareneingang_sns(session, position, bereinigt)
    except (SnNichtVerfuegbar, BelegGesperrt) as exc:
        session.rollback()
        return _meldung(request, str(exc), status.HTTP_409_CONFLICT)
    session.commit()
    return _grid_response(request, session, beleg)


@router.post("/detail/{beleg_id}/kopf")
def kopf_speichern(  # noqa: PLR0913
    request: Request,
    beleg_id: int,
    datum: str = Form(""),
    leistungsdatum_von: str = Form(""),
    leistungsdatum_bis: str = Form(""),
    kopftext: str = Form(""),
    fusstext: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    beleg = _lade_beleg(session, beleg_id, request)
    try:
        beleg_service.pruefe_bearbeitbar(beleg)
    except BelegGesperrt as exc:
        return _editor_redirect(beleg_id, fehler=str(exc))
    neues_datum = _parse_datum(datum)
    if neues_datum is not None:
        beleg.datum = neues_datum
    beleg.leistungsdatum_von = _parse_datum(leistungsdatum_von)
    beleg.leistungsdatum_bis = _parse_datum(leistungsdatum_bis)
    beleg.kopftext = kopftext.strip()
    beleg.fusstext = fusstext.strip()
    session.add(beleg)
    session.commit()
    return _editor_redirect(beleg_id)


def _statuswechsel(
    session: Session,
    beleg: Beleg,
    ziel: BelegStatus,
    benutzer: Benutzer,
    erfolg_query: str = "",
):
    beleg_id = beleg.id
    try:
        beleg_service.setze_status(session, beleg, ziel, benutzer_id=benutzer.id)
    except (
        UngueltigerStatusUebergang,
        BelegGesperrt,
        SnPflichtVerletzt,
        StornoNichtMoeglich,
        # Lagerbuchung konnte wegen Nebenläufigkeit nicht durchgeführt werden —
        # gehört fachlich gemeldet (erneut versuchen), nicht als 500.
        LagerbuchungFehlgeschlagen,
    ) as exc:
        # Hooks können teilgebucht haben — alles zurückrollen, bevor umgeleitet wird
        session.rollback()
        return _editor_redirect(beleg_id, fehler=str(exc))
    session.commit()
    return RedirectResponse(
        f"/belege/detail/{beleg_id}{erfolg_query}", status_code=status.HTTP_302_FOUND
    )


@router.post("/detail/{beleg_id}/status/gedruckt")
def status_gedruckt(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    beleg = _lade_beleg(session, beleg_id, request)
    erfolg_query = "?gebucht=1" if beleg.belegart == Belegart.LIEFERSCHEIN else ""
    return _statuswechsel(session, beleg, BelegStatus.GEDRUCKT, benutzer, erfolg_query)


@router.post("/detail/{beleg_id}/status/erledigt")
def status_erledigt(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    beleg = _lade_beleg(session, beleg_id, request)
    return _statuswechsel(session, beleg, BelegStatus.ERLEDIGT, benutzer)


@router.post("/detail/{beleg_id}/status/storniert")
def status_storniert(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(require_admin),
    session: Session = Depends(get_session),
):
    beleg = _lade_beleg(session, beleg_id, request)
    return _statuswechsel(session, beleg, BelegStatus.STORNIERT, benutzer)


@router.get("/detail/{beleg_id}/folgebeleg-dialog")
def folgebeleg_dialog(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: Teilmengen-Auswahl für den Folgebeleg (nur offene Restmengen)."""
    beleg = _lade_beleg(session, beleg_id, request)
    ziel_art = beleg_service.FOLGEBELEGART.get(beleg.belegart)
    if ziel_art is None or beleg.status not in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT):
        return _meldung(
            request, "Für diesen Beleg ist kein Folgebeleg möglich", status.HTTP_409_CONFLICT
        )
    restmengen = beleg_service.offene_restmengen(session, beleg)
    positionen = [p for p in _positionen(session, beleg) if restmengen.get(p.id, Decimal("0")) > 0]
    uebernommen = {
        p.id: beleg_service.bereits_uebernommen(session, p) for p in positionen
    }
    return templates.TemplateResponse(
        request,
        "partials/folgebeleg_dialog.html",
        {
            "beleg": beleg,
            "positionen": positionen,
            "restmengen": restmengen,
            "uebernommen": uebernommen,
            "ziel_label": BELEGART_LABELS[ziel_art],
        },
    )


@router.post("/detail/{beleg_id}/folgebeleg")
async def folgebeleg_erzeugen(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Erzeugt den Folgebeleg. Mit ``menge_<id>``-Feldern → Teilübernahme;
    ohne diese Felder (Abwärtskompatibilität) volle Übernahme aller Positionen."""
    beleg = _lade_beleg(session, beleg_id, request)
    formular = await request.form()
    mengen: dict[int, Decimal] = {}
    for schluessel, wert in formular.items():
        if not schluessel.startswith("menge_"):
            continue
        try:
            position_id = int(schluessel[len("menge_") :])
        except ValueError:
            continue
        mengen[position_id] = parse_decimal(str(wert))
    try:
        neu = beleg_service.erzeuge_folgebeleg(
            session, beleg, benutzer_id=benutzer.id, mengen=mengen or None
        )
    except (UngueltigeBelegart, UngueltigerStatusUebergang, WerkplusFehler) as exc:
        session.rollback()
        return _editor_redirect(beleg_id, fehler=str(exc))
    session.commit()
    return _editor_redirect(neu.id)


# -------------------------------------------------- Abschlags-/Schlussrechnung (Kplus)


@router.get("/detail/{beleg_id}/abschlag-dialog")
def abschlag_dialog(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: Eingabe für eine Abschlagsrechnung (nur aus gedrucktem Auftrag)."""
    beleg = _lade_beleg(session, beleg_id, request)
    if beleg.belegart != Belegart.AUFTRAG or beleg.status not in (
        BelegStatus.GEDRUCKT,
        BelegStatus.ERLEDIGT,
    ):
        return _meldung(
            request,
            "Abschlagsrechnung nur aus einem gedruckten Auftrag möglich",
            status.HTTP_409_CONFLICT,
        )
    abgerechnet = beleg_service.bereits_abgerechnete_abschlaege(session, beleg)
    return templates.TemplateResponse(
        request,
        "partials/abschlag_dialog.html",
        {
            "beleg": beleg,
            "netto": beleg.summe_netto,
            "abgerechnet": abgerechnet,
            "offen": beleg.summe_netto - abgerechnet,
        },
    )


@router.post("/detail/{beleg_id}/abschlagsrechnung")
def abschlagsrechnung_erzeugen(
    request: Request,
    beleg_id: int,
    prozent: str = Form(""),
    betrag: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Erzeugt eine Abschlagsrechnung aus einem Auftrag und öffnet deren Editor."""
    beleg = _lade_beleg(session, beleg_id, request)
    prozent_wert = parse_decimal(prozent) if prozent.strip() else None
    betrag_wert = parse_decimal(betrag) if betrag.strip() else None
    if prozent_wert is None and betrag_wert is None:
        return _editor_redirect(beleg_id, fehler="Bitte Prozentsatz oder Betrag angeben")
    try:
        neu = beleg_service.erzeuge_abschlagsrechnung(
            session,
            beleg,
            prozent=prozent_wert,
            betrag=betrag_wert,
            benutzer_id=benutzer.id,
        )
    except (UngueltigeBelegart, UngueltigerStatusUebergang, WerkplusFehler) as exc:
        session.rollback()
        return _editor_redirect(beleg_id, fehler=str(exc))
    session.commit()
    return _editor_redirect(neu.id)


@router.post("/detail/{beleg_id}/schlussrechnung")
def schlussrechnung_erzeugen(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Erzeugt die Schlussrechnung eines Auftrags (mit Abschlagsabzug) und öffnet sie."""
    beleg = _lade_beleg(session, beleg_id, request)
    try:
        neu = beleg_service.erzeuge_schlussrechnung(session, beleg, benutzer_id=benutzer.id)
    except (UngueltigeBelegart, UngueltigerStatusUebergang, WerkplusFehler) as exc:
        session.rollback()
        return _editor_redirect(beleg_id, fehler=str(exc))
    session.commit()
    return _editor_redirect(neu.id)


@router.post("/sammelrechnung")
def sammelrechnung_erzeugen(
    lieferschein_ids: list[int] = Form([]),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Erzeugt aus mehreren gewählten Lieferscheinen eine Sammelrechnung."""
    if not lieferschein_ids:
        return _lieferschein_liste_redirect("Bitte mindestens einen Lieferschein wählen")
    lieferscheine = [
        ls for ls in (session.get(Beleg, lid) for lid in lieferschein_ids) if ls is not None
    ]
    try:
        neu = beleg_service.erzeuge_sammelrechnung(
            session, lieferscheine, benutzer_id=benutzer.id
        )
    except (UngueltigeBelegart, UngueltigerStatusUebergang, WerkplusFehler) as exc:
        session.rollback()
        return _lieferschein_liste_redirect(str(exc))
    session.commit()
    return _editor_redirect(neu.id)


def _lieferschein_liste_redirect(fehler: str) -> RedirectResponse:
    return RedirectResponse(
        f"/belege/lieferschein?fehler={quote(fehler)}", status_code=status.HTTP_302_FOUND
    )


@router.post("/detail/{beleg_id}/bezahlt")
def beleg_bezahlt(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Markiert einen zahlungsrelevanten Beleg als bezahlt (bezahlt_am = heute)."""
    beleg = _lade_beleg(session, beleg_id, request)
    if beleg.belegart not in ZAHLUNGSARTEN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Belegart {beleg.belegart} kennt keinen Zahlungsstatus",
        )
    beleg.bezahlt_am = date.today()
    session.add(beleg)
    session.commit()
    return _editor_redirect(beleg_id)


@router.post("/detail/{beleg_id}/offen")
def beleg_offen(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Setzt einen zahlungsrelevanten Beleg wieder auf offen (bezahlt_am = None)."""
    beleg = _lade_beleg(session, beleg_id, request)
    if beleg.belegart not in ZAHLUNGSARTEN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Belegart {beleg.belegart} kennt keinen Zahlungsstatus",
        )
    beleg.bezahlt_am = None
    session.add(beleg)
    session.commit()
    return _editor_redirect(beleg_id)


@router.get("/detail/{beleg_id}/druckvorschau")
def beleg_druckvorschau(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTML-Vorschau der Druckvorlage — funktioniert auch ohne WeasyPrint/GTK."""
    beleg = _lade_beleg(session, beleg_id, request)
    from app.services import pdf_service

    return Response(content=pdf_service.render_beleg_html(session, beleg), media_type="text/html")


@router.get("/detail/{beleg_id}/pdf")
def beleg_pdf(
    request: Request,
    beleg_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Rendert den Beleg als PDF (WeasyPrint); 503, wenn die PDF-Erzeugung fehlt."""
    beleg = _lade_beleg(session, beleg_id, request)
    try:
        from app.services import pdf_service
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF-Erzeugung ist auf diesem System nicht verfügbar",
        ) from None
    try:
        pdf = pdf_service.render_beleg_pdf(session, beleg)
    except pdf_service.PdfNichtVerfuegbar as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{beleg.belegnr}.pdf"'},
    )


# ------------------------------------------------------- Belegart-Routen (generisch)


@router.get("/{belegart}")
def beleg_liste(  # noqa: PLR0913
    request: Request,
    belegart: str,
    q: str = "",
    status_filter: str = "alle",
    seite: int = 1,
    fehler: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    art = _belegart_mit_recht(belegart, request, session)
    belege, pag = _suche_belege(session, art, q, status_filter, seite)
    return templates.TemplateResponse(
        request,
        "belege/liste.html",
        {
            "belegart": art,
            "label": BELEGART_LABELS[art],
            "label_plural": BELEGART_LABELS_PLURAL[art],
            "belege": belege,
            "pag": pag,
            "gefiltert": _beleg_filter_aktiv(q, status_filter),
            "q": q,
            "status_filter": status_filter,
            "fehler": fehler,
            "status_labels": STATUS_LABELS,
            "status_badges": STATUS_BADGES,
            "zahlungsrelevant": art in ZAHLUNGSARTEN,
            "zahlung_map": _zahlung_map(belege, art),
            "zahlstatus_badges": ZAHLSTATUS_BADGES,
            "zahlstatus_labels": ZAHLSTATUS_LABELS,
        },
    )


@router.get("/{belegart}/suche")
def beleg_liste_suche(
    request: Request,
    belegart: str,
    q: str = "",
    status_filter: str = "alle",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen der Belegliste (inkl. Blätter-Zeile)."""
    art = _belegart_mit_recht(belegart, request, session)
    belege, pag = _suche_belege(session, art, q, status_filter, seite)
    return templates.TemplateResponse(
        request,
        "partials/beleg_zeilen.html",
        {
            "belege": belege,
            "pag": pag,
            "gefiltert": _beleg_filter_aktiv(q, status_filter),
            "status_labels": STATUS_LABELS,
            "status_badges": STATUS_BADGES,
            "zahlungsrelevant": art in ZAHLUNGSARTEN,
            "zahlung_map": _zahlung_map(belege, art),
            "zahlstatus_badges": ZAHLSTATUS_BADGES,
            "zahlstatus_labels": ZAHLSTATUS_LABELS,
        },
    )


@router.get("/{belegart}/neu")
def beleg_neu_form(
    request: Request,
    belegart: str,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    art = _belegart_mit_recht(belegart, request, session)
    return templates.TemplateResponse(
        request,
        "belege/neu.html",
        {
            "belegart": art,
            "label": BELEGART_LABELS[art],
            "adressen": _adressen_fuer_belegart(session, art),
            "heute": date.today(),
        },
    )


@router.post("/{belegart}/neu")
def beleg_neu(
    request: Request,
    belegart: str,
    adresse_id: int = Form(...),
    datum: str = Form(""),
    kopftext: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    art = _belegart_mit_recht(belegart, request, session)
    adresse = session.get(Adresse, adresse_id)
    if adresse is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adresse nicht gefunden")
    kopf: dict = {"kopftext": kopftext.strip()}
    beleg_datum = _parse_datum(datum)
    if beleg_datum is not None:
        kopf["datum"] = beleg_datum
    beleg = beleg_service.erstelle_beleg(session, art, adresse, benutzer_id=benutzer.id, **kopf)
    session.commit()
    return _editor_redirect(beleg.id)
