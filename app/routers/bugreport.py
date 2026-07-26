"""Bug-/Feature-Melder: lokale Speicherung + optionaler GitHub-Issue-Sync.

Jeder angemeldete Benutzer darf melden (keine Bereichswache). Meldungen werden
immer lokal gespeichert; ist GitHub konfiguriert, wird zusätzlich ein Issue
angelegt. Scheitert das, bleibt die Meldung lokal erhalten (Fehler wird notiert).
"""

import logging
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies import get_current_user, require_admin
from app.models import Benutzer, BugReport, MeldungTyp
from app.services import github_service
from app.templating import templates

router = APIRouter(prefix="/bugreport", tags=["bugreport"])

logger = logging.getLogger(__name__)

# Screenshots landen unter app/static/bugreports/{id}.png
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "static" / "bugreports"
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5 MB

TYP_LABELS = {
    MeldungTyp.BUG: "Fehler",
    MeldungTyp.FEATURE: "Wunsch",
}


def _baue_koerper(report: BugReport) -> str:
    """Erzeugt den Markdown-Text für das GitHub-Issue."""
    zeit = report.erstellt_am.strftime("%d.%m.%Y %H:%M")
    zeilen = [
        report.beschreibung or "_(keine Beschreibung)_",
        "",
        "---",
        f"- **Melder:** {report.melder_name}",
        f"- **Seite:** {report.seite}",
        f"- **Browser:** {report.browser}",
        f"- **Gemeldet am:** {zeit}",
    ]
    if report.screenshot_datei:
        zeilen.append(
            "- **Screenshot:** in WerkPlus unter Bug-Meldungen hinterlegt"
        )
    return "\n".join(zeilen)


def _sende_an_github(session: Session, report: BugReport) -> BugReport:
    """Versucht, für den Report ein GitHub-Issue anzulegen. Idempotent-sicher."""
    labels = ["bug"] if report.typ == MeldungTyp.BUG else ["enhancement"]
    try:
        nummer, url = github_service.erstelle_issue(
            report.titel, _baue_koerper(report), labels
        )
        report.gesendet = True
        report.github_issue_nr = nummer
        report.github_url = url
        report.fehler = ""
    except github_service.GitHubNichtKonfiguriert:
        logger.info(
            "Meldung %s nur lokal gespeichert: GitHub ist nicht konfiguriert.", report.id
        )
        report.fehler = "GitHub ist nicht konfiguriert – nur lokal gespeichert."
    except Exception as fehler:  # noqa: BLE001 — Meldung darf lokal nie verloren gehen
        # Verhalten bleibt unverändert (Meldung bleibt lokal erhalten), aber der
        # Fehler ist ab jetzt im Log nachvollziehbar. Der GitHub-Token steckt nur
        # im Request-Header, nie in der Exception — er kann hier nicht auftauchen.
        logger.exception(
            "GitHub-Übertragung der Meldung %s fehlgeschlagen — bleibt lokal gespeichert.",
            report.id,
        )
        report.fehler = str(fehler)
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def _speichere_screenshot(report: BugReport, screenshot: UploadFile) -> None:
    """Speichert ein Bild-Upload unter static/bugreports/{id}.png (max. 5 MB)."""
    if not screenshot.content_type or not screenshot.content_type.startswith("image/"):
        return
    inhalt = screenshot.file.read(MAX_SCREENSHOT_BYTES + 1)
    if not inhalt or len(inhalt) > MAX_SCREENSHOT_BYTES:
        return
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ziel = SCREENSHOT_DIR / f"{report.id}.png"
    ziel.write_bytes(inhalt)
    report.screenshot_datei = ziel.name


def _ergebnis_fragment(report: BugReport) -> HTMLResponse:
    """Kleines HTMX-Antwortfragment für den Ergebnis-Container im Modal."""
    if report.gesendet and report.github_url:
        inhalt = (
            '<div class="alert alert-success mb-0" role="alert">'
            "Danke, gemeldet! Als GitHub-Issue "
            f'<a href="{escape(report.github_url)}" target="_blank" '
            f'rel="noopener">#{report.github_issue_nr}</a> angelegt.'
            "</div>"
        )
    elif report.fehler:
        inhalt = (
            '<div class="alert alert-warning mb-0" role="alert">'
            "Danke, gemeldet und lokal gespeichert. GitHub-Übertragung offen: "
            f"{escape(report.fehler)}"
            "</div>"
        )
    else:
        inhalt = (
            '<div class="alert alert-success mb-0" role="alert">'
            "Danke, gemeldet!"
            "</div>"
        )
    return HTMLResponse(inhalt)


@router.get("/modal")
def bug_melder_modal(
    request: Request,
    benutzer: Benutzer = Depends(get_current_user),
):
    """Liefert das Modal-Partial (für optionales HTMX-Lazy-Load)."""
    return templates.TemplateResponse(
        request, "partials/bug_melder_modal.html", {}
    )


@router.get("")
def bugreport_liste(
    request: Request,
    benutzer: Benutzer = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Alle Meldungen — nur für Admins (enthält fremde Meldungen samt Browser-Infos)."""
    reports = session.exec(
        select(BugReport).order_by(BugReport.erstellt_am.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "bugreport/liste.html",
        {"reports": reports, "typ_labels": TYP_LABELS},
    )


@router.post("")
async def bugreport_anlegen(
    request: Request,
    typ: str = Form(MeldungTyp.BUG.value),
    titel: str = Form(...),
    beschreibung: str = Form(""),
    seite: str = Form(""),
    screenshot: UploadFile | None = None,
    benutzer: Benutzer = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    try:
        typ_wert = MeldungTyp(typ)
    except ValueError:
        typ_wert = MeldungTyp.BUG

    report = BugReport(
        typ=typ_wert,
        titel=titel.strip(),
        beschreibung=beschreibung.strip(),
        seite=seite,
        browser=request.headers.get("user-agent", ""),
        melder_id=benutzer.id,
        melder_name=benutzer.name,
    )
    session.add(report)
    session.commit()
    session.refresh(report)

    if screenshot is not None and screenshot.filename:
        _speichere_screenshot(report, screenshot)
        session.add(report)
        session.commit()
        session.refresh(report)

    if github_service.ist_konfiguriert():
        _sende_an_github(session, report)

    return _ergebnis_fragment(report)


@router.post("/{report_id}/senden")
def bugreport_erneut_senden(
    request: Request,
    report_id: int,
    benutzer: Benutzer = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Erneuter GitHub-Versand — nur für Admins (löst externen Aufruf aus)."""
    report = session.get(BugReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Meldung nicht gefunden"
        )
    _sende_an_github(session, report)
    return RedirectResponse("/bugreport", status_code=status.HTTP_302_FOUND)
