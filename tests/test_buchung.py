"""End-to-End-Tests der Lagerwirkung: Buchungs-/Storno-Hooks über die Services."""

from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.adresse import Adresse
from app.models.artikel import Artikel
from app.models.beleg import Beleg, Belegart, BelegPosition, BelegStatus
from app.models.lager import Buchungsart, Lager, Lagerbewegung
from app.models.seriennummer import Seriennummer, SnStatus
from app.services import beleg_service, lager_service
from app.services.beleg_service import (
    erstelle_beleg,
    erzeuge_folgebeleg,
    fuege_position_hinzu,
    setze_status,
)
from app.services.buchung import registriere_lager_hooks
from app.services.fehler import SnPflichtVerletzt, StornoNichtMoeglich
from app.services.lager_service import bestand
from app.services.seriennummer_service import (
    erfasse_wareneingang_sns,
    verfuegbare_sns,
    weise_sns_zu,
)


@pytest.fixture(autouse=True)
def _lager_hooks():
    """Lager-Hooks für diese Testdatei registrieren, danach Registries leeren."""
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()
    registriere_lager_hooks()
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


@pytest.fixture
def adresse(session) -> Adresse:
    adresse = Adresse(firma="Muster GmbH")
    session.add(adresse)
    session.flush()
    return adresse


@pytest.fixture
def lager(session) -> Lager:
    lager = Lager(name="Hauptlager", ist_standard=True)
    session.add(lager)
    session.flush()
    return lager


@pytest.fixture
def sn_artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="WP-8",
        bezeichnung="Wärmepumpe 8 kW",
        seriennummernpflichtig=True,
        vk_preis=Decimal("5000.00"),
    )
    session.add(artikel)
    session.flush()
    return artikel


@pytest.fixture
def normal_artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="KR-22",
        bezeichnung="Kupferrohr 22mm",
        einheit="m",
        vk_preis=Decimal("7.00"),
    )
    session.add(artikel)
    session.flush()
    return artikel


@pytest.fixture
def dienstleistung(session) -> Artikel:
    artikel = Artikel(
        artikelnr="MONT",
        bezeichnung="Montagestunde",
        einheit="h",
        ist_lagerartikel=False,
        vk_preis=Decimal("95.00"),
    )
    session.add(artikel)
    session.flush()
    return artikel


def _positionen(session, beleg) -> list[BelegPosition]:
    return list(
        session.exec(
            select(BelegPosition).where(BelegPosition.beleg_id == beleg.id)
        ).all()
    )


def _wareneingang_ueber_bestellung(session, adresse, artikel, sns: list[str]):
    """Bestellung → (Folgebeleg) EK-Lieferschein → SN-Erfassung → Buchen."""
    bestellung = erstelle_beleg(session, Belegart.BESTELLUNG, adresse)
    fuege_position_hinzu(session, bestellung, artikel, menge=Decimal(len(sns)))
    setze_status(session, bestellung, BelegStatus.GEDRUCKT)  # keine Lagerwirkung
    ek_ls = erzeuge_folgebeleg(session, bestellung)
    ek_position = _positionen(session, ek_ls)[0]
    erfasse_wareneingang_sns(session, ek_position, sns)
    setze_status(session, ek_ls, BelegStatus.GEDRUCKT)  # Wareneingang bucht
    return ek_ls


class TestKompletterFluss:
    def test_einkauf_verkauf_storno(
        self, session, adresse, lager, sn_artikel, normal_artikel, dienstleistung
    ):
        # --- Einkauf: Bestellung → EK-LS, 3 SNs erfassen, buchen ---
        bestellung = erstelle_beleg(session, Belegart.BESTELLUNG, adresse)
        fuege_position_hinzu(session, bestellung, sn_artikel, menge=Decimal("3"))
        setze_status(session, bestellung, BelegStatus.GEDRUCKT)
        assert session.exec(select(Lagerbewegung)).all() == []  # Bestellung wirkt nicht

        ek_ls = erzeuge_folgebeleg(session, bestellung)
        ek_position = _positionen(session, ek_ls)[0]
        erfasse_wareneingang_sns(session, ek_position, ["SN-1", "SN-2", "SN-3"])
        assert verfuegbare_sns(session, sn_artikel.id) == []  # Entwurf: noch nicht eingebucht

        setze_status(session, ek_ls, BelegStatus.GEDRUCKT)
        assert bestand(session, sn_artikel.id) == Decimal("3")
        verfuegbar = verfuegbare_sns(session, sn_artikel.id)
        assert [sn.sn for sn in verfuegbar] == ["SN-1", "SN-2", "SN-3"]
        assert all(sn.lager_id == lager.id for sn in verfuegbar)

        # --- Verkauf: Lieferschein mit SN-, Nicht-SN- und Dienstleistungs-Position ---
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        pos_sn = fuege_position_hinzu(session, ls, sn_artikel, menge=Decimal("2"))
        pos_normal = fuege_position_hinzu(session, ls, normal_artikel, menge=Decimal("4"))
        pos_dienst = fuege_position_hinzu(session, ls, dienstleistung, menge=Decimal("1"))
        weise_sns_zu(session, pos_sn, [verfuegbar[0].id, verfuegbar[1].id])

        setze_status(session, ls, BelegStatus.GEDRUCKT)
        assert bestand(session, sn_artikel.id) == Decimal("1")  # 3 − 2
        assert bestand(session, normal_artikel.id) == Decimal("-4")  # negativ ist ok
        # Dienstleistung erzeugt NIE eine Lagerbewegung
        assert (
            session.exec(
                select(Lagerbewegung).where(Lagerbewegung.artikel_id == dienstleistung.id)
            ).all()
            == []
        )
        assert (
            session.exec(
                select(Lagerbewegung).where(Lagerbewegung.position_id == pos_dienst.id)
            ).all()
            == []
        )
        session.refresh(verfuegbar[0])
        session.refresh(verfuegbar[1])
        assert verfuegbar[0].status == SnStatus.VERKAUFT
        assert verfuegbar[0].lieferschein_position_id == pos_sn.id
        assert verfuegbar[1].status == SnStatus.VERKAUFT
        assert [sn.sn for sn in verfuegbare_sns(session, sn_artikel.id)] == ["SN-3"]
        # Nicht-SN-Abgang referenziert die Position im Journal
        abgang = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.position_id == pos_normal.id)
        ).one()
        assert abgang.menge == Decimal("-4")
        assert abgang.buchungsart == Buchungsart.LIEFERSCHEIN

        # --- Storno des Lieferscheins: Bestand zurück, SNs wieder verfügbar ---
        setze_status(session, ls, BelegStatus.STORNIERT)
        assert bestand(session, sn_artikel.id) == Decimal("3")
        assert bestand(session, normal_artikel.id) == Decimal("0")
        session.refresh(verfuegbar[0])
        assert verfuegbar[0].status == SnStatus.AUF_LAGER
        assert verfuegbar[0].lieferschein_position_id is None
        assert len(verfuegbare_sns(session, sn_artikel.id)) == 3
        # Journal: Originalbuchungen bleiben, Storno kommt als Gegenbuchung dazu
        ls_journal = session.exec(
            select(Lagerbewegung).where(Lagerbewegung.beleg_id == ls.id)
        ).all()
        original = [b for b in ls_journal if b.buchungsart == Buchungsart.LIEFERSCHEIN]
        storni = [b for b in ls_journal if b.buchungsart == Buchungsart.STORNO]
        assert len(original) == 3  # 2× SN à −1, 1× Nicht-SN −4
        assert len(storni) == 3
        assert sum((b.menge for b in ls_journal), Decimal("0")) == Decimal("0")


class TestSnPflicht:
    def test_buchen_ohne_sn_zuweisung_wirft_und_beleg_bleibt_entwurf(
        self, session, adresse, lager, sn_artikel
    ):
        _wareneingang_ueber_bestellung(session, adresse, sn_artikel, ["SN-1", "SN-2"])
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        fuege_position_hinzu(session, ls, sn_artikel, menge=Decimal("2"))
        session.commit()  # Setup festschreiben, damit der Rollback nur den Fehlversuch trifft
        ls_id = ls.id
        journal_vorher = len(session.exec(select(Lagerbewegung)).all())

        with pytest.raises(SnPflichtVerletzt):
            setze_status(session, ls, BelegStatus.GEDRUCKT)

        # setze_status flusht den Statuswechsel VOR dem Hook. Dokumentiertes Verhalten:
        # Der Hook validiert, bevor er bucht — bei einer Hook-Exception bleibt ohne
        # Commit kein Schaden, der Aufrufer muss rollbacken.
        session.rollback()
        ls = session.get(Beleg, ls_id)
        assert ls.status == BelegStatus.ENTWURF
        assert len(session.exec(select(Lagerbewegung)).all()) == journal_vorher

    def test_falsche_anzahl_sns(self, session, adresse, lager, sn_artikel):
        _wareneingang_ueber_bestellung(session, adresse, sn_artikel, ["SN-1", "SN-2"])
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        position = fuege_position_hinzu(session, ls, sn_artikel, menge=Decimal("2"))
        eine_sn = verfuegbare_sns(session, sn_artikel.id)[0]
        weise_sns_zu(session, position, [eine_sn.id])  # nur 1 von 2
        with pytest.raises(SnPflichtVerletzt) as fehler:
            setze_status(session, ls, BelegStatus.GEDRUCKT)
        assert f"Position {position.pos_nr}" in str(fehler.value)

    def test_ek_buchen_mit_falscher_sn_anzahl(self, session, adresse, lager, sn_artikel):
        ek_ls = erstelle_beleg(session, Belegart.EK_LIEFERSCHEIN, adresse)
        position = fuege_position_hinzu(session, ek_ls, sn_artikel, menge=Decimal("3"))
        erfasse_wareneingang_sns(session, position, ["SN-1", "SN-2"])  # nur 2 von 3
        with pytest.raises(SnPflichtVerletzt):
            setze_status(session, ek_ls, BelegStatus.GEDRUCKT)

    def test_nicht_ganzzahlige_menge(self, session, adresse, lager, sn_artikel):
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        fuege_position_hinzu(session, ls, sn_artikel, menge=Decimal("1.5"))
        with pytest.raises(SnPflichtVerletzt):
            setze_status(session, ls, BelegStatus.GEDRUCKT)


class TestEkStorno:
    def test_storno_bucht_aus_und_sns_bleiben_erfasst(
        self, session, adresse, lager, sn_artikel
    ):
        ek_ls = _wareneingang_ueber_bestellung(
            session, adresse, sn_artikel, ["SN-1", "SN-2", "SN-3"]
        )
        setze_status(session, ek_ls, BelegStatus.STORNIERT)
        assert bestand(session, sn_artikel.id) == Decimal("0")
        assert verfuegbare_sns(session, sn_artikel.id) == []
        sns = session.exec(
            select(Seriennummer).where(Seriennummer.artikel_id == sn_artikel.id)
        ).all()
        assert len(sns) == 3  # bleiben als erfasst bestehen
        assert all(sn.lager_id is None and sn.status == SnStatus.AUF_LAGER for sn in sns)
        storni = session.exec(
            select(Lagerbewegung).where(
                Lagerbewegung.beleg_id == ek_ls.id,
                Lagerbewegung.buchungsart == Buchungsart.STORNO,
            )
        ).all()
        assert len(storni) == 3

    def test_storno_nach_verkauf_nicht_moeglich(self, session, adresse, lager, sn_artikel):
        ek_ls = _wareneingang_ueber_bestellung(session, adresse, sn_artikel, ["SN-1"])
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        position = fuege_position_hinzu(session, ls, sn_artikel, menge=Decimal("1"))
        weise_sns_zu(session, position, [verfuegbare_sns(session, sn_artikel.id)[0].id])
        setze_status(session, ls, BelegStatus.GEDRUCKT)  # SN ist jetzt verkauft
        session.commit()

        with pytest.raises(StornoNichtMoeglich):
            setze_status(session, ek_ls, BelegStatus.STORNIERT)
        session.rollback()
        assert session.get(Beleg, ek_ls.id).status == BelegStatus.GEDRUCKT

    def test_storno_bei_offener_ls_zuordnung_nicht_moeglich(
        self, session, adresse, lager, sn_artikel
    ):
        ek_ls = _wareneingang_ueber_bestellung(session, adresse, sn_artikel, ["SN-1"])
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        position = fuege_position_hinzu(session, ls, sn_artikel, menge=Decimal("1"))
        weise_sns_zu(session, position, [verfuegbare_sns(session, sn_artikel.id)[0].id])
        # LS noch im Entwurf — SN ist zugeordnet, aber nicht verkauft
        with pytest.raises(StornoNichtMoeglich):
            setze_status(session, ek_ls, BelegStatus.STORNIERT)


class TestNegativerBestand:
    def test_lieferschein_bucht_unter_null_durch(
        self, session, adresse, lager, normal_artikel
    ):
        lager_service.buche(
            session,
            artikel_id=normal_artikel.id,
            lager_id=lager.id,
            menge=Decimal("5"),
            buchungsart=Buchungsart.KORREKTUR,
        )
        ls = erstelle_beleg(session, Belegart.LIEFERSCHEIN, adresse)
        fuege_position_hinzu(session, ls, normal_artikel, menge=Decimal("10"))
        setze_status(session, ls, BelegStatus.GEDRUCKT)  # keine Bestandssperre
        assert ls.status == BelegStatus.GEDRUCKT
        assert bestand(session, normal_artikel.id) == Decimal("-5")


class TestRegistrierung:
    def test_idempotent(self):
        registriere_lager_hooks()
        registriere_lager_hooks()  # Doppelaufruf registriert nicht doppelt
        assert set(beleg_service._buchungs_hooks) == {
            Belegart.LIEFERSCHEIN,
            Belegart.EK_LIEFERSCHEIN,
        }
        assert set(beleg_service._storno_hooks) == {
            Belegart.LIEFERSCHEIN,
            Belegart.EK_LIEFERSCHEIN,
        }

    def test_keine_wirkung_fuer_andere_belegarten(
        self, session, adresse, lager, normal_artikel
    ):
        for belegart in (
            Belegart.ANGEBOT,
            Belegart.AUFTRAG,
            Belegart.RECHNUNG,
            Belegart.GUTSCHRIFT,
            Belegart.BESTELLUNG,
            Belegart.EK_RECHNUNG,
        ):
            beleg = erstelle_beleg(session, belegart, adresse)
            fuege_position_hinzu(session, beleg, normal_artikel, menge=Decimal("2"))
            setze_status(session, beleg, BelegStatus.GEDRUCKT)
            setze_status(session, beleg, BelegStatus.STORNIERT)
        assert session.exec(select(Lagerbewegung)).all() == []
        assert bestand(session, normal_artikel.id) == Decimal("0")
