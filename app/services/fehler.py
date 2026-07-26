"""Domänen-Exceptions des Belegwesens."""


class WerkplusFehler(Exception):
    """Basisklasse aller WerkPlus-Domänenfehler."""


class UngueltigerStatusUebergang(WerkplusFehler):
    """Der gewünschte Statusübergang ist laut Statusmaschine nicht erlaubt."""


class BelegGesperrt(WerkplusFehler):
    """Mutation an einem gedruckten/erledigten/stornierten Beleg."""


class UngueltigeBelegart(WerkplusFehler):
    """Belegart ist für diese Operation nicht zulässig (z. B. keine Folgebelegart)."""


class SnPflichtVerletzt(WerkplusFehler):
    """Anzahl zugewiesener/erfasster Seriennummern passt beim Buchen nicht zur Menge."""


class SnNichtVerfuegbar(WerkplusFehler):
    """Seriennummer nicht auf Lager, bereits vergeben oder doppelt angegeben."""


class StornoNichtMoeglich(WerkplusFehler):
    """Der Storno ist fachlich nicht möglich (z. B. Seriennummern bereits verkauft)."""


class ZeitBereitsVerrechnet(WerkplusFehler):
    """Änderung/Löschung einer Arbeitszeit, die schon als Belegposition steht.

    Eine verrechnete Zeile ist Teil eines Belegs. Sie nachträglich zu ändern
    würde Erfassung und Rechnung auseinanderlaufen lassen — deshalb gesperrt.
    """


class LagerbuchungFehlgeschlagen(WerkplusFehler):
    """Bestandsfortschreibung war auch nach mehreren Versuchen nicht möglich.

    Tritt auf, wenn die Datenbank dauerhaft durch parallele Schreiber belegt ist
    ("database is locked" / SQLITE_BUSY_SNAPSHOT) oder der optimistische
    Compare-and-Swap wiederholt verloren geht. Der Aufrufer soll zurückrollen und
    zum erneuten Versuch auffordern — kein Serverfehler.
    """
