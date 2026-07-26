"""Zentrales Parsen von Formular-Eingaben (deutsches Zahlenformat)."""

from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, status


def parse_decimal(text: str | None) -> Decimal:
    """Parst Beträge im deutschen Format; leere Eingabe → Decimal('0').

    - Komma ist der Dezimaltrenner; Punkte gelten dann als Tausendertrenner
      ("1.234,56" → 1234.56).
    - Ohne Komma bleibt ein Punkt der Dezimaltrenner ("12.50" → 12.50, "100" → 100).
    """
    if text is None:
        return Decimal("0")
    s = text.strip()
    if not s:
        return Decimal("0")
    bereinigt = s.replace(".", "").replace(",", ".") if "," in s else s
    try:
        wert = Decimal(bereinigt)
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Ungültige Zahl: {text!r}",
        ) from exc
    # Decimal akzeptiert "NaN"/"Infinity" klaglos — beide würden Beträge/Bestände
    # dauerhaft verderben. Nur endliche Zahlen zulassen.
    if not wert.is_finite():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Ungültige Zahl: {text!r}",
        )
    return wert


# --- Wertebereiche ----------------------------------------------------------
# Bewusst großzügige Obergrenzen: sie fangen Tippfehler (verrutschtes Komma,
# Zahlenwust) ab, ohne legitime Geschäftsfälle zu behindern.
MAX_BETRAG = Decimal("1000000000")  # 1 Mrd. je Zeile
MAX_MENGE = Decimal("1000000")  # 1 Mio. Einheiten je Zeile
MAX_AUFSCHLAG_PROZENT = Decimal("100000")


def _fehler(feld: str, hinweis: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"{feld}: {hinweis}",
    )


def parse_prozent(
    text: str | None,
    feld: str,
    min_wert: Decimal = Decimal("0"),
    max_wert: Decimal = Decimal("100"),
) -> Decimal:
    """Parst einen Prozentsatz und prüft den Wertebereich (Standard 0–100 %).

    Ein Rabatt über 100 % würde negative Preise erzeugen, ein negativer Rabatt
    wäre ein verkappter Zuschlag — beides ist ein Eingabefehler.
    """
    wert = parse_decimal(text)
    if wert < min_wert or wert > max_wert:
        raise _fehler(
            feld, f"muss zwischen {min_wert:g} und {max_wert:g} % liegen (war {wert:g})"
        )
    return wert


def parse_betrag(text: str | None, feld: str) -> Decimal:
    """Parst einen Geldbetrag und wehrt unplausible Größenordnungen ab.

    Negative Beträge sind erlaubt — Abzugspositionen (z. B. der Abschlagsabzug
    einer Schlussrechnung) haben fachlich einen negativen Einzelpreis.
    """
    wert = parse_decimal(text)
    if abs(wert) > MAX_BETRAG:
        raise _fehler(feld, f"unplausibel groß (war {wert:g})")
    return wert


def parse_menge(text: str | None, feld: str = "Menge") -> Decimal:
    """Parst eine Menge und wehrt unplausible Größenordnungen ab.

    Negative Mengen bleiben zulässig (Korrektur-/Rückgabezeilen).
    """
    wert = parse_decimal(text)
    if abs(wert) > MAX_MENGE:
        raise _fehler(feld, f"unplausibel groß (war {wert:g})")
    return wert
