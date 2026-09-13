"""Tests des PDF-Renderings.

Die HTML-Tests laufen immer (kein WeasyPrint nötig). Der eigentliche PDF-Test
läuft nur, wenn WeasyPrint samt GTK/Pango-Runtime importierbar ist — auf
Windows ohne GTK wird er geskippt.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.models.adresse import Adresse, AdressTyp
from app.models.artikel import Artikel
from app.models.beleg import Beleg, Belegart, BelegPositionSeriennummer, BelegStatus
from app.models.seriennummer import Seriennummer
from app.services import firma_service, pdf_service
from app.services.beleg_service import (
    erstelle_beleg,
    erzeuge_abschlagsrechnung,
    erzeuge_folgebeleg,
    erzeuge_schlussrechnung,
    fuege_position_hinzu,
    setze_status,
)
from app.services.pdf_service import (
    PdfNichtVerfuegbar,
    render_beleg_html,
    render_beleg_pdf,
)
from app.templating import format_datum, format_euro


def _weasyprint_lauffaehig() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


@pytest.fixture
def kunde(session) -> Adresse:
    adresse = Adresse(
        firma="Testkunde GmbH",
        ansprechpartner="Max Mustermann",
        strasse="Kundenweg 1",
        plz="1010",
        ort="Wien",
        uid="ATU87654321",
        zahlungsziel_tage=14,
        standard_rabatt_prozent=Decimal("0"),
    )
    session.add(adresse)
    session.flush()
    return adresse


@pytest.fixture
def lieferant(session) -> Adresse:
    adresse = Adresse(
        typ=AdressTyp.LIEFERANT,
        firma="Großhandel Liefer GmbH",
        ansprechpartner="Erna Einkauf",
        strasse="Lieferstraße 9",
        plz="4020",
        ort="Linz",
        uid="ATU11223344",
        zahlungsziel_tage=30,
        standard_rabatt_prozent=Decimal("0"),
    )
    session.add(adresse)
    session.flush()
    return adresse


@pytest.fixture
def artikel_sn(session) -> Artikel:
    """SN-pflichtiger Artikel, 20 % USt."""
    artikel = Artikel(
        artikelnr="WP-8",
        bezeichnung="Wärmepumpe WP-8",
        langtext="Luft/Wasser-Wärmepumpe 8 kW",
        vk_preis=Decimal("100.00"),
        ust_satz=20,
        seriennummernpflichtig=True,
    )
    session.add(artikel)
    session.flush()
    return artikel


@pytest.fixture
def artikel_zwanzig(session) -> Artikel:
    """Normaler Artikel, 20 % USt (bekommt im Beleg 5 % Rabatt)."""
    artikel = Artikel(
        artikelnr="RO-15",
        bezeichnung="Kupferrohr 15 mm",
        vk_preis=Decimal("50.00"),
        ust_satz=20,
    )
    session.add(artikel)
    session.flush()
    return artikel


@pytest.fixture
def artikel_zehn(session) -> Artikel:
    """Artikel mit 10 % USt."""
    artikel = Artikel(
        artikelnr="DL-10",
        bezeichnung="Wartungspauschale",
        vk_preis=Decimal("30.00"),
        ust_satz=10,
        ist_lagerartikel=False,
    )
    session.add(artikel)
    session.flush()
    return artikel


def _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn) -> None:
    """3 Positionen: 2× 20 % (eine mit 5 % Rabatt), 1× 10 %, eine mit Seriennummer.

    Netto 20 %: 100,00 + 95,00 = 195,00 (USt 39,00)
    Netto 10 %: 90,00 (USt 9,00) — gesamt 285,00 / 48,00 / 333,00.
    """
    # Preise explizit (VK), damit die Beträge auch auf Einkaufsbelegen gleich
    # bleiben — dort würde der Service sonst den EK des Artikels vorschlagen.
    pos_sn = fuege_position_hinzu(
        session, beleg, artikel_sn, menge=Decimal("1"), einzelpreis=artikel_sn.vk_preis
    )
    fuege_position_hinzu(
        session, beleg, artikel_zwanzig, menge=Decimal("2"), rabatt_prozent=Decimal("5"),
        einzelpreis=artikel_zwanzig.vk_preis,
    )
    fuege_position_hinzu(
        session, beleg, artikel_zehn, menge=Decimal("3"), einzelpreis=artikel_zehn.vk_preis
    )
    sn = Seriennummer(artikel_id=artikel_sn.id, sn="SN-4711")
    session.add(sn)
    session.flush()
    session.add(BelegPositionSeriennummer(position_id=pos_sn.id, seriennummer_id=sn.id))
    session.flush()


@pytest.fixture
def rechnung(session, kunde, artikel_sn, artikel_zwanzig, artikel_zehn) -> Beleg:
    beleg = erstelle_beleg(session, Belegart.RECHNUNG, kunde)
    _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn)
    setze_status(session, beleg, BelegStatus.GEDRUCKT)
    return beleg


class TestRechnungHtml:
    def test_pflichtmerkmale_nach_par_11_ustg(self, session, rechnung):
        html = render_beleg_html(session, rechnung)
        # Aussteller und Empfänger mit Anschrift und UID
        assert settings.firma.name in html
        assert settings.firma.strasse in html
        assert settings.firma.uid in html
        assert "Testkunde GmbH" in html
        assert "Kundenweg 1" in html
        assert "ATU87654321" in html
        # Fortlaufende Nummer, Ausstellungsdatum, Leistungsdatum
        assert rechnung.belegnr in html
        assert format_datum(rechnung.datum) in html
        assert "Leistungsdatum entspricht Rechnungsdatum" in html
        # Menge + Bezeichnung
        assert "Wärmepumpe WP-8" in html
        assert "Kupferrohr 15 mm" in html
        assert "Wartungspauschale" in html

    def test_summen_je_steuersatz_und_brutto(self, session, rechnung):
        html = render_beleg_html(session, rechnung)
        assert "20 %" in html
        assert "10 %" in html
        assert format_euro(Decimal("195.00")) in html  # Netto 20 %
        assert format_euro(Decimal("39.00")) in html  # USt 20 %
        assert format_euro(Decimal("90.00")) in html  # Netto 10 %
        assert format_euro(Decimal("285.00")) in html  # Netto gesamt
        assert format_euro(Decimal("48.00")) in html  # USt gesamt
        assert format_euro(Decimal("333.00")) in html  # Brutto

    def test_seriennummer_zahlungsziel_iban(self, session, rechnung):
        html = render_beleg_html(session, rechnung)
        assert "SN: SN-4711" in html
        faelligkeit = rechnung.datum + timedelta(days=rechnung.zahlungsziel_tage)
        assert f"Zahlbar bis {format_datum(faelligkeit)} ohne Abzug" in html
        assert settings.firma.iban in html

    def test_firma_aus_db_ueberschreibt_config(self, session, rechnung):
        firma_service.aktualisiere_firma(
            session, name="Testfirma XY", uid="ATU99999999"
        )
        html = render_beleg_html(session, rechnung)
        # DB-Firmendaten erscheinen im Beleg statt der Config-Defaults
        assert "Testfirma XY" in html
        assert "ATU99999999" in html
        assert settings.firma.name not in html

    def test_leistungszeitraum_wenn_von_bis_verschieden(
        self, session, kunde, artikel_sn, artikel_zwanzig, artikel_zehn
    ):
        beleg = erstelle_beleg(session, Belegart.RECHNUNG, kunde)
        _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn)
        beleg.leistungsdatum_von = beleg.datum - timedelta(days=10)
        beleg.leistungsdatum_bis = beleg.datum - timedelta(days=3)
        session.add(beleg)
        session.flush()
        html = render_beleg_html(session, beleg)
        assert "Leistungszeitraum:" in html
        assert format_datum(beleg.leistungsdatum_von) in html
        assert format_datum(beleg.leistungsdatum_bis) in html


class TestLieferscheinHtml:
    def test_keine_preise_aber_seriennummern(
        self, session, kunde, artikel_sn, artikel_zwanzig, artikel_zehn
    ):
        beleg = erstelle_beleg(session, Belegart.LIEFERSCHEIN, kunde)
        _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        html = render_beleg_html(session, beleg)
        assert "Lieferschein" in html
        assert "SN: SN-4711" in html
        assert "Wärmepumpe WP-8" in html
        # Keinerlei Preise: weder Euro-Zeichen noch Einzelpreis- oder Summenwerte
        assert "€" not in html
        assert "&euro;" not in html
        assert format_euro(Decimal("100.00")) not in html  # Einzelpreis
        assert format_euro(Decimal("285.00")) not in html  # Summe netto
        assert format_euro(Decimal("333.00")) not in html  # Summe brutto


class TestEkLieferscheinHtml:
    def test_wareneingang_mengen_und_sn_ohne_preise(
        self, session, lieferant, artikel_sn, artikel_zwanzig, artikel_zehn
    ):
        beleg = erstelle_beleg(session, Belegart.EK_LIEFERSCHEIN, lieferant)
        _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        html = render_beleg_html(session, beleg)
        assert "Wareneingang" in html
        assert "Großhandel Liefer GmbH" in html  # Lieferant im Kopf
        # Mengen und Seriennummern sind vorhanden
        assert "SN: SN-4711" in html
        assert "Wärmepumpe WP-8" in html
        assert "Kupferrohr 15 mm" in html
        # Keinerlei Preise im Positionsblock: weder Euro-Zeichen noch Preiswerte
        assert "€" not in html
        assert "&euro;" not in html
        assert format_euro(Decimal("100.00")) not in html  # Einzelpreis
        assert format_euro(Decimal("285.00")) not in html  # Summe netto
        assert format_euro(Decimal("333.00")) not in html  # Summe brutto


class TestEkRechnungHtml:
    def test_eingangsrechnung_mit_betraegen_je_steuersatz(
        self, session, lieferant, artikel_sn, artikel_zwanzig, artikel_zehn
    ):
        beleg = erstelle_beleg(session, Belegart.EK_RECHNUNG, lieferant)
        _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        html = render_beleg_html(session, beleg)
        # Fachliche Kennzeichnung als Eingangsrechnung des Lieferanten
        assert "Eingangsrechnung" in html
        assert "Großhandel Liefer GmbH" in html
        # Beträge und Summen je Steuersatz
        assert "20 %" in html
        assert "10 %" in html
        assert format_euro(Decimal("195.00")) in html  # Netto 20 %
        assert format_euro(Decimal("39.00")) in html  # USt 20 %
        assert format_euro(Decimal("90.00")) in html  # Netto 10 %
        assert format_euro(Decimal("285.00")) in html  # Netto gesamt
        assert format_euro(Decimal("48.00")) in html  # USt gesamt
        assert format_euro(Decimal("333.00")) in html  # Brutto
        # Keine "Zahlbar bis"-Zeile (Rechnung des Lieferanten, nicht unsere)
        assert "Zahlbar bis" not in html

    def test_kopftitel_zeigt_eingangsrechnung(
        self, session, lieferant, artikel_sn, artikel_zwanzig, artikel_zehn
    ):
        beleg = erstelle_beleg(session, Belegart.EK_RECHNUNG, lieferant)
        _befuelle_beleg(session, beleg, artikel_sn, artikel_zwanzig, artikel_zehn)
        setze_status(session, beleg, BelegStatus.GEDRUCKT)
        html = render_beleg_html(session, beleg)
        # Der Belegkopf (h1) zeigt "Eingangsrechnung", nicht "EK-Rechnung"
        assert f"<h1>Eingangsrechnung {beleg.belegnr}" in html


class TestGutschriftHtml:
    def test_referenz_auf_rechnung(self, session, rechnung):
        gutschrift = erzeuge_folgebeleg(session, rechnung)
        html = render_beleg_html(session, gutschrift)
        assert "Gutschrift" in html
        assert gutschrift.belegnr in html
        assert rechnung.belegnr in html  # Hinweis auf die referenzierte Rechnung


class TestAbschlagUndSchlussHtml:
    def _gedruckter_auftrag(self, session, kunde, artikel_zwanzig) -> Beleg:
        auftrag = erstelle_beleg(session, Belegart.AUFTRAG, kunde)
        fuege_position_hinzu(
            session, auftrag, artikel_zwanzig, menge=Decimal("2"), rabatt_prozent=Decimal("0")
        )  # 2 * 50 = 100 netto
        setze_status(session, auftrag, BelegStatus.GEDRUCKT)
        return auftrag

    def test_abschlagsrechnung_html_zeigt_abschlag(self, session, kunde, artikel_zwanzig):
        auftrag = self._gedruckter_auftrag(session, kunde, artikel_zwanzig)
        abschlag = erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("20"))
        html = render_beleg_html(session, abschlag)
        assert "Abschlagsrechnung" in html
        assert "Abschlag" in html
        # Pflichtangaben bleiben erhalten (Aussteller-UID, Empfänger)
        assert settings.firma.uid in html
        assert "Testkunde GmbH" in html

    def test_schlussrechnung_html_zeigt_minusbetrag(self, session, kunde, artikel_zwanzig):
        auftrag = self._gedruckter_auftrag(session, kunde, artikel_zwanzig)
        erzeuge_abschlagsrechnung(session, auftrag, prozent=Decimal("20"))  # 20 netto
        schluss = erzeuge_schlussrechnung(session, auftrag)
        html = render_beleg_html(session, schluss)
        assert "Abzug Abschlagsrechnung" in html
        assert format_euro(Decimal("-20.00")) in html  # negative Abzugsposition
        assert format_euro(Decimal("80.00")) in html  # Restbetrag netto


class TestRenderBelegPdf:
    @pytest.mark.skipif(
        not _weasyprint_lauffaehig(),
        reason="WeasyPrint (GTK/Pango-Runtime) auf diesem System nicht verfügbar",
    )
    def test_liefert_pdf_bytes(self, session, rechnung):
        daten = render_beleg_pdf(session, rechnung)
        assert isinstance(daten, bytes)
        assert daten.startswith(b"%PDF")

    @pytest.mark.parametrize(
        "fehler",
        [ImportError("No module named 'weasyprint'"), OSError("cannot load library 'gobject'")],
    )
    def test_wirft_pdf_nicht_verfuegbar_ohne_weasyprint(
        self, session, rechnung, monkeypatch, fehler
    ):
        def _kaputter_import():
            raise fehler

        monkeypatch.setattr(pdf_service, "_weasyprint", None)
        monkeypatch.setattr(pdf_service, "_weasyprint_fehler", None)
        monkeypatch.setattr(pdf_service, "_importiere_weasyprint", _kaputter_import)
        with pytest.raises(PdfNichtVerfuegbar):
            render_beleg_pdf(session, rechnung)
        # Der Fehler ist gecacht — auch der zweite Aufruf wirft sauber
        with pytest.raises(PdfNichtVerfuegbar):
            render_beleg_pdf(session, rechnung)
