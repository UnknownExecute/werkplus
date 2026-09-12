"""Tests des Beleg-Services: Anlage, Positionen, Statusmaschine, Folgebelege."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.adresse import Adresse
from app.models.artikel import Artikel
from app.models.beleg import (
    Belegart,
    BelegPosition,
    BelegPositionSeriennummer,
    BelegStatus,
)
from app.models.seriennummer import Seriennummer
from app.services import beleg_service
from app.services.beleg_service import (
    aktualisiere_summen,
    bereits_abgerechnete_abschlaege,
    bereits_uebernommen,
    entferne_position,
    erstelle_beleg,
    erzeuge_abschlagsrechnung,
    erzeuge_folgebeleg,
    erzeuge_sammelrechnung,
    erzeuge_schlussrechnung,
    fuege_position_hinzu,
    hat_offene_restmengen,
    registriere_buchungs_hook,
    registriere_storno_hook,
    restmenge,
    setze_status,
    verschiebe_position,
)
from app.services.berechnung import parse_steuer_details
from app.services.fehler import (
    BelegGesperrt,
    UngueltigeBelegart,
    UngueltigerStatusUebergang,
    WerkplusFehler,
)


@pytest.fixture
def adresse(session) -> Adresse:
    adresse = Adresse(
        firma="Muster GmbH",
        zahlungsziel_tage=30,
        standard_rabatt_prozent=Decimal("5"),
    )
    session.add(adresse)
    session.flush()
    return adresse


@pytest.fixture
def artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="A-100",
        bezeichnung="Wärmepumpe WP-8",
        langtext="Luft/Wasser-Wärmepumpe 8 kW",
        einheit="Stk",
        ek_preis=Decimal("60.00"),
        vk_preis=Decimal("100.00"),
        ust_satz=20,
    )
    session.add(artikel)
    session.flush()
    return artikel


@pytest.fixture(autouse=True)
def _hooks_isolieren():
    """Modul-globale Hook-Registries zwischen Tests zurücksetzen."""
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


class TestErstelleBeleg:
    def test_nummer_status_zahlungsziel(self, session, adresse):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse, benutzer_id=None)
        jahr = date.today().year
        assert beleg.belegnr == f"{jahr % 100:02d}0001"
        assert beleg.status == BelegStatus.ENTWURF
        assert beleg.zahlungsziel_tage == 30  # von der Adresse übernommen
        assert beleg.adresse_id == adresse.id
        assert beleg.id is not None  # geflusht

    def test_kopffelder_ueberschreibbar(self, session, adresse):
        beleg = erstelle_beleg(
            session, Belegart.RECHNUNG, adresse, kopftext="Hallo", zahlungsziel_tage=7
        )
        assert beleg.kopftext == "Hallo"
        assert beleg.zahlungsziel_tage == 7


class TestPositionen:
    def test_defaults_vk_preis_und_kundenrabatt(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        position = fuege_position_hinzu(session, beleg, artikel, menge=Decimal("2"))
        assert position.einzelpreis == Decimal("100.00")  # VK-Preis
        assert position.rabatt_prozent == Decimal("5")  # Kundenrabatt
        assert position.bezeichnung == "Wärmepumpe WP-8"
        assert position.ust_satz == 20
        assert position.pos_nr == 1
        # Summen: 2 * 100 * 0.95 = 190.00 netto, 38.00 USt
        assert beleg.summe_netto == Decimal("190.00")
        assert beleg.summe_ust == Decimal("38.00")
        assert beleg.summe_brutto == Decimal("228.00")
        details = parse_steuer_details(beleg.steuer_details)
        assert details[20].netto == Decimal("190.00")

    def test_freitext_und_fortlaufende_pos_nr(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        fuege_position_hinzu(session, beleg, artikel)
        position = fuege_position_hinzu(
            session,
            beleg,
            bezeichnung="Montagepauschale",
            menge=Decimal("1"),
            einzelpreis=Decimal("250.00"),
            rabatt_prozent=Decimal("0"),
            ust_satz=20,
        )
        assert position.pos_nr == 2
        assert position.artikel_id is None
        # 1*100*0.95 + 250 = 345.00
        assert beleg.summe_netto == Decimal("345.00")

    def test_entferne_position(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        position = fuege_position_hinzu(session, beleg, artikel, rabatt_prozent=Decimal("0"))
        entferne_position(session, beleg, position)
        assert beleg.summe_netto == Decimal("0.00")
        rest = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg.id)
        ).all()
        assert rest == []


class TestVerschiebePosition:
    def _drei_positionen(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        p1 = fuege_position_hinzu(session, beleg, artikel, bezeichnung="A")
        p2 = fuege_position_hinzu(session, beleg, None, bezeichnung="B")
        p3 = fuege_position_hinzu(session, beleg, None, bezeichnung="C")
        assert (p1.pos_nr, p2.pos_nr, p3.pos_nr) == (1, 2, 3)
        return beleg, p1, p2, p3

    def test_runter_tauscht_pos_nr(self, session, adresse, artikel):
        beleg, p1, p2, _ = self._drei_positionen(session, adresse, artikel)
        verschiebe_position(session, beleg, p1, "runter")
        assert p1.pos_nr == 2
        assert p2.pos_nr == 1

    def test_hoch_tauscht_pos_nr(self, session, adresse, artikel):
        beleg, _, p2, p3 = self._drei_positionen(session, adresse, artikel)
        verschiebe_position(session, beleg, p3, "hoch")
        assert p3.pos_nr == 2
        assert p2.pos_nr == 3

    def test_noop_am_oberen_rand(self, session, adresse, artikel):
        beleg, p1, p2, p3 = self._drei_positionen(session, adresse, artikel)
        verschiebe_position(session, beleg, p1, "hoch")
        assert (p1.pos_nr, p2.pos_nr, p3.pos_nr) == (1, 2, 3)

    def test_noop_am_unteren_rand(self, session, adresse, artikel):
        beleg, p1, p2, p3 = self._drei_positionen(session, adresse, artikel)
        verschiebe_position(session, beleg, p3, "runter")
        assert (p1.pos_nr, p2.pos_nr, p3.pos_nr) == (1, 2, 3)

    def test_gesperrt_bei_gedruckt(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.RECHNUNG, adresse)
        position = fuege_position_hinzu(session, beleg, artikel)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        with pytest.raises(BelegGesperrt):
            verschiebe_position(session, beleg, position, "hoch")


class TestStatusmaschine:
    def test_gueltige_uebergaenge(self, session, adresse):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        assert beleg.status == BelegStatus.GEDRUCKT
        assert beleg.gedruckt_am is not None
        setze_status(session, beleg, BelegStatus.ERLEDIGT)
        assert beleg.status == BelegStatus.ERLEDIGT

    def test_entwurf_stornierbar(self, session, adresse):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        setze_status(session, beleg, BelegStatus.STORNIERT)
        assert beleg.status == BelegStatus.STORNIERT

    @pytest.mark.parametrize(
        ("start", "ziel"),
        [
            (BelegStatus.ENTWURF, BelegStatus.ERLEDIGT),
            (BelegStatus.GEDRUCKT, BelegStatus.GEDRUCKT),
            (BelegStatus.ERLEDIGT, BelegStatus.STORNIERT),
            (BelegStatus.ERLEDIGT, BelegStatus.ENTWURF),
            (BelegStatus.STORNIERT, BelegStatus.ENTWURF),
            (BelegStatus.STORNIERT, BelegStatus.GEDRUCKT),
        ],
    )
    def test_ungueltige_uebergaenge(self, session, adresse, start, ziel):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        beleg.status = start
        with pytest.raises(UngueltigerStatusUebergang):
            setze_status(session, beleg, ziel)

    def test_druck_friert_summen_ein(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.RECHNUNG, adresse)
        fuege_position_hinzu(session, beleg, artikel, rabatt_prozent=Decimal("0"))
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        assert beleg.summe_netto == Decimal("100.00")
        assert beleg.summe_brutto == Decimal("120.00")

    def test_hooks_werden_aufgerufen(self, session, adresse):
        aufrufe: list[tuple[str, str]] = []
        registriere_buchungs_hook(
            Belegart.LIEFERSCHEIN,
            lambda s, b, uid: aufrufe.append(("buchung", b.belegnr)),
        )
        registriere_storno_hook(
            Belegart.LIEFERSCHEIN,
            lambda s, b, uid: aufrufe.append(("storno", b.belegnr)),
        )
        beleg = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        setze_status(session, beleg, BelegStatus.STORNIERT)
        assert aufrufe == [("buchung", beleg.belegnr), ("storno", beleg.belegnr)]


class TestRechnungsUnveraenderlichkeit:
    def test_gedruckte_rechnung_gesperrt(self, session, adresse, artikel):
        rechnung = erstelle_beleg(session, Belegart.RECHNUNG, adresse)
        position = fuege_position_hinzu(session, rechnung, artikel)
        setze_status(session, rechnung, BelegStatus.GEDRUCKT)
        with pytest.raises(BelegGesperrt):
            fuege_position_hinzu(session, rechnung, artikel)
        with pytest.raises(BelegGesperrt):
            entferne_position(session, rechnung, position)
        with pytest.raises(UngueltigerStatusUebergang):
            setze_status(session, rechnung, BelegStatus.ENTWURF)


class TestFolgebeleg:
    def _gedruckter_beleg(self, session, adresse, artikel, belegart=Belegart.ANGEBOT):
        beleg = erstelle_beleg(session, belegart, adresse, kopftext="K", fusstext="F")
        fuege_position_hinzu(session, beleg, artikel, menge=Decimal("2"))
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        return beleg

    def test_kette_angebot_bis_rechnung(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel)

        auftrag = erzeuge_folgebeleg(session, angebot)
        assert auftrag.belegart == Belegart.AUFTRAG
        assert auftrag.status == BelegStatus.ENTWURF
        assert auftrag.vorgaenger_beleg_id == angebot.id
        assert auftrag.kopftext == "K"
        assert auftrag.fusstext == "F"
        assert auftrag.zahlungsziel_tage == angebot.zahlungsziel_tage
        assert angebot.status == BelegStatus.ERLEDIGT  # Vorgänger erledigt
        assert auftrag.summe_netto == angebot.summe_netto

        # Positionsreferenzen gesetzt
        angebot_pos = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == angebot.id)
        ).one()
        auftrag_pos = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == auftrag.id)
        ).one()
        assert auftrag_pos.ursprungs_position_id == angebot_pos.id
        assert auftrag_pos.menge == angebot_pos.menge
        assert auftrag_pos.einzelpreis == angebot_pos.einzelpreis

        setze_status(session, auftrag, BelegStatus.GEDRUCKT)
        lieferschein = erzeuge_folgebeleg(session, auftrag)
        assert lieferschein.belegart == Belegart.LIEFERSCHEIN
        assert auftrag.status == BelegStatus.ERLEDIGT

        setze_status(session, lieferschein, BelegStatus.GEDRUCKT)
        rechnung = erzeuge_folgebeleg(session, lieferschein)
        assert rechnung.belegart == Belegart.RECHNUNG
        assert lieferschein.status == BelegStatus.ERLEDIGT
        # Leistungsdatum = Datum des Lieferscheins
        assert rechnung.leistungsdatum_von == lieferschein.datum
        assert rechnung.leistungsdatum_bis == lieferschein.datum

    def test_seriennummern_wandern_vom_lieferschein_zur_rechnung(
        self, session, adresse, artikel
    ):
        lieferschein = self._gedruckter_beleg(
            session, adresse, artikel, belegart=Belegart.LIEFERSCHEIN
        )
        ls_position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == lieferschein.id)
        ).one()
        sn = Seriennummer(artikel_id=artikel.id, sn="SN-0001")
        session.add(sn)
        session.flush()
        session.add(
            BelegPositionSeriennummer(position_id=ls_position.id, seriennummer_id=sn.id)
        )
        session.flush()

        rechnung = erzeuge_folgebeleg(session, lieferschein)
        re_position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == rechnung.id)
        ).one()
        zuordnungen = session.exec(
            select(BelegPositionSeriennummer).where(
                BelegPositionSeriennummer.position_id == re_position.id
            )
        ).all()
        assert [z.seriennummer_id for z in zuordnungen] == [sn.id]

    def test_gutschrift_aus_gedruckter_rechnung(self, session, adresse, artikel):
        rechnung = self._gedruckter_beleg(session, adresse, artikel, belegart=Belegart.RECHNUNG)
        gutschrift = erzeuge_folgebeleg(session, rechnung)
        assert gutschrift.belegart == Belegart.GUTSCHRIFT
        assert rechnung.status == BelegStatus.ERLEDIGT

    def test_gutschrift_aus_erledigter_rechnung(self, session, adresse, artikel):
        rechnung = self._gedruckter_beleg(session, adresse, artikel, belegart=Belegart.RECHNUNG)
        setze_status(session, rechnung, BelegStatus.ERLEDIGT)
        gutschrift = erzeuge_folgebeleg(session, rechnung)
        assert gutschrift.belegart == Belegart.GUTSCHRIFT
        assert rechnung.status == BelegStatus.ERLEDIGT  # bleibt erledigt

    def test_einkaufskette(self, session, adresse, artikel):
        bestellung = self._gedruckter_beleg(
            session, adresse, artikel, belegart=Belegart.BESTELLUNG
        )
        we = erzeuge_folgebeleg(session, bestellung)
        assert we.belegart == Belegart.EK_LIEFERSCHEIN
        setze_status(session, we, BelegStatus.GEDRUCKT)
        er = erzeuge_folgebeleg(session, we)
        assert er.belegart == Belegart.EK_RECHNUNG

    def test_folgebeleg_aus_entwurf_verboten(self, session, adresse, artikel):
        angebot = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        with pytest.raises(UngueltigerStatusUebergang):
            erzeuge_folgebeleg(session, angebot)

    def test_folgebeleg_aus_storniert_verboten(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel)
        setze_status(session, angebot, BelegStatus.STORNIERT)
        with pytest.raises(UngueltigerStatusUebergang):
            erzeuge_folgebeleg(session, angebot)

    def test_ungueltige_kette_verboten(self, session, adresse, artikel):
        # Angebot -> Rechnung direkt gibt es nicht: der Folgebeleg eines
        # Angebots ist IMMER der Auftrag; Gutschrift/EK-Rechnung sind Endpunkte.
        angebot = self._gedruckter_beleg(session, adresse, artikel)
        folge = erzeuge_folgebeleg(session, angebot)
        assert folge.belegart == Belegart.AUFTRAG
        assert folge.belegart != Belegart.RECHNUNG

        gutschrift = self._gedruckter_beleg(
            session, adresse, artikel, belegart=Belegart.GUTSCHRIFT
        )
        with pytest.raises(UngueltigeBelegart):
            erzeuge_folgebeleg(session, gutschrift)

        ek_rechnung = self._gedruckter_beleg(
            session, adresse, artikel, belegart=Belegart.EK_RECHNUNG
        )
        with pytest.raises(UngueltigeBelegart):
            erzeuge_folgebeleg(session, ek_rechnung)


class TestTeilFolgebeleg:
    def _gedruckter_beleg(self, session, adresse, artikel, menge=Decimal("2")):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse, kopftext="K")
        fuege_position_hinzu(session, beleg, artikel, menge=menge)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        return beleg

    def _pos(self, session, beleg) -> BelegPosition:
        return session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg.id)
        ).one()

    def test_teilmenge_vorgaenger_bleibt_gedruckt(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel, menge=Decimal("2"))
        pos = self._pos(session, angebot)

        auftrag = erzeuge_folgebeleg(session, angebot, mengen={pos.id: Decimal("1")})
        auftrag_pos = self._pos(session, auftrag)
        assert auftrag_pos.menge == Decimal("1")
        assert auftrag_pos.ursprungs_position_id == pos.id
        # Vorgänger bleibt offen, Restmenge halbiert
        assert angebot.status == BelegStatus.GEDRUCKT
        assert bereits_uebernommen(session, pos) == Decimal("1")
        assert restmenge(session, pos) == Decimal("1")
        assert hat_offene_restmengen(session, angebot) is True

    def test_zweite_teilmenge_erledigt_vorgaenger(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel, menge=Decimal("2"))
        pos = self._pos(session, angebot)

        erzeuge_folgebeleg(session, angebot, mengen={pos.id: Decimal("1")})
        assert angebot.status == BelegStatus.GEDRUCKT

        erzeuge_folgebeleg(session, angebot, mengen={pos.id: Decimal("1")})
        assert restmenge(session, pos) == Decimal("0")
        assert hat_offene_restmengen(session, angebot) is False
        assert angebot.status == BelegStatus.ERLEDIGT

    def test_mengen_none_bleibt_volle_uebernahme(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel, menge=Decimal("2"))
        pos = self._pos(session, angebot)

        auftrag = erzeuge_folgebeleg(session, angebot)
        assert self._pos(session, auftrag).menge == Decimal("2")
        assert angebot.status == BelegStatus.ERLEDIGT
        assert restmenge(session, pos) == Decimal("0")

    def test_clamping_bei_menge_groesser_rest(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel, menge=Decimal("2"))
        pos = self._pos(session, angebot)

        auftrag = erzeuge_folgebeleg(session, angebot, mengen={pos.id: Decimal("5")})
        assert self._pos(session, auftrag).menge == Decimal("2")  # auf Rest begrenzt
        assert angebot.status == BelegStatus.ERLEDIGT

    def test_alle_mengen_null_fehler(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel, menge=Decimal("2"))
        pos = self._pos(session, angebot)
        with pytest.raises(WerkplusFehler):
            erzeuge_folgebeleg(session, angebot, mengen={pos.id: Decimal("0")})
        assert angebot.status == BelegStatus.GEDRUCKT  # unverändert

    def test_stornierter_folgebeleg_gibt_menge_frei(self, session, adresse, artikel):
        angebot = self._gedruckter_beleg(session, adresse, artikel, menge=Decimal("2"))
        pos = self._pos(session, angebot)
        auftrag = erzeuge_folgebeleg(session, angebot, mengen={pos.id: Decimal("1")})
        assert restmenge(session, pos) == Decimal("1")
        setze_status(session, auftrag, BelegStatus.STORNIERT)
        assert restmenge(session, pos) == Decimal("2")  # Menge wieder frei


class TestDuplizieren:
    def test_dupliziert_entwurf_mit_kopierten_positionen(self, session, adresse, artikel):
        original = erstelle_beleg(
            session, Belegart.RECHNUNG, adresse, kopftext="K", fusstext="F"
        )
        fuege_position_hinzu(session, original, artikel, menge=Decimal("2"))
        fuege_position_hinzu(
            session,
            original,
            bezeichnung="Montage",
            menge=Decimal("1"),
            einzelpreis=Decimal("50"),
            rabatt_prozent=Decimal("0"),
            ust_satz=20,
        )
        # Zustand, der im Duplikat NICHT übernommen werden darf:
        original.gedruckt_am = datetime.now()
        original.bezahlt_am = date.today()
        session.flush()

        dup = beleg_service.dupliziere_beleg(session, original)

        assert dup.id != original.id
        assert dup.belegart == original.belegart
        assert dup.status == BelegStatus.ENTWURF
        assert dup.belegnr != original.belegnr
        assert dup.vorgaenger_beleg_id is None
        assert dup.gedruckt_am is None
        assert dup.bezahlt_am is None
        assert dup.kopftext == "K"
        assert dup.fusstext == "F"
        assert dup.adresse_id == original.adresse_id
        assert dup.summe_brutto == original.summe_brutto

        orig_pos = session.exec(
            select(BelegPosition)
            .where(BelegPosition.beleg_id == original.id)
            .order_by(BelegPosition.pos_nr)
        ).all()
        dup_pos = session.exec(
            select(BelegPosition)
            .where(BelegPosition.beleg_id == dup.id)
            .order_by(BelegPosition.pos_nr)
        ).all()
        assert len(dup_pos) == len(orig_pos) == 2
        assert [p.bezeichnung for p in dup_pos] == [p.bezeichnung for p in orig_pos]
        assert [p.menge for p in dup_pos] == [p.menge for p in orig_pos]
        assert [p.einzelpreis for p in dup_pos] == [p.einzelpreis for p in orig_pos]
        assert all(p.ursprungs_position_id is None for p in dup_pos)

    def test_dupliziert_ohne_seriennummern(self, session, adresse, artikel):
        original = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        pos = fuege_position_hinzu(session, original, artikel, menge=Decimal("1"))
        sn = Seriennummer(artikel_id=artikel.id, sn="SN-DUP-1")
        session.add(sn)
        session.flush()
        session.add(
            BelegPositionSeriennummer(position_id=pos.id, seriennummer_id=sn.id)
        )
        session.flush()

        dup = beleg_service.dupliziere_beleg(session, original)
        dup_pos = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == dup.id)
        ).one()
        zuordnungen = session.exec(
            select(BelegPositionSeriennummer).where(
                BelegPositionSeriennummer.position_id == dup_pos.id
            )
        ).all()
        assert zuordnungen == []


class TestAbschlagUndSchluss:
    def _gedruckter_auftrag(self, session, adresse, artikel, menge=Decimal("2")):
        auftrag = erstelle_beleg(session, Belegart.AUFTRAG, adresse, kopftext="K")
        fuege_position_hinzu(session, auftrag, artikel, menge=menge, rabatt_prozent=Decimal("0"))
        setze_status(session, auftrag, BelegStatus.GEDRUCKT)
        return auftrag

    def test_abschlagsrechnung_prozent(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)  # netto 200
        abschlag = erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("20"))
        assert abschlag.belegart == Belegart.RECHNUNG
        assert abschlag.ist_abschlag is True
        assert abschlag.auftrag_id == auftrag.id
        assert abschlag.status == BelegStatus.ENTWURF
        assert abschlag.adresse_id == auftrag.adresse_id
        assert abschlag.belegnr.isdigit()  # eigener Rechnungs-Nummernkreis (JJNNNN)
        # 20 % von 200 = 40 netto, 8 USt, 48 brutto
        assert abschlag.summe_netto == Decimal("40.00")
        assert abschlag.summe_brutto == Decimal("48.00")
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == abschlag.id)
        ).one()
        assert position.einzelpreis == Decimal("40.00")
        assert position.ust_satz == 20
        assert "Abschlag 20%" in position.bezeichnung
        assert auftrag.belegnr in position.bezeichnung

    def test_abschlagsrechnung_betrag(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)
        abschlag = erzeuge_abschlagsrechnung(session, auftrag, betrag=Decimal("50"))
        assert abschlag.summe_netto == Decimal("50.00")
        position = session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == abschlag.id)
        ).one()
        assert position.einzelpreis == Decimal("50.00")

    def test_abschlag_nur_aus_auftrag(self, session, adresse, artikel):
        angebot = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        fuege_position_hinzu(session, angebot, artikel, rabatt_prozent=Decimal("0"))
        setze_status(session, angebot, BelegStatus.GEDRUCKT)
        with pytest.raises(UngueltigeBelegart):
            erzeuge_abschlagsrechnung(session, angebot, prozent=Decimal("10"))

    def test_abschlag_nur_gedruckt(self, session, adresse, artikel):
        auftrag = erstelle_beleg(session, Belegart.AUFTRAG, adresse)
        fuege_position_hinzu(session, auftrag, artikel, rabatt_prozent=Decimal("0"))
        with pytest.raises(UngueltigerStatusUebergang):
            erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("10"))

    def test_abschlag_ohne_angabe_fehler(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)
        with pytest.raises(WerkplusFehler):
            erzeuge_abschlagsrechnung(session, auftrag)

    def test_bereits_abgerechnete_abschlaege_summiert(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)  # netto 200
        assert bereits_abgerechnete_abschlaege(session, auftrag) == Decimal("0")
        erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("20"))  # 40
        erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("10"))  # 20
        assert bereits_abgerechnete_abschlaege(session, auftrag) == Decimal("60.00")

    def test_stornierter_abschlag_zaehlt_nicht(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)
        abschlag = erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("20"))
        setze_status(session, abschlag, BelegStatus.STORNIERT)
        assert bereits_abgerechnete_abschlaege(session, auftrag) == Decimal("0")

    def test_schlussrechnung_mit_abzug(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)  # netto 200
        erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("20"))  # 40 netto

        schluss = erzeuge_schlussrechnung(session, auftrag)
        assert schluss.belegart == Belegart.RECHNUNG
        assert schluss.auftrag_id == auftrag.id
        assert schluss.vorgaenger_beleg_id == auftrag.id
        positionen = list(
            session.exec(
                select(BelegPosition)
                .where(BelegPosition.beleg_id == schluss.id)
                .order_by(BelegPosition.pos_nr)
            ).all()
        )
        # volle Auftragsposition + eine negative Abzugsposition
        assert len(positionen) == 2
        assert positionen[0].einzelpreis == Decimal("100.00")
        assert positionen[0].menge == Decimal("2")
        assert positionen[1].einzelpreis == Decimal("-40.00")
        assert positionen[1].menge == Decimal("1")
        assert "Abzug Abschlagsrechnung" in positionen[1].bezeichnung
        # Restbetrag: 200 − 40 = 160 netto
        assert schluss.summe_netto == Decimal("160.00")
        assert schluss.summe_brutto == Decimal("192.00")

    def test_schlussrechnung_ohne_abschlag(self, session, adresse, artikel):
        auftrag = self._gedruckter_auftrag(session, adresse, artikel)
        schluss = erzeuge_schlussrechnung(session, auftrag)
        positionen = list(
            session.exec(
                select(BelegPosition).where(BelegPosition.beleg_id == schluss.id)
            ).all()
        )
        assert len(positionen) == 1
        assert schluss.summe_netto == Decimal("200.00")


class TestSammelrechnung:
    def _gedruckter_lieferschein(self, session, adresse, artikel, menge=Decimal("1")):
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        fuege_position_hinzu(session, ls, artikel, menge=menge, rabatt_prozent=Decimal("0"))
        setze_status(session, ls, BelegStatus.GEDRUCKT)
        return ls

    def test_fuehrt_positionen_zusammen(self, session, adresse, artikel):
        ls1 = self._gedruckter_lieferschein(session, adresse, artikel, menge=Decimal("1"))
        ls2 = self._gedruckter_lieferschein(session, adresse, artikel, menge=Decimal("3"))

        rechnung = erzeuge_sammelrechnung(session, [ls1, ls2])
        assert rechnung.belegart == Belegart.RECHNUNG
        assert rechnung.status == BelegStatus.ENTWURF
        positionen = list(
            session.exec(
                select(BelegPosition)
                .where(BelegPosition.beleg_id == rechnung.id)
                .order_by(BelegPosition.pos_nr)
            ).all()
        )
        assert len(positionen) == 2
        assert [p.pos_nr for p in positionen] == [1, 2]
        assert [p.menge for p in positionen] == [Decimal("1"), Decimal("3")]
        # 1*100 + 3*100 = 400 netto
        assert rechnung.summe_netto == Decimal("400.00")
        # Lieferscheine sind jetzt erledigt
        assert ls1.status == BelegStatus.ERLEDIGT
        assert ls2.status == BelegStatus.ERLEDIGT

    def test_leere_liste_fehler(self, session):
        with pytest.raises(WerkplusFehler):
            erzeuge_sammelrechnung(session, [])

    def test_verschiedene_kunden_fehler(self, session, adresse, artikel):
        andere = Adresse(firma="Andere GmbH")
        session.add(andere)
        session.flush()
        ls1 = self._gedruckter_lieferschein(session, adresse, artikel)
        ls2 = self._gedruckter_lieferschein(session, andere, artikel)
        with pytest.raises(WerkplusFehler):
            erzeuge_sammelrechnung(session, [ls1, ls2])

    def test_falsche_belegart_fehler(self, session, adresse, artikel):
        ls = self._gedruckter_lieferschein(session, adresse, artikel)
        auftrag = erstelle_beleg(session, Belegart.AUFTRAG, adresse)
        fuege_position_hinzu(session, auftrag, artikel, rabatt_prozent=Decimal("0"))
        setze_status(session, auftrag, BelegStatus.GEDRUCKT)
        with pytest.raises(WerkplusFehler):
            erzeuge_sammelrechnung(session, [ls, auftrag])


class TestAktualisiereSummen:
    def test_direktaufruf(self, session, adresse, artikel):
        beleg = erstelle_beleg(session, Belegart.ANGEBOT, adresse)
        session.add(
            BelegPosition(
                beleg_id=beleg.id,
                pos_nr=1,
                menge=Decimal("1"),
                einzelpreis=Decimal("10.00"),
                rabatt_prozent=Decimal("0"),
                ust_satz=10,
            )
        )
        session.flush()
        aktualisiere_summen(session, beleg)
        assert beleg.summe_netto == Decimal("10.00")
        assert beleg.summe_ust == Decimal("1.00")
        assert beleg.summe_brutto == Decimal("11.00")
        assert parse_steuer_details(beleg.steuer_details)[10].ust == Decimal("1.00")
