"""Gemeinsame Jinja2-Umgebung mit deutschen Formatierungs-Filtern."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def format_euro(wert: Decimal | None) -> str:
    """1234.5 → '1.234,50' (deutsches Zahlenformat)."""
    if wert is None:
        wert = Decimal("0")
    q = Decimal(wert).quantize(Decimal("0.01"))
    ganz, komma = f"{q:,.2f}".split(".")
    return ganz.replace(",", ".") + "," + komma


def format_datum(d: date | datetime | None) -> str:
    if d is None:
        return ""
    return d.strftime("%d.%m.%Y")


def format_menge(wert: Decimal | None) -> str:
    """Mengen ohne unnötige Nachkommastellen: 3.00 → '3', 2.50 → '2,5'."""
    if wert is None:
        return ""
    d = Decimal(wert).normalize()
    text = f"{d:f}"
    return text.replace(".", ",")


def brutto_wert(netto: Decimal | None, ust_satz: int) -> Decimal:
    """Bruttopreis aus Nettopreis und USt-Satz (reines Decimal, kaufmännisch gerundet)."""
    n = Decimal(netto if netto is not None else 0)
    brutto = n * (Decimal("1") + Decimal(int(ust_satz)) / Decimal("100"))
    return brutto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


templates.env.filters["euro"] = format_euro
templates.env.filters["datum"] = format_datum
templates.env.filters["menge"] = format_menge
templates.env.filters["brutto"] = brutto_wert

# Berechtigungs-Helfer für Templates (Navigation ausblenden, Preise/Marge verbergen).
# Import hier unten, um Zirkelimporte beim Modulaufbau zu vermeiden.
from app.services import berechtigung  # noqa: E402

templates.env.globals["darf_bereich"] = berechtigung.darf_bereich
templates.env.globals["darf_kalkulation"] = berechtigung.darf_kalkulation
# Hinweis: Kein globales `firma` mehr — die Firmenstammdaten stammen jetzt aus der
# DB (app.services.firma_service). Nur PDF-Templates nutzen `firma`; sie erhalten es
# über den PDF-Kontext (pdf_service._baue_kontext). Kein Nicht-PDF-Template greift
# auf `firma` zu (nur `adresse.firma` = Kundenfirma), daher ist das Global entbehrlich.
