"""Kern-Services des Belegwesens: Statusmaschine, Summen, Positionen, Folgebelege.

Keine FastAPI-Abhängigkeit. Alle Funktionen flushen nur — der Aufrufer committed.
"""

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from sqlmodel import Session, select

from app.models.adresse import Adresse
from app.models.artikel import Artikel
from app.models.beleg import (
    Angebotsergebnis,
    Beleg,
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
    BelegStatus,
)
from app.services import berechnung
from app.services.fehler import (
    BelegGesperrt,
    UngueltigeBelegart,
    UngueltigerStatusUebergang,
    WerkplusFehler,
)
from app.services.nummernkreis_service import naechste_belegnr, naechste_nummer

ERLAUBTE_UEBERGAENGE: dict[BelegStatus, set[BelegStatus]] = {
    BelegStatus.ENTWURF: {BelegStatus.GEDRUCKT, BelegStatus.STORNIERT},
    BelegStatus.GEDRUCKT: {BelegStatus.ERLEDIGT, BelegStatus.STORNIERT},
    BelegStatus.ERLEDIGT: set(),
    BelegStatus.STORNIERT: set(),
}

# Belegketten: Verkauf und Einkauf
FOLGEBELEGART: dict[Belegart, Belegart] = {
    Belegart.ANGEBOT: Belegart.AUFTRAG,
    Belegart.AUFTRAG: Belegart.LIEFERSCHEIN,
    Belegart.LIEFERSCHEIN: Belegart.RECHNUNG,
    Belegart.RECHNUNG: Belegart.GUTSCHRIFT,
    Belegart.PREISANFRAGE: Belegart.BESTELLUNG,
    Belegart.BESTELLUNG: Belegart.EK_LIEFERSCHEIN,
    Belegart.EK_LIEFERSCHEIN: Belegart.EK_RECHNUNG,
}

VERKAUFSARTEN: frozenset[Belegart] = frozenset(
    {
        Belegart.ANGEBOT,
        Belegart.AUFTRAG,
        Belegart.LIEFERSCHEIN,
        Belegart.RECHNUNG,
        Belegart.GUTSCHRIFT,
    }
)

# Hook-Punkte für Lagerwirkung (M4): fn(session, beleg, benutzer_id)
BuchungsHook = Callable[[Session, Beleg, int | None], None]
_buchungs_hooks: dict[Belegart, BuchungsHook] = {}  # bei entwurf -> gedruckt
_storno_hooks: dict[Belegart, BuchungsHook] = {}  # bei gedruckt -> storniert


def registriere_buchungs_hook(belegart: Belegart, fn: BuchungsHook) -> None:
    """Registriert einen Hook, der bei entwurf→gedruckt aufgerufen wird."""
    _buchungs_hooks[belegart] = fn


def registriere_storno_hook(belegart: Belegart, fn: BuchungsHook) -> None:
    """Registriert einen Hook, der bei gedruckt→storniert aufgerufen wird."""
    _storno_hooks[belegart] = fn


def pruefe_bearbeitbar(beleg: Beleg) -> None:
    """Wirft BelegGesperrt, wenn der Beleg nicht mehr im Entwurf ist."""
    if beleg.status != BelegStatus.ENTWURF:
        raise BelegGesperrt(
            f"Beleg {beleg.belegnr} ist {beleg.status} und kann nicht geändert werden"
        )


def erstelle_beleg(
    session: Session,
    belegart: Belegart,
    adresse: Adresse,
    benutzer_id: int | None = None,
    **kopf,
) -> Beleg:
    """Legt einen neuen Beleg im Entwurf an; zieht Nummer, übernimmt Zahlungsziel."""
    kopf.setdefault("zahlungsziel_tage", adresse.zahlungsziel_tage)
    beleg = Beleg(
        belegart=belegart,
        belegnr=naechste_belegnr(session, belegart),
        status=BelegStatus.ENTWURF,
        adresse_id=adresse.id,
        erstellt_von=benutzer_id,
        **kopf,
    )
    session.add(beleg)
    session.flush()
    return beleg


def _lade_positionen(session: Session, beleg: Beleg) -> list[BelegPosition]:
    return list(
        session.exec(
            select(BelegPosition)
            .where(BelegPosition.beleg_id == beleg.id)
            .order_by(BelegPosition.pos_nr)  # type: ignore[arg-type]
        ).all()
    )


def aktualisiere_summen(session: Session, beleg: Beleg) -> None:
    """Berechnet Netto/USt/Brutto + Steuer-Aufschlüsselung neu und schreibt sie in den Kopf."""
    summen = berechnung.beleg_summen(_lade_positionen(session, beleg))
    beleg.summe_netto = summen.netto_gesamt
    beleg.summe_ust = summen.ust_gesamt
    beleg.summe_brutto = summen.brutto_gesamt
    beleg.steuer_details = berechnung.steuer_details_json(summen)
    session.add(beleg)
    session.flush()


def setze_status(
    session: Session,
    beleg: Beleg,
    neuer_status: BelegStatus,
    benutzer_id: int | None = None,
) -> None:
    """Validiert und vollzieht einen Statusübergang (inkl. Buchungs-/Storno-Hooks)."""
    if beleg.ist_muster:
        raise UngueltigerStatusUebergang(
            f"Muster {beleg.belegnr} kann nicht gebucht werden — "
            "zuerst einen Beleg aus dem Muster erzeugen"
        )
    if neuer_status not in ERLAUBTE_UEBERGAENGE.get(beleg.status, set()):
        raise UngueltigerStatusUebergang(
            f"Beleg {beleg.belegnr}: Übergang {beleg.status} -> {neuer_status} nicht erlaubt"
        )
    hook: BuchungsHook | None = None
    if beleg.status == BelegStatus.ENTWURF and neuer_status == BelegStatus.GEDRUCKT:
        aktualisiere_summen(session, beleg)  # Summen einfrieren
        beleg.gedruckt_am = datetime.now()
        hook = _buchungs_hooks.get(beleg.belegart)
    elif beleg.status == BelegStatus.GEDRUCKT and neuer_status == BelegStatus.STORNIERT:
        hook = _storno_hooks.get(beleg.belegart)
    beleg.status = neuer_status
    session.add(beleg)
    session.flush()
    if hook is not None:
        hook(session, beleg, benutzer_id)


def fuege_position_hinzu(
    session: Session,
    beleg: Beleg,
    artikel: Artikel | None = None,
    *,
    bezeichnung: str | None = None,
    langtext: str | None = None,
    menge: Decimal = Decimal("1"),
    einzelpreis: Decimal | None = None,
    rabatt_prozent: Decimal | None = None,
    einheit: str | None = None,
    ust_satz: int | None = None,
) -> BelegPosition:
    """Hängt eine Artikel- oder Freitextposition an; Defaults: VK-Preis, Kundenrabatt."""
    pruefe_bearbeitbar(beleg)
    if artikel is None and bezeichnung is None:
        raise WerkplusFehler("Position braucht einen Artikel oder eine Freitext-Bezeichnung")
    if einzelpreis is None:
        einzelpreis = vorschlagspreis(session, beleg, artikel, menge)
    if rabatt_prozent is None:
        adresse = session.get(Adresse, beleg.adresse_id)
        rabatt_prozent = (
            adresse.standard_rabatt_prozent if adresse is not None else Decimal("0")
        )
    if bezeichnung is None:
        bezeichnung = artikel.bezeichnung if artikel else ""
    if langtext is None:
        langtext = artikel.langtext if artikel else ""
    # Kalkulationsfelder aus dem Artikel vorbelegen (bei Freitextposition alles 0)
    letzte_pos_nr = max((p.pos_nr for p in _lade_positionen(session, beleg)), default=0)
    position = BelegPosition(
        beleg_id=beleg.id,
        pos_nr=letzte_pos_nr + 1,
        artikel_id=artikel.id if artikel is not None else None,
        bezeichnung=bezeichnung,
        langtext=langtext,
        menge=menge,
        einheit=einheit if einheit is not None else (artikel.einheit if artikel else "Stk"),
        listenpreis=artikel.listenpreis_netto if artikel else Decimal("0"),
        rabatt1_prozent=artikel.rabatt1_prozent if artikel else Decimal("0"),
        rabatt2_prozent=artikel.rabatt2_prozent if artikel else Decimal("0"),
        ek_preis=artikel.ek_preis if artikel else Decimal("0"),
        aufschlag_prozent=artikel.aufschlag_prozent if artikel else Decimal("0"),
        einzelpreis=einzelpreis,
        rabatt_prozent=rabatt_prozent,
        ust_satz=ust_satz if ust_satz is not None else (artikel.ust_satz if artikel else 20),
    )
    session.add(position)
    session.flush()
    aktualisiere_summen(session, beleg)
    return position


def vorschlagspreis(
    session: Session, beleg: Beleg, artikel: Artikel | None, menge: Decimal
) -> Decimal:
    """Einzelpreis-Vorschlag für eine Artikelposition.

    Verkaufsbelege: Staffelpreis nach Menge (sonst Stamm-VK). Einkaufsbelege
    (Preisanfrage/Bestellung/Wareneingang/EK-Rechnung): EK beim Lieferanten des
    Belegs, sonst Stamm-EK. Freitext: 0.
    """
    from app.services import artikel_service  # zirkulären Import vermeiden

    if artikel is None:
        return Decimal("0")
    if beleg.belegart in VERKAUFSARTEN:
        return artikel_service.staffelpreis(session, artikel, menge)
    return artikel_service.ek_fuer_lieferant(session, artikel, beleg.adresse_id)


def entferne_position(session: Session, beleg: Beleg, position: BelegPosition) -> None:
    """Löscht eine Position (inkl. SN-Zuordnungen) und rechnet die Summen neu."""
    pruefe_bearbeitbar(beleg)
    if position.beleg_id != beleg.id:
        raise WerkplusFehler("Position gehört nicht zu diesem Beleg")
    for zuordnung in session.exec(
        select(BelegPositionSeriennummer).where(
            BelegPositionSeriennummer.position_id == position.id
        )
    ).all():
        session.delete(zuordnung)
    session.delete(position)
    session.flush()
    aktualisiere_summen(session, beleg)


def verschiebe_position(
    session: Session, beleg: Beleg, position: BelegPosition, richtung: str
) -> None:
    """Tauscht die pos_nr einer Position mit der benachbarten (nur im Entwurf).

    richtung "hoch" tauscht mit der Position davor, "runter" mit der danach.
    An den Enden No-Op. Summen ändern sich nicht — der Aufrufer committed.
    """
    pruefe_bearbeitbar(beleg)
    if position.beleg_id != beleg.id:
        raise WerkplusFehler("Position gehört nicht zu diesem Beleg")
    positionen = _lade_positionen(session, beleg)
    index = next((i for i, p in enumerate(positionen) if p.id == position.id), None)
    if index is None:
        raise WerkplusFehler("Position gehört nicht zu diesem Beleg")
    if richtung == "hoch":
        nachbar_index = index - 1
    elif richtung == "runter":
        nachbar_index = index + 1
    else:
        raise WerkplusFehler(f"Ungültige Richtung: {richtung!r}")
    if nachbar_index < 0 or nachbar_index >= len(positionen):
        return  # am Rand: No-Op
    nachbar = positionen[nachbar_index]
    position.pos_nr, nachbar.pos_nr = nachbar.pos_nr, position.pos_nr
    session.add(position)
    session.add(nachbar)
    session.flush()


def bereits_uebernommen(session: Session, position: BelegPosition) -> Decimal:
    """Summe der Mengen aller (nicht stornierten) Folgepositionen dieser Position.

    Nutzt die ``ursprungs_position_id``-Kette: jede in einen Folgebeleg
    übernommene Teilmenge zeigt auf ihre Ursprungsposition. Stornierte
    Folgebelege zählen nicht (die Menge wird wieder frei).
    """
    if position.id is None:
        return Decimal("0")
    positionen = session.exec(
        select(BelegPosition)
        .join(Beleg, BelegPosition.beleg_id == Beleg.id)  # type: ignore[arg-type]
        .where(BelegPosition.ursprungs_position_id == position.id)
        .where(Beleg.status != BelegStatus.STORNIERT)
    ).all()
    return sum((p.menge for p in positionen), Decimal("0"))


def restmenge(session: Session, position: BelegPosition) -> Decimal:
    """Noch nicht übernommene Menge einer Position (min. 0)."""
    rest = position.menge - bereits_uebernommen(session, position)
    return rest if rest > Decimal("0") else Decimal("0")


def offene_restmengen(session: Session, beleg: Beleg) -> dict[int, Decimal]:
    """Je Position des Belegs die noch offene Restmenge."""
    return {p.id: restmenge(session, p) for p in _lade_positionen(session, beleg)}


def hat_offene_restmengen(session: Session, beleg: Beleg) -> bool:
    """True, solange mindestens eine Position noch eine Restmenge > 0 hat."""
    return any(rest > Decimal("0") for rest in offene_restmengen(session, beleg).values())


def erzeuge_folgebeleg(
    session: Session,
    vorgaenger: Beleg,
    benutzer_id: int | None = None,
    mengen: dict[int, Decimal] | None = None,
) -> Beleg:
    """Erzeugt den Folgebeleg der Kette und setzt den Vorgänger auf erledigt.

    Kette: Angebot→Auftrag→Lieferschein→Rechnung, Rechnung→Gutschrift,
    Bestellung→EK-Lieferschein→EK-Rechnung. Nur aus Status gedruckt oder
    erledigt (erledigt bleibt erlaubt, z. B. Gutschrift aus erledigter Rechnung).
    Positionen werden mitsamt SN-Zuordnungen kopiert (ursprungs_position_id gesetzt).

    ``mengen=None`` (Standard) → alle Positionen mit voller Menge übernehmen und
    den Vorgänger (wenn gedruckt) auf erledigt setzen.

    ``mengen`` als ``{ursprungs_position_id: Menge}`` → Teilübernahme: nur
    Positionen mit gewählter Menge > 0, auf die Restmenge begrenzt (clamping).
    SN-Zuordnungen wandern nur bei voller Positionsmenge mit. Bleiben danach
    noch Restmengen offen, bleibt der Vorgänger gedruckt; erst wenn alles
    übernommen ist, wird er erledigt.
    """
    ziel_art = FOLGEBELEGART.get(vorgaenger.belegart)
    if ziel_art is None:
        raise UngueltigeBelegart(
            f"Für Belegart {vorgaenger.belegart} ist kein Folgebeleg definiert"
        )
    if vorgaenger.status not in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT):
        raise UngueltigerStatusUebergang(
            f"Folgebeleg nur aus gedruckt/erledigt möglich, "
            f"Beleg {vorgaenger.belegnr} ist {vorgaenger.status}"
        )

    # Zu übernehmende Posten als (Ursprungsposition, Menge, volle_Menge?) bestimmen.
    if mengen is None:
        posten = [(p, p.menge, True) for p in _lade_positionen(session, vorgaenger)]
    else:
        posten = []
        for p in _lade_positionen(session, vorgaenger):
            gewaehlt = mengen.get(p.id, Decimal("0"))
            uebernahme = min(gewaehlt, restmenge(session, p))
            if uebernahme > Decimal("0"):
                posten.append((p, uebernahme, uebernahme >= p.menge))
        if not posten:
            raise WerkplusFehler("Keine Mengen zur Übernahme gewählt")

    neu = Beleg(
        belegart=ziel_art,
        belegnr=naechste_belegnr(session, ziel_art),
        status=BelegStatus.ENTWURF,
        adresse_id=vorgaenger.adresse_id,
        datum=date.today(),
        vorgaenger_beleg_id=vorgaenger.id,
        zahlungsziel_tage=vorgaenger.zahlungsziel_tage,
        lieferadresse_id=vorgaenger.lieferadresse_id,
        betreff=vorgaenger.betreff,
        kopftext=vorgaenger.kopftext,
        fusstext=vorgaenger.fusstext,
        erstellt_von=benutzer_id,
    )
    if ziel_art == Belegart.RECHNUNG and vorgaenger.belegart == Belegart.LIEFERSCHEIN:
        neu.leistungsdatum_von = vorgaenger.datum
        neu.leistungsdatum_bis = vorgaenger.datum
    session.add(neu)
    session.flush()

    for pos_nr, (alte_position, uebernahme_menge, volle_menge) in enumerate(posten, start=1):
        neue_position = BelegPosition(
            beleg_id=neu.id,
            pos_nr=pos_nr,
            artikel_id=alte_position.artikel_id,
            bezeichnung=alte_position.bezeichnung,
            langtext=alte_position.langtext,
            menge=uebernahme_menge,
            einheit=alte_position.einheit,
            listenpreis=alte_position.listenpreis,
            rabatt1_prozent=alte_position.rabatt1_prozent,
            rabatt2_prozent=alte_position.rabatt2_prozent,
            ek_preis=alte_position.ek_preis,
            aufschlag_prozent=alte_position.aufschlag_prozent,
            einzelpreis=alte_position.einzelpreis,
            rabatt_prozent=alte_position.rabatt_prozent,
            ust_satz=alte_position.ust_satz,
            ursprungs_position_id=alte_position.id,
        )
        session.add(neue_position)
        session.flush()
        # SN-Zuordnungen nur bei voller Menge mitnehmen — bei Teilmengen wählt
        # der Nutzer die Seriennummern später im Lieferschein.
        if volle_menge:
            for zuordnung in session.exec(
                select(BelegPositionSeriennummer).where(
                    BelegPositionSeriennummer.position_id == alte_position.id
                )
            ).all():
                session.add(
                    BelegPositionSeriennummer(
                        position_id=neue_position.id,
                        seriennummer_id=zuordnung.seriennummer_id,
                    )
                )
    session.flush()

    # Vorgänger erledigen, wenn keine Restmengen mehr offen sind (bei voller
    # Übernahme immer). Sonst bleibt er gedruckt für weitere Teilübernahmen.
    if vorgaenger.status == BelegStatus.GEDRUCKT and (
        mengen is None or not hat_offene_restmengen(session, vorgaenger)
    ):
        setze_status(session, vorgaenger, BelegStatus.ERLEDIGT, benutzer_id=benutzer_id)

    aktualisiere_summen(session, neu)
    return neu


def _kopiere_beleg(  # noqa: PLR0913
    session: Session,
    vorlage: Beleg,
    belegnr: str,
    adresse_id: int,
    ist_muster: bool,
    benutzer_id: int | None,
) -> Beleg:
    """Frischer Entwurf als Kopie von ``vorlage`` (Kopf + alle Positionen).

    Die Kopie ist eine neue Vorlage: Status Entwurf, datum=heute, KEIN
    ``vorgaenger_beleg_id``, keine Druck-/Zahl-Zeitstempel, keine
    ``ursprungs_position_id`` und keine SN-Zuordnungen. Lieferadresse nur, wenn
    die Adresse gleich bleibt.
    """
    neu = Beleg(
        belegart=vorlage.belegart,
        belegnr=belegnr,
        status=BelegStatus.ENTWURF,
        ist_muster=ist_muster,
        adresse_id=adresse_id,
        datum=date.today(),
        zahlungsziel_tage=vorlage.zahlungsziel_tage,
        betreff=vorlage.betreff,
        auftragsart=vorlage.auftragsart,
        lieferadresse_id=vorlage.lieferadresse_id if adresse_id == vorlage.adresse_id else None,
        kopftext=vorlage.kopftext,
        fusstext=vorlage.fusstext,
        erstellt_von=benutzer_id,
    )
    session.add(neu)
    session.flush()

    for alte_position in _lade_positionen(session, vorlage):
        session.add(
            BelegPosition(
                beleg_id=neu.id,
                pos_nr=alte_position.pos_nr,
                artikel_id=alte_position.artikel_id,
                bezeichnung=alte_position.bezeichnung,
                langtext=alte_position.langtext,
                menge=alte_position.menge,
                einheit=alte_position.einheit,
                listenpreis=alte_position.listenpreis,
                rabatt1_prozent=alte_position.rabatt1_prozent,
                rabatt2_prozent=alte_position.rabatt2_prozent,
                ek_preis=alte_position.ek_preis,
                aufschlag_prozent=alte_position.aufschlag_prozent,
                einzelpreis=alte_position.einzelpreis,
                rabatt_prozent=alte_position.rabatt_prozent,
                ust_satz=alte_position.ust_satz,
            )
        )
    session.flush()
    aktualisiere_summen(session, neu)
    return neu


def dupliziere_beleg(
    session: Session, vorlage: Beleg, benutzer_id: int | None = None
) -> Beleg:
    """Legt einen neuen Entwurf derselben Belegart als Kopie/Vorlage an.

    Kopiert Kopftexte und ALLE Positionen (inkl. Kalkulations-, Preis- und
    Textfelder); neue Belegnummer, Status Entwurf — auch aus einem Muster
    (dann ist das Ergebnis ein normaler Beleg).
    """
    return _kopiere_beleg(
        session, vorlage, naechste_belegnr(session, vorlage.belegart),
        vorlage.adresse_id, ist_muster=False, benutzer_id=benutzer_id,
    )


# --------------------------------------------------------------- Kplus: Muster


def naechste_musternr(session: Session, belegart: Belegart) -> str:
    """Muster haben einen eigenen Zähler je Belegart (``M0001``), ohne Jahr."""
    return f"M{naechste_nummer(session, f'muster:{belegart}', 0):04d}"


def erstelle_muster(
    session: Session,
    belegart: Belegart,
    adresse: Adresse,
    benutzer_id: int | None = None,
    **kopf,
) -> Beleg:
    """Leeres Muster (Vorlage) einer Belegart anlegen."""
    kopf.setdefault("zahlungsziel_tage", adresse.zahlungsziel_tage)
    muster = Beleg(
        belegart=belegart,
        belegnr=naechste_musternr(session, belegart),
        status=BelegStatus.ENTWURF,
        ist_muster=True,
        adresse_id=adresse.id,
        erstellt_von=benutzer_id,
        **kopf,
    )
    session.add(muster)
    session.flush()
    return muster


def speichere_als_muster(
    session: Session, vorlage: Beleg, benutzer_id: int | None = None
) -> Beleg:
    """Kplus „Als Muster speichern": Kopie des Belegs als Vorlage (Muster-Nummer)."""
    return _kopiere_beleg(
        session, vorlage, naechste_musternr(session, vorlage.belegart),
        vorlage.adresse_id, ist_muster=True, benutzer_id=benutzer_id,
    )


def erzeuge_aus_muster(
    session: Session,
    muster: Beleg,
    adresse: Adresse | None = None,
    benutzer_id: int | None = None,
) -> Beleg:
    """Kplus „Beleg aus Muster": neuer Entwurf mit echter Belegnummer, wahlweise für
    eine andere Adresse (die Adressart muss zur Belegart passen)."""
    if not muster.ist_muster:
        raise WerkplusFehler(f"Beleg {muster.belegnr} ist kein Muster")
    ziel_adresse_id = muster.adresse_id
    if adresse is not None:
        pruefe_adressart(muster.belegart, adresse)
        ziel_adresse_id = adresse.id
    neu = _kopiere_beleg(
        session, muster, naechste_belegnr(session, muster.belegart),
        ziel_adresse_id, ist_muster=False, benutzer_id=benutzer_id,
    )
    if adresse is not None:
        neu.zahlungsziel_tage = adresse.zahlungsziel_tage
        session.add(neu)
        session.flush()
    return neu


# ------------------------------------------------- Kplus: Belegadresse tauschen


def pruefe_adressart(belegart: Belegart, adresse: Adresse) -> None:
    """Verkaufsbelege brauchen einen Kunden, Einkaufsbelege einen Lieferanten."""
    verkauf = belegart in VERKAUFSARTEN
    if verkauf and not adresse.ist_kunde:
        raise WerkplusFehler(f"{adresse.firma} ist kein Kunde")
    if not verkauf and not adresse.ist_lieferant:
        raise WerkplusFehler(f"{adresse.firma} ist kein Lieferant")


def wechsle_adresse(session: Session, beleg: Beleg, adresse: Adresse) -> None:
    """Kplus „Belegadresse tauschen": nur im Entwurf; Lieferadresse wird zurückgesetzt,
    Zahlungsziel kommt von der neuen Adresse."""
    pruefe_bearbeitbar(beleg)
    pruefe_adressart(beleg.belegart, adresse)
    if adresse.id == beleg.adresse_id:
        return
    beleg.adresse_id = adresse.id
    beleg.lieferadresse_id = None
    beleg.zahlungsziel_tage = adresse.zahlungsziel_tage
    session.add(beleg)
    session.flush()


# ------------------------------------------------------------ Entwurf löschen


def loesche_entwurf(session: Session, beleg: Beleg) -> None:
    """Löscht einen Beleg im Entwurf samt Positionen (Kplus „Beleg löschen").

    Nur Entwürfe: gebuchte Belege haben Lager- und Zahlungswirkung und werden
    storniert, nie gelöscht. Ein Entwurf kann keine Folgebelege haben
    (Folgebelege setzen „gedruckt" voraus). Zugeordnete Arbeitszeiten werden
    vom Beleg gelöst, nicht gelöscht — die Stunden sind ja geleistet.
    """
    pruefe_bearbeitbar(beleg)
    from app.models.zeiterfassung import Zeiterfassung  # zirkulären Import vermeiden

    for zeit in session.exec(
        select(Zeiterfassung).where(Zeiterfassung.beleg_id == beleg.id)
    ).all():
        zeit.beleg_id = None
        session.add(zeit)
    for position in _lade_positionen(session, beleg):
        for zuordnung in session.exec(
            select(BelegPositionSeriennummer).where(
                BelegPositionSeriennummer.position_id == position.id
            )
        ).all():
            session.delete(zuordnung)
        session.delete(position)
    session.delete(beleg)
    session.flush()


# ------------------------------------------------- Kplus: Angebot erhalten / nicht


def angebot_ist_offen(beleg: Beleg) -> bool:
    """Angebot, das noch „erhalten"/„nicht erhalten" werden kann (UI-Anzeige)."""
    return (
        beleg.belegart == Belegart.ANGEBOT
        and not beleg.ist_muster
        and beleg.status in (BelegStatus.ENTWURF, BelegStatus.GEDRUCKT)
        and not beleg.angebotsergebnis
    )


def _pruefe_angebot_offen(angebot: Beleg) -> None:
    if angebot.belegart != Belegart.ANGEBOT:
        raise UngueltigeBelegart(
            f"'Auftrag erhalten' gibt es nur für Angebote, nicht für {angebot.belegart}"
        )
    # Offen = Entwurf oder gedruckt ohne Ergebnis. Ein erledigtes Angebot ohne
    # Ergebnis (Altbestand vor dieser Funktion) hat seinen Auftrag bereits.
    if not angebot_ist_offen(angebot):
        raise UngueltigerStatusUebergang(
            f"Angebot {angebot.belegnr} ist bereits abgeschlossen "
            f"({angebot.angebotsergebnis or angebot.status})"
        )


def angebot_erhalten(
    session: Session,
    angebot: Beleg,
    benutzer_id: int | None = None,
    auftragsart: str = "",
    termin: date | None = None,
) -> Beleg:
    """Kplus „Auftrag erhalten": erzeugt den Auftrag als Folgebeleg und erledigt das Angebot.

    Darf auch aus dem Entwurf heraus (der Kunde sagt am Telefon zu, bevor das
    Angebot formal gedruckt wurde): dann wird das Angebot zuerst gebucht
    (Summen einfrieren), damit die Folgebeleg-Logik greift. Alle Positionen
    wandern vollständig in den Auftrag; der Auftrag bekommt die gewählte
    Auftragsart und den Termin.
    """
    _pruefe_angebot_offen(angebot)
    if angebot.status == BelegStatus.ENTWURF:
        setze_status(session, angebot, BelegStatus.GEDRUCKT, benutzer_id=benutzer_id)
    auftrag = erzeuge_folgebeleg(session, angebot, benutzer_id=benutzer_id)
    auftrag.auftragsart = auftragsart
    auftrag.termin = termin
    angebot.angebotsergebnis = Angebotsergebnis.ERHALTEN
    session.add(auftrag)
    session.add(angebot)
    session.flush()
    return auftrag


def angebot_nicht_erhalten(session: Session, angebot: Beleg) -> None:
    """Kplus „Auftrag nicht erhalten": Angebot als verloren erledigen.

    Angebote haben keine Lagerwirkung, deshalb wird der Status hier direkt
    gesetzt — auch aus dem Entwurf heraus (die Statusmaschine kennt keinen
    Weg Entwurf→Erledigt, weil der für buchende Belegarten falsch wäre).
    """
    _pruefe_angebot_offen(angebot)
    angebot.angebotsergebnis = Angebotsergebnis.NICHT_ERHALTEN
    angebot.status = BelegStatus.ERLEDIGT
    session.add(angebot)
    session.flush()


# ------------------------------------------------------ Kplus: Abschlag/Sammelrechnung


def _haeufigster_ust_satz(session: Session, beleg: Beleg) -> int:
    """Häufigster USt-Satz der Positionen eines Belegs; 20 %, wenn keine Position."""
    saetze = [p.ust_satz for p in _lade_positionen(session, beleg)]
    if not saetze:
        return 20
    return Counter(saetze).most_common(1)[0][0]


def _fmt_prozent(prozent: Decimal) -> str:
    """Prozentwert ohne unnötige Nachkommastellen (20.00 → '20', 12.5 → '12,5')."""
    text = f"{prozent.normalize():f}"
    return text.replace(".", ",")


def _kopiere_position_voll(
    session: Session, ziel: Beleg, alte: BelegPosition, pos_nr: int
) -> BelegPosition:
    """Kopiert eine Position vollständig (inkl. Kalkulation + SN-Zuordnungen) in ``ziel``."""
    neue = BelegPosition(
        beleg_id=ziel.id,
        pos_nr=pos_nr,
        artikel_id=alte.artikel_id,
        bezeichnung=alte.bezeichnung,
        langtext=alte.langtext,
        menge=alte.menge,
        einheit=alte.einheit,
        listenpreis=alte.listenpreis,
        rabatt1_prozent=alte.rabatt1_prozent,
        rabatt2_prozent=alte.rabatt2_prozent,
        ek_preis=alte.ek_preis,
        aufschlag_prozent=alte.aufschlag_prozent,
        einzelpreis=alte.einzelpreis,
        rabatt_prozent=alte.rabatt_prozent,
        ust_satz=alte.ust_satz,
        ursprungs_position_id=alte.id,
    )
    session.add(neue)
    session.flush()
    for zuordnung in session.exec(
        select(BelegPositionSeriennummer).where(
            BelegPositionSeriennummer.position_id == alte.id
        )
    ).all():
        session.add(
            BelegPositionSeriennummer(
                position_id=neue.id, seriennummer_id=zuordnung.seriennummer_id
            )
        )
    session.flush()
    return neue


def _abschlagsrechnungen(session: Session, auftrag: Beleg) -> list[Beleg]:
    """Alle nicht stornierten Abschlagsrechnungen eines Auftrags (aufsteigend)."""
    return list(
        session.exec(
            select(Beleg)
            .where(Beleg.ist_abschlag == True)  # noqa: E712
            .where(Beleg.auftrag_id == auftrag.id)
            .where(Beleg.status != BelegStatus.STORNIERT)
            .order_by(Beleg.id)  # type: ignore[arg-type]
        ).all()
    )


def bereits_abgerechnete_abschlaege(session: Session, auftrag: Beleg) -> Decimal:
    """Netto-Summe aller (nicht stornierten) Abschlagsrechnungen des Auftrags."""
    return sum(
        (a.summe_netto for a in _abschlagsrechnungen(session, auftrag)), Decimal("0")
    )


def _pruefe_auftrag(auftrag: Beleg) -> None:
    """Stellt sicher, dass ``auftrag`` ein gedruckter/erledigter Auftrag ist."""
    if auftrag.belegart != Belegart.AUFTRAG:
        raise UngueltigeBelegart(
            f"Abschlags-/Schlussrechnung nur aus einem Auftrag, "
            f"Beleg {auftrag.belegnr} ist {auftrag.belegart}"
        )
    if auftrag.status not in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT):
        raise UngueltigerStatusUebergang(
            f"Auftrag {auftrag.belegnr} ist {auftrag.status}, "
            f"nur aus gedruckt/erledigt möglich"
        )


def erzeuge_abschlagsrechnung(
    session: Session,
    auftrag: Beleg,
    *,
    prozent: Decimal | None = None,
    betrag: Decimal | None = None,
    benutzer_id: int | None = None,
) -> Beleg:
    """Erzeugt eine Abschlags-/Anzahlungsrechnung (RECHNUNG, ``ist_abschlag=True``).

    Der Abschlagsbetrag ergibt sich entweder aus ``prozent`` der Auftrags-
    Nettosumme (``auftrag.summe_netto``) oder direkt aus ``betrag`` (netto).
    Die Rechnung enthält genau eine Freitext-Position mit dem USt-Satz, der im
    Auftrag am häufigsten vorkommt (Fallback 20 %). Nur aus einem gedruckten
    oder erledigten Auftrag.
    """
    _pruefe_auftrag(auftrag)
    if prozent is None and betrag is None:
        raise WerkplusFehler("Abschlag braucht einen Prozentsatz oder einen Betrag")

    ust_satz = _haeufigster_ust_satz(session, auftrag)
    if prozent is not None:
        netto = berechnung.runde(auftrag.summe_netto * prozent / Decimal("100"))
        bezeichnung = f"Abschlag {_fmt_prozent(prozent)}% zu Auftrag {auftrag.belegnr}"
    else:
        netto = berechnung.runde(betrag)
        bezeichnung = f"Abschlag zu Auftrag {auftrag.belegnr}"

    neu = Beleg(
        belegart=Belegart.RECHNUNG,
        belegnr=naechste_belegnr(session, Belegart.RECHNUNG),
        status=BelegStatus.ENTWURF,
        adresse_id=auftrag.adresse_id,
        datum=date.today(),
        zahlungsziel_tage=auftrag.zahlungsziel_tage,
        ist_abschlag=True,
        auftrag_id=auftrag.id,
        erstellt_von=benutzer_id,
    )
    session.add(neu)
    session.flush()
    session.add(
        BelegPosition(
            beleg_id=neu.id,
            pos_nr=1,
            bezeichnung=bezeichnung,
            menge=Decimal("1"),
            einheit="Psch",
            einzelpreis=netto,
            rabatt_prozent=Decimal("0"),
            ust_satz=ust_satz,
        )
    )
    session.flush()
    aktualisiere_summen(session, neu)
    return neu


def erzeuge_schlussrechnung(
    session: Session, auftrag: Beleg, benutzer_id: int | None = None
) -> Beleg:
    """Erzeugt die Schlussrechnung eines Auftrags mit Abzug der Abschläge.

    Kopiert die vollen Auftragspositionen in eine neue RECHNUNG (``auftrag_id``
    gesetzt) und hängt je bestehender Abschlagsrechnung eine negative
    Abzugsposition an (gleicher USt-Satz wie der Abschlag). Die Belegsumme ist
    damit die Auftrags-Nettosumme minus der bereits abgerechneten Abschläge.
    Nur aus einem gedruckten oder erledigten Auftrag.
    """
    _pruefe_auftrag(auftrag)

    neu = Beleg(
        belegart=Belegart.RECHNUNG,
        belegnr=naechste_belegnr(session, Belegart.RECHNUNG),
        status=BelegStatus.ENTWURF,
        adresse_id=auftrag.adresse_id,
        datum=date.today(),
        vorgaenger_beleg_id=auftrag.id,
        auftrag_id=auftrag.id,
        zahlungsziel_tage=auftrag.zahlungsziel_tage,
        kopftext=auftrag.kopftext,
        fusstext=auftrag.fusstext,
        erstellt_von=benutzer_id,
    )
    session.add(neu)
    session.flush()

    pos_nr = 0
    for alte in _lade_positionen(session, auftrag):
        pos_nr += 1
        _kopiere_position_voll(session, neu, alte, pos_nr)

    for abschlag in _abschlagsrechnungen(session, auftrag):
        pos_nr += 1
        abschlag_positionen = _lade_positionen(session, abschlag)
        ust_satz = (
            abschlag_positionen[0].ust_satz
            if abschlag_positionen
            else _haeufigster_ust_satz(session, auftrag)
        )
        session.add(
            BelegPosition(
                beleg_id=neu.id,
                pos_nr=pos_nr,
                bezeichnung=(
                    f"Abzug Abschlagsrechnung {abschlag.belegnr} "
                    f"vom {abschlag.datum.strftime('%d.%m.%Y')}"
                ),
                menge=Decimal("1"),
                einheit="Psch",
                einzelpreis=-abschlag.summe_netto,
                rabatt_prozent=Decimal("0"),
                ust_satz=ust_satz,
            )
        )
    session.flush()
    aktualisiere_summen(session, neu)
    return neu


def erzeuge_sammelrechnung(
    session: Session, lieferscheine: list[Beleg], benutzer_id: int | None = None
) -> Beleg:
    """Fasst mehrere Lieferscheine desselben Kunden zu einer Rechnung zusammen.

    Alle ``lieferscheine`` müssen Belegart LIEFERSCHEIN, Status gedruckt oder
    erledigt und dieselbe Adresse haben. Die Positionen aller Lieferscheine
    werden (mit fortlaufender pos_nr, Kalkulations-/Preisfeldern und
    SN-Zuordnungen) in eine neue RECHNUNG kopiert; die Lieferscheine werden auf
    erledigt gesetzt.
    """
    if not lieferscheine:
        raise WerkplusFehler("Keine Lieferscheine zur Sammelrechnung gewählt")
    erste = lieferscheine[0]
    for ls in lieferscheine:
        if ls.belegart != Belegart.LIEFERSCHEIN:
            raise UngueltigeBelegart(f"Beleg {ls.belegnr} ist kein Lieferschein")
        if ls.status not in (BelegStatus.GEDRUCKT, BelegStatus.ERLEDIGT):
            raise UngueltigerStatusUebergang(
                f"Lieferschein {ls.belegnr} ist {ls.status}, nicht sammelbar"
            )
        if ls.adresse_id != erste.adresse_id:
            raise WerkplusFehler(
                "Sammelrechnung nur für Lieferscheine desselben Kunden möglich"
            )

    daten = [ls.datum for ls in lieferscheine]
    neu = Beleg(
        belegart=Belegart.RECHNUNG,
        belegnr=naechste_belegnr(session, Belegart.RECHNUNG),
        status=BelegStatus.ENTWURF,
        adresse_id=erste.adresse_id,
        datum=date.today(),
        vorgaenger_beleg_id=erste.id,
        zahlungsziel_tage=erste.zahlungsziel_tage,
        leistungsdatum_von=min(daten),
        leistungsdatum_bis=max(daten),
        erstellt_von=benutzer_id,
    )
    session.add(neu)
    session.flush()

    pos_nr = 0
    for ls in lieferscheine:
        for alte in _lade_positionen(session, ls):
            pos_nr += 1
            _kopiere_position_voll(session, neu, alte, pos_nr)

    for ls in lieferscheine:
        if ls.status == BelegStatus.GEDRUCKT:
            setze_status(session, ls, BelegStatus.ERLEDIGT, benutzer_id=benutzer_id)

    aktualisiere_summen(session, neu)
    return neu
