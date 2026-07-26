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
