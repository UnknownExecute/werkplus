"""Arbeitszeit-/Regieerfassung: Übersicht, Erfassen, Ändern, Löschen.

Die Routen sind bewusst dünn — Fachlogik und Eigentümerprüfung liegen in
``app.services.zeiterfassung_service``.

Berechtigung, zweistufig:

1. ``bereich_wache("zeiterfassung")`` am Router prüft die Bereichsstufe
   (GET ⇒ ansehen, POST ⇒ bearbeiten) und damit ``recht_zeiterfassung``.
2. Die **Eigentümerprüfung je Zeile** macht :func:`_lade_zeile` über
   ``zeiterfassung_service.lade_zeile_fuer``: eigene Zeilen immer, fremde nur als
   Admin. Eine fremde Zeile ergibt 404 (nicht 403) — so verrät die Antwort nicht,
   ob die ID überhaupt vergeben ist.

Routen-Reihenfolge: die statischen Pfade (``/neu``, ``/zeilen``) stehen VOR
``/{zeit_id}``, sonst würde ``/zeiterfassung/neu`` am int-Pfadparameter
scheitern (422).
"""

from datetime import date
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.dependencies import get_current_user, ist_admin
from app.formparse import parse_betrag, parse_menge
from app.models import (
    Adresse,
    Beleg,
    Belegart,
    BelegStatus,
    Benutzer,
    Zeiterfassung,
)
from app.pagination import Blaetterwerk, blaettere
from app.services import zeiterfassung_service
from app.services.berechtigung import bereich_wache  # noqa: E402
from app.services.fehler import WerkplusFehler
from app.templating import templates

router = APIRouter(
    prefix="/zeiterfassung",
    tags=["zeiterfassung"],
    dependencies=[Depends(bereich_wache("zeiterfassung"))],
)

SEITENGROESSE = 50

# Belegarten/Status, auf die Zeiten gebucht werden können: der Auftrag ist der
# Sammelbeleg der Regie; gedruckte Aufträge bleiben wählbar (die Verrechnung
# läuft später über Schluss-/Folgerechnung).
BUCHBARE_STATUS = (BelegStatus.ENTWURF, BelegStatus.GEDRUCKT)
MAX_AUFTRAGS_OPTIONEN = 200


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


def _lade_zeile(session: Session, zeit_id: int, benutzer: Benutzer) -> Zeiterfassung:
    """Zeile laden UND die Eigentümerprüfung erzwingen (fremd ⇒ 404).

    Absichtlich 404 statt 403: eine fremde Zeile soll für einen Nicht-Admin
    nicht von einer nicht existierenden zu unterscheiden sein.
    """
    zeile = zeiterfassung_service.lade_zeile_fuer(session, zeit_id, benutzer)
    if zeile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Arbeitszeit nicht gefunden"
        )
    return zeile


def _mitarbeiter(session: Session) -> list[Benutzer]:
    return list(session.exec(select(Benutzer).order_by(col(Benutzer.name))).all())


def _auftrags_optionen(
    session: Session, zusatz_beleg_id: int | None = None
) -> list[tuple[Beleg, Adresse | None]]:
    """Wählbare Aufträge (Entwurf/gedruckt), neueste zuerst.

    ``zusatz_beleg_id`` hängt den bereits zugeordneten Beleg einer Zeile an, auch
    wenn er nicht mehr in der Auswahl steht — sonst würde die Zuordnung beim
    Speichern still verloren gehen.
    """
    stmt = (
        select(Beleg, Adresse)
        .join(Adresse, col(Adresse.id) == col(Beleg.adresse_id), isouter=True)
        .where(Beleg.belegart == Belegart.AUFTRAG)
        .where(col(Beleg.status).in_(BUCHBARE_STATUS))
        .order_by(col(Beleg.id).desc())
        .limit(MAX_AUFTRAGS_OPTIONEN)
    )
    optionen = list(session.exec(stmt).all())
    if zusatz_beleg_id is not None and all(b.id != zusatz_beleg_id for b, _ in optionen):
        beleg = session.get(Beleg, zusatz_beleg_id)
        if beleg is not None:
            optionen.insert(0, (beleg, session.get(Adresse, beleg.adresse_id)))
    return optionen


def _beleg_bezeichnungen(session: Session, zeilen: list) -> dict[int, str]:
    """beleg_id → „Belegnr (Kunde)" für die Anzeige in der Liste."""
    ids = {z.beleg_id for z, _ in zeilen if z.beleg_id is not None}
    if not ids:
        return {}
    treffer = session.exec(
        select(Beleg, Adresse)
        .join(Adresse, col(Adresse.id) == col(Beleg.adresse_id), isouter=True)
        .where(col(Beleg.id).in_(ids))
    ).all()
    return {
        beleg.id: f"{beleg.belegnr}" + (f" ({adresse.firma})" if adresse else "")
        for beleg, adresse in treffer
    }


def _gefilterte_benutzer_id(
    session: Session, benutzer: Benutzer, mitarbeiter: str
) -> int | None:
    """Nach welchem Mitarbeiter wird gefiltert? ``None`` = alle (nur für Admins).

    Ein Nicht-Admin sieht ausschließlich seine eigenen Zeilen — der Parameter
    ``mitarbeiter`` wird für ihn ignoriert (sonst wäre er ein IDOR-Einfallstor).
    """
    if not ist_admin(session, benutzer):
        return benutzer.id
    wert = (mitarbeiter or "").strip()
    if not wert or wert == "alle":
        return None
    try:
        return int(wert)
    except ValueError:
        return None


def _listen_kontext(
    session: Session, benutzer: Benutzer, mitarbeiter: str, seite: int
) -> dict:
    admin = ist_admin(session, benutzer)
    benutzer_id = _gefilterte_benutzer_id(session, benutzer, mitarbeiter)
    zeilen, pag = _blaettere_zeilen(session, benutzer_id, mitarbeiter if admin else "", seite)
    return {
        "zeilen": zeilen,
        "pag": pag,
        "summe": zeiterfassung_service.summe_je_filter(session, benutzer_id=benutzer_id),
        "betrag_map": {z.id: zeiterfassung_service.zeilen_betrag(z) for z, _ in zeilen},
        "beleg_namen": _beleg_bezeichnungen(session, zeilen),
        "ist_admin_ansicht": admin,
        "mitarbeiter_liste": _mitarbeiter(session) if admin else [],
        "mitarbeiter": mitarbeiter if admin else "",
        "eigene_id": benutzer.id,
        "gefiltert": bool(benutzer_id is not None and admin),
    }


def _blaettere_zeilen(
    session: Session, benutzer_id: int | None, mitarbeiter: str, seite: int
) -> tuple[list, Blaetterwerk]:
    return blaettere(
        session,
        zeiterfassung_service.zeilen_stmt(benutzer_id=benutzer_id),
        seite,
        groesse=SEITENGROESSE,
        basis_url="/zeiterfassung",
        params={"mitarbeiter": mitarbeiter},
        htmx_url="/zeiterfassung/zeilen",
        htmx_target="#zeiten-tabelle",
    )


def _formular_kontext(
    zeile: Zeiterfassung,
    *,
    neu: bool,
    session: Session,
    benutzer: Benutzer,
    fehler: str = "",
) -> dict:
    admin = ist_admin(session, benutzer)
    return {
        "zeile": zeile,
        "neu": neu,
        "auftraege": _auftrags_optionen(session, zeile.beleg_id),
        "ist_admin_ansicht": admin,
        "mitarbeiter_liste": _mitarbeiter(session) if admin else [],
        "fehler": fehler,
    }


def _uebersicht_redirect(fehler: str = "", mitarbeiter: str = "") -> RedirectResponse:
    url = "/zeiterfassung"
    params = []
    if mitarbeiter:
        params.append(f"mitarbeiter={quote(mitarbeiter)}")
    if fehler:
        params.append(f"fehler={quote(fehler)}")
    if params:
        url += "?" + "&".join(params)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# --------------------------------------------------------------------- Übersicht


@router.get("")
def zeiten_uebersicht(  # noqa: PLR0913
    request: Request,
    mitarbeiter: str = "",
    seite: int = 1,
    fehler: str = "",
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    kontext = _listen_kontext(session, benutzer, mitarbeiter, seite)
    kontext["fehler"] = fehler
    return templates.TemplateResponse(request, "zeiterfassung/liste.html", kontext)


@router.get("/zeilen")
def zeiten_zeilen(
    request: Request,
    mitarbeiter: str = "",
    seite: int = 1,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """HTMX-Partial: nur die Tabellenzeilen (inkl. Blätter-Zeile)."""
    return templates.TemplateResponse(
        request,
        "partials/zeiterfassung_zeilen.html",
        _listen_kontext(session, benutzer, mitarbeiter, seite),
    )


# ----------------------------------------------------------------------- Erfassen


@router.get("/neu")
def zeit_neu_form(
    request: Request,
    beleg_id: int | None = None,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Erfassungsformular; Stundensatz aus dem Mitarbeiterstamm vorbelegt."""
    vorlage = Zeiterfassung(
        benutzer_id=benutzer.id,
        beleg_id=beleg_id,
        datum=date.today(),
        stunden=Decimal("0"),
        stundensatz=benutzer.stundensatz,
    )
    return templates.TemplateResponse(
        request,
        "zeiterfassung/form.html",
        _formular_kontext(vorlage, neu=True, session=session, benutzer=benutzer),
    )


def _benutzer_id_aus_form(
    session: Session, benutzer: Benutzer, gewaehlt: str, vorgabe: int
) -> int:
    """Für welchen Mitarbeiter wird gebucht? Fremde nur als Admin (sonst eigene ID)."""
    if not ist_admin(session, benutzer):
        return vorgabe
    wert = (gewaehlt or "").strip()
    if not wert:
        return vorgabe
    try:
        gewaehlte_id = int(wert)
    except ValueError:
        return vorgabe
    return gewaehlte_id if session.get(Benutzer, gewaehlte_id) is not None else vorgabe


def _beleg_id_aus_form(text: str) -> int | None:
    wert = (text or "").strip()
    if not wert:
        return None
    try:
        return int(wert)
    except ValueError:
        return None


@router.post("/neu")
def zeit_neu(  # noqa: PLR0913
    request: Request,
    datum: str = Form(""),
    stunden: str = Form("0"),
    stundensatz: str = Form("0"),
    taetigkeit: str = Form(""),
    beleg_id: str = Form(""),
    benutzer_id: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    ziel_benutzer_id = _benutzer_id_aus_form(session, benutzer, benutzer_id, benutzer.id)
    werte = {
        "benutzer_id": ziel_benutzer_id,
        "datum": _parse_datum(datum) or date.today(),
        "stunden": parse_menge(stunden, "Stunden"),
        "stundensatz": parse_betrag(stundensatz, "Stundensatz"),
        "taetigkeit": taetigkeit,
        "beleg_id": _beleg_id_aus_form(beleg_id),
    }
    try:
        zeiterfassung_service.erfasse_zeit(session, **werte)
        session.commit()
    except WerkplusFehler as exc:
        session.rollback()
        return templates.TemplateResponse(
            request,
            "zeiterfassung/form.html",
            _formular_kontext(
                Zeiterfassung(**werte), neu=True, session=session,
                benutzer=benutzer, fehler=str(exc),
            ),
        )
    return _uebersicht_redirect()


# ------------------------------------------------------------- Ändern und Löschen


@router.get("/{zeit_id}")
def zeit_bearbeiten_form(
    request: Request,
    zeit_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    zeile = _lade_zeile(session, zeit_id, benutzer)
    return templates.TemplateResponse(
        request,
        "zeiterfassung/form.html",
        _formular_kontext(zeile, neu=False, session=session, benutzer=benutzer),
    )


@router.post("/{zeit_id}")
def zeit_bearbeiten(  # noqa: PLR0913
    request: Request,
    zeit_id: int,
    datum: str = Form(""),
    stunden: str = Form("0"),
    stundensatz: str = Form("0"),
    taetigkeit: str = Form(""),
    beleg_id: str = Form(""),
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    zeile = _lade_zeile(session, zeit_id, benutzer)
    try:
        zeiterfassung_service.aendere_zeit(
            session,
            zeile,
            datum=_parse_datum(datum) or zeile.datum,
            stunden=parse_menge(stunden, "Stunden"),
            stundensatz=parse_betrag(stundensatz, "Stundensatz"),
            taetigkeit=taetigkeit,
            beleg_id=_beleg_id_aus_form(beleg_id),
        )
        session.commit()
    except WerkplusFehler as exc:
        session.rollback()
        return templates.TemplateResponse(
            request,
            "zeiterfassung/form.html",
            _formular_kontext(
                _lade_zeile(session, zeit_id, benutzer), neu=False,
                session=session, benutzer=benutzer, fehler=str(exc),
            ),
        )
    return _uebersicht_redirect()


@router.post("/{zeit_id}/loeschen")
def zeit_loeschen(
    request: Request,
    zeit_id: int,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    zeile = _lade_zeile(session, zeit_id, benutzer)
    try:
        zeiterfassung_service.loesche_zeit(session, zeile)
        session.commit()
    except WerkplusFehler as exc:
        session.rollback()
        return _uebersicht_redirect(fehler=str(exc))
    return _uebersicht_redirect()
