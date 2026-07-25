"""Eigene SQLAlchemy-Typen.

Geldbeträge und Mengen werden als TEXT gespeichert und als ``Decimal`` gelesen,
damit niemals Float-Rundungsfehler in die Datenbank gelangen (Vorgabe: nie float).
"""

from decimal import Decimal

from sqlalchemy import String, TypeDecorator


class DecimalStr(TypeDecorator):
    """Speichert Decimal verlustfrei als String (SQLite hat keinen Decimal-Typ)."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(Decimal(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)
