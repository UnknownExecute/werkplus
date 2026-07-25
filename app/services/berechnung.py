"""Reine Rechenfunktionen des Belegwesens — keine Datenbank, keine Seiteneffekte.

Rundungsregel: kaufmännisch (ROUND_HALF_UP) auf 2 Nachkommastellen.
Die Umsatzsteuer wird je Steuersatz auf die Netto-SUMME berechnet (nicht je
Position) und dann gerundet — übliche Praxis nach § 11 UStG.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.models.beleg import BelegPosition

_CENT = Decimal("0.01")
_HUNDERT = Decimal("100")


def runde(betrag: Decimal) -> Decimal:
    """Kaufmännische Rundung auf 2 Nachkommastellen (0.005 → 0.01)."""
    return betrag.quantize(_CENT, rounding=ROUND_HALF_UP)


def positions_netto(menge: Decimal, einzelpreis: Decimal, rabatt_prozent: Decimal) -> Decimal:
    """Netto einer Position: menge * einzelpreis * (1 - rabatt/100), gerundet."""
    return runde(menge * einzelpreis * (Decimal("1") - rabatt_prozent / _HUNDERT))


# ---------------------------------------------------------------- Kalkulation
# Kette wie Kplus: Listenpreis − Rabatt1 − Rabatt2 = EK-Preis; EK + Aufschlag % = VK.


def kalkuliere_ek(
    listenpreis_netto: Decimal, rabatt1_prozent: Decimal, rabatt2_prozent: Decimal
) -> Decimal:
    """EK aus Netto-Listenpreis nach zwei aufeinanderfolgenden Rabatten, gerundet."""
    faktor = (Decimal("1") - rabatt1_prozent / _HUNDERT) * (
        Decimal("1") - rabatt2_prozent / _HUNDERT
    )
    return runde(listenpreis_netto * faktor)


def kalkuliere_vk(ek_preis: Decimal, aufschlag_prozent: Decimal) -> Decimal:
    """VK aus EK-Preis zuzüglich Aufschlag, gerundet."""
    return runde(ek_preis * (Decimal("1") + aufschlag_prozent / _HUNDERT))


def aufschlag_aus_vk(ek_preis: Decimal, vk_preis: Decimal) -> Decimal:
    """Rückrechnung des Aufschlags aus manuell gesetztem VK; 0 %, wenn EK = 0."""
    if ek_preis == 0:
        return Decimal("0.00")
    return runde((vk_preis / ek_preis - Decimal("1")) * _HUNDERT)


def brutto_aus_netto(netto: Decimal, ust_satz: int) -> Decimal:
    """Bruttopreis aus Nettopreis und USt-Satz, gerundet."""
    return runde(netto * (Decimal("1") + Decimal(ust_satz) / _HUNDERT))


def netto_aus_brutto(brutto: Decimal, ust_satz: int) -> Decimal:
    """Nettopreis aus Bruttopreis und USt-Satz, gerundet."""
    return runde(brutto / (Decimal("1") + Decimal(ust_satz) / _HUNDERT))


@dataclass(frozen=True)
class SteuersatzSumme:
    netto: Decimal
    ust: Decimal


@dataclass(frozen=True)
class BelegSummen:
    netto_gesamt: Decimal = Decimal("0.00")
    ust_gesamt: Decimal = Decimal("0.00")
    brutto_gesamt: Decimal = Decimal("0.00")
    je_steuersatz: dict[int, SteuersatzSumme] = field(default_factory=dict)


def beleg_summen(positionen: Iterable[BelegPosition]) -> BelegSummen:
    """Summiert Positionen zu Netto/USt/Brutto, aufgeschlüsselt je Steuersatz.

    Die Positions-Netti werden je Steuersatz aufsummiert; die USt wird auf
    diese Summe berechnet und einmal gerundet.
    """
    netto_je_satz: dict[int, Decimal] = {}
    for pos in positionen:
        netto = positions_netto(pos.menge, pos.einzelpreis, pos.rabatt_prozent)
        netto_je_satz[pos.ust_satz] = netto_je_satz.get(pos.ust_satz, Decimal("0")) + netto

    je_steuersatz: dict[int, SteuersatzSumme] = {}
    netto_gesamt = Decimal("0.00")
    ust_gesamt = Decimal("0.00")
    for satz in sorted(netto_je_satz):
        netto = runde(netto_je_satz[satz])
        ust = runde(netto * satz / _HUNDERT)
        je_steuersatz[satz] = SteuersatzSumme(netto=netto, ust=ust)
        netto_gesamt += netto
        ust_gesamt += ust

    return BelegSummen(
        netto_gesamt=runde(netto_gesamt),
        ust_gesamt=runde(ust_gesamt),
        brutto_gesamt=runde(netto_gesamt + ust_gesamt),
        je_steuersatz=je_steuersatz,
    )


@dataclass(frozen=True)
class BelegMarge:
    """Deckungsbeitrag/Marge eines Belegs (nur Verkaufsbelege, nicht im PDF)."""

    ek_gesamt: Decimal = Decimal("0.00")
    vk_netto_gesamt: Decimal = Decimal("0.00")
    deckungsbeitrag: Decimal = Decimal("0.00")
    marge_prozent: Decimal = Decimal("0.00")


def beleg_marge(positionen: Iterable[BelegPosition]) -> BelegMarge:
    """Ermittelt EK-Summe, Deckungsbeitrag und Marge % über alle Positionen.

    EK-Gesamt = Summe(menge * ek_preis) je Position gerundet; VK-Netto = Summe
    der Positions-Netti; Deckungsbeitrag = VK − EK; Marge % = DB / VK * 100
    (0, wenn VK 0).
    """
    ek_gesamt = Decimal("0.00")
    vk_netto_gesamt = Decimal("0.00")
    for pos in positionen:
        ek_gesamt += runde(pos.menge * pos.ek_preis)
        vk_netto_gesamt += positions_netto(pos.menge, pos.einzelpreis, pos.rabatt_prozent)
    ek_gesamt = runde(ek_gesamt)
    vk_netto_gesamt = runde(vk_netto_gesamt)
    deckungsbeitrag = vk_netto_gesamt - ek_gesamt
    if vk_netto_gesamt == 0:
        marge_prozent = Decimal("0.00")
    else:
        marge_prozent = runde(deckungsbeitrag / vk_netto_gesamt * _HUNDERT)
    return BelegMarge(
        ek_gesamt=ek_gesamt,
        vk_netto_gesamt=vk_netto_gesamt,
        deckungsbeitrag=deckungsbeitrag,
        marge_prozent=marge_prozent,
    )


def steuer_details_json(summen: BelegSummen) -> str:
    """Serialisiert die Steuersatz-Aufschlüsselung für das Beleg-Feld ``steuer_details``."""
    return json.dumps(
        {
            str(satz): {"netto": str(s.netto), "ust": str(s.ust)}
            for satz, s in sorted(summen.je_steuersatz.items())
        }
    )


def parse_steuer_details(text: str) -> dict[int, SteuersatzSumme]:
    """Liest das JSON aus ``Beleg.steuer_details`` zurück in Decimal-Werte."""
    roh = json.loads(text or "{}")
    return {
        int(satz): SteuersatzSumme(netto=Decimal(werte["netto"]), ust=Decimal(werte["ust"]))
        for satz, werte in roh.items()
    }
