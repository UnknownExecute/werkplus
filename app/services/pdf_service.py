"""PDF-Rendering für Belege — eigenes Jinja2-Environment + WeasyPrint (lazy geladen).

WeasyPrint benötigt eine GTK/Pango-Runtime; fehlt diese (typisch auf Windows),
schlägt bereits ``import weasyprint`` mit OSError fehl. Der Import passiert
deshalb lazy in :func:`_lade_weasyprint`, das Ergebnis (Modul oder Fehler) wird
gecacht. Ohne Runtime wirft :func:`render_beleg_pdf` :class:`PdfNichtVerfuegbar`.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import jinja2
from sqlmodel import Session, select

from app.models.adresse import Adresse, Lieferadresse
from app.models.artikel import Artikel
from app.models.beleg import (
    AUFTRAGSART_LABELS,
    Beleg,
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
)
from app.models.seriennummer import Seriennummer
from app.services import firma_service
from app.services.berechnung import parse_steuer_details, positions_netto
from app.templating import format_datum, format_euro, format_menge

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

BELEGART_LABEL: dict[Belegart, str] = {
    Belegart.ANGEBOT: "Angebot",
    Belegart.AUFTRAG: "Auftragsbestätigung",
    Belegart.LIEFERSCHEIN: "Lieferschein",
    Belegart.RECHNUNG: "Rechnung",
    Belegart.GUTSCHRIFT: "Gutschrift",
    Belegart.BESTELLUNG: "Bestellung",
    Belegart.EK_LIEFERSCHEIN: "Wareneingang",
    Belegart.EK_RECHNUNG: "EK-Rechnung",
}

# Explizite Zuordnung Belegart -> Template. Fehlt eine Art hier, greift der
# Namenskonventions-Fallback (pdf/<belegart>.html) und zuletzt pdf/basis.html.
BELEGART_TEMPLATE: dict[Belegart, str] = {
    Belegart.LIEFERSCHEIN: "pdf/lieferschein.html",
    Belegart.RECHNUNG: "pdf/rechnung.html",
    Belegart.GUTSCHRIFT: "pdf/gutschrift.html",
    Belegart.BESTELLUNG: "pdf/bestellung.html",
    Belegart.EK_LIEFERSCHEIN: "pdf/ek_lieferschein.html",
    Belegart.EK_RECHNUNG: "pdf/ek_rechnung.html",
}


class PdfNichtVerfuegbar(Exception):
    """WeasyPrint ist nicht importierbar (z. B. fehlende GTK/Pango-Runtime)."""


# Cache des lazy Imports: entweder das Modul oder der Fehler des ersten Versuchs.
_weasyprint: ModuleType | None = None
_weasyprint_fehler: BaseException | None = None


# Übliche Installationsorte der GTK3-Runtime unter Windows (WeasyPrint-Voraussetzung).
# Der Installer erweitert nur den System-PATH; bereits laufende Prozesse sehen das
# nicht — deshalb wird das bin-Verzeichnis hier direkt als DLL-Pfad ergänzt.
_GTK_BIN_KANDIDATEN = (
    Path("C:/Program Files/GTK3-Runtime Win64/bin"),
    Path("C:/Program Files (x86)/GTK3-Runtime Win64/bin"),
)


def _ergaenze_gtk_dll_pfad() -> None:
    import os
    import sys

    if sys.platform != "win32":
        return
    for kandidat in _GTK_BIN_KANDIDATEN:
        if kandidat.is_dir():
            os.add_dll_directory(str(kandidat))
            os.environ["PATH"] = f"{kandidat};{os.environ.get('PATH', '')}"
            return


def _importiere_weasyprint() -> ModuleType:
    """Führt den eigentlichen Import aus — in Tests gezielt patchbar."""
    _ergaenze_gtk_dll_pfad()
    import weasyprint

    return weasyprint


def _lade_weasyprint() -> ModuleType:
    """Lädt WeasyPrint lazy und gecacht; wirft PdfNichtVerfuegbar bei Fehlschlag."""
    global _weasyprint, _weasyprint_fehler
    if _weasyprint is not None:
        return _weasyprint
    if _weasyprint_fehler is None:
        try:
            _weasyprint = _importiere_weasyprint()
            return _weasyprint
        except (ImportError, OSError) as fehler:
            _weasyprint_fehler = fehler
    raise PdfNichtVerfuegbar(
        f"WeasyPrint ist auf diesem System nicht verfügbar: {_weasyprint_fehler}"
    ) from _weasyprint_fehler


# Eigenes Environment (unabhängig von app.templating.templates / FastAPI):
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
)
_env.filters["euro"] = format_euro
_env.filters["datum"] = format_datum
_env.filters["menge"] = format_menge


def _lade_positionen(session: Session, beleg: Beleg) -> list[BelegPosition]:
    return list(
        session.exec(
            select(BelegPosition)
            .where(BelegPosition.beleg_id == beleg.id)
            .order_by(BelegPosition.pos_nr)  # type: ignore[arg-type]
        ).all()
    )


def _seriennummern(session: Session, position: BelegPosition) -> list[str]:
    return list(
        session.exec(
            select(Seriennummer.sn)
            .join(
                BelegPositionSeriennummer,
                BelegPositionSeriennummer.seriennummer_id == Seriennummer.id,  # type: ignore[arg-type]
            )
            .where(BelegPositionSeriennummer.position_id == position.id)
            .order_by(Seriennummer.sn)  # type: ignore[arg-type]
        ).all()
    )


def _baue_kontext(session: Session, beleg: Beleg) -> dict:
    """Sammelt alle Template-Daten für einen Beleg."""
    adresse = session.get(Adresse, beleg.adresse_id)
    positionen = _lade_positionen(session, beleg)

    sn_je_position: dict[int, list[str]] = {}
    artikelnr_je_position: dict[int, str] = {}
    netto_je_position: dict[int, Decimal] = {}
    for pos in positionen:
        netto_je_position[pos.id] = positions_netto(pos.menge, pos.einzelpreis, pos.rabatt_prozent)
        if pos.artikel_id is not None:
            artikel = session.get(Artikel, pos.artikel_id)
            if artikel is not None:
                artikelnr_je_position[pos.id] = artikel.artikelnr
        sns = _seriennummern(session, pos)
        if sns:
            sn_je_position[pos.id] = sns

    summen = {
        "je_steuersatz": parse_steuer_details(beleg.steuer_details),
        "netto": beleg.summe_netto,
        "ust": beleg.summe_ust,
        "brutto": beleg.summe_brutto,
    }

    faelligkeit: date | None = None
    if beleg.belegart == Belegart.RECHNUNG:
        faelligkeit = beleg.datum + timedelta(days=beleg.zahlungsziel_tage)

    vorgaenger: Beleg | None = None
    if beleg.vorgaenger_beleg_id is not None:
        vorgaenger = session.get(Beleg, beleg.vorgaenger_beleg_id)

    lieferadresse = (
        session.get(Lieferadresse, beleg.lieferadresse_id) if beleg.lieferadresse_id else None
    )

    return {
        "beleg": beleg,
        "adresse": adresse,
        "lieferadresse": lieferadresse,
        "positionen": positionen,
        "sn_je_position": sn_je_position,
        "artikelnr_je_position": artikelnr_je_position,
        "netto_je_position": netto_je_position,
        "summen": summen,
        "firma": firma_service.lade_firma(session),
        "belegart_label": BELEGART_LABEL[beleg.belegart],
        "auftragsart_label": AUFTRAGSART_LABELS.get(beleg.auftragsart, ""),
        "faelligkeit": faelligkeit,
        "vorgaenger": vorgaenger,
    }


def _waehle_template(beleg: Beleg) -> jinja2.Template:
    """Wählt das Template zur Belegart; Fallback auf pdf/basis.html bei Unbekanntem."""
    name = BELEGART_TEMPLATE.get(beleg.belegart, f"pdf/{beleg.belegart.value}.html")
    try:
        return _env.get_template(name)
    except jinja2.TemplateNotFound:
        return _env.get_template("pdf/basis.html")


def render_beleg_html(session: Session, beleg: Beleg) -> str:
    """Rendert den Beleg als eigenständiges HTML-Dokument (Druck-Layout)."""
    template = _waehle_template(beleg)
    return template.render(**_baue_kontext(session, beleg))


def render_beleg_pdf(session: Session, beleg: Beleg) -> bytes:
    """Rendert den Beleg als PDF (A4). Wirft PdfNichtVerfuegbar ohne WeasyPrint."""
    weasyprint = _lade_weasyprint()
    html = render_beleg_html(session, beleg)
    return weasyprint.HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()
