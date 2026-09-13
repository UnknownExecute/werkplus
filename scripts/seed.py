"""Seed-Daten: Benutzer, Lager, 5 Kunden, 2 Lieferanten, 20 Artikel (5 SN-pflichtig à 3 SNs).

Aufruf:  python -m uv run python -m scripts.seed
Der Seed ist idempotent — vorhandene Daten (per Artikelnr./Login/Firma erkannt) werden übersprungen.
Zusätzlich (M5): Demo-Belegdurchlauf Angebot→Auftrag→Lieferschein→Rechnung über die
Services (inkl. Lagerbuchung), zwei Demo-Anlagen für das Dashboard sowie ein
Regie-Auftrag im Entwurf mit erfassten Arbeitszeiten (Zeiterfassung).
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.database import engine
from app.models import (
    Adresse,
    AdressTyp,
    Anlage,
    Ansprechpartner,
    Artikel,
    Beleg,
    Belegart,
    BelegPosition,
    BelegStatus,
    Benutzer,
    Buchungsart,
    Lager,
    Lagerbestand,
    Lagerbewegung,
    Lieferadresse,
    Rolle,
    Seriennummer,
    SnStatus,
    Zeiterfassung,
)
from app.services import (
    adresse_service,
    anlagen_service,
    artikel_service,
    beleg_service,
    berechnung,
    seriennummer_service,
    zeiterfassung_service,
)
from app.services.auth_service import hash_passwort
from app.services.buchung import registriere_lager_hooks

# (login, name, passwort, rolle, stundensatz) — Stundensätze für die Zeiterfassung
BENUTZER = [
    ("admin", "Administrator", "admin", Rolle.ADMIN, "95.00"),
    ("sb", "Susi Bearbeiter", "sb", Rolle.SACHBEARBEITER, "78.00"),
    ("mo", "Max Monteur", "mo", "monteur", "68.00"),
]

# (firma, matchcode, ansprechpartner (Anrede, Vorname, Nachname, Funktion), strasse, plz, ort,
#  uid, telefon, email, branche, merkmale)
KUNDEN = [
    ("Hotel Alpenblick GmbH", "ALPENBLICK", ("Frau", "Petra", "Maier", "Direktion"),
     "Dorfstraße 1", "6020", "Innsbruck", "ATU61234567", "0512 34567", "office@alpenblick.at",
     "Hotellerie / Gastronomie", "Wartungsvertrag, Großkunde"),
    ("Bäckerei Brotherz e.U.", "BROTHERZ", ("Herr", "Karl", "Weber", "Inhaber"), "Hauptplatz 4",
     "4020", "Linz", "ATU62234567", "0732 987654", "info@brotherz.at", "Gewerbe",
     "Wartungsvertrag"),
    ("Wohnbau Steiner GmbH", "STEINER", ("Herr", "Josef", "Steiner", "Bauleitung"), "Bauring 12",
     "8010", "Graz", "ATU63234567", "0316 111222", "bau@steiner.at", "Bauunternehmen", ""),
    ("Gasthof Post KG", "POST", ("Frau", "Anna", "Huber", "Geschäftsführung"), "Poststraße 9",
     "5020", "Salzburg", "ATU64234567", "0662 555666", "post@gasthof-post.at",
     "Hotellerie / Gastronomie", ""),
    ("Privat Fam. Gruber", "GRUBER", ("Herr", "Franz", "Gruber", ""), "Feldweg 3",
     "3100", "St. Pölten", "", "0664 1234567", "", "Privat", ""),
]

LIEFERANTEN = [
    ("Vaillant Austria GmbH", "VAILLANT", ("", "", "Vertrieb Innendienst", ""),
     "Forchheimergasse 7", "1230", "Wien", "ATU65234567", "01 863600", "vertrieb@vaillant.at",
     "Handel", ""),
    ("Groß-Elektro Handel AG", "GROSSELEKTRO", ("Herr", "Otto", "Ohm", "Verkauf"),
     "Industriestraße 20", "4600", "Wels", "ATU66234567", "07242 4444",
     "verkauf@grosselektro.at", "Handel", ""),
]

# (artikelnr, bezeichnung, warengruppe, einheit, ek, vk, ust, sn_pflicht, lagerartikel)
ARTIKEL = [
    ("HZ-1001", "Gastherme ecoTEC plus VC 20", "Heizung", "Stk", "1450.00", "2190.00", 20, True,
     True),
    ("HZ-1002", "Wärmepumpe aroTHERM 8kW", "Heizung", "Stk", "5200.00", "7990.00", 20, True, True),
    ("HZ-1003", "Warmwasserspeicher 200l", "Heizung", "Stk", "620.00", "990.00", 20, True, True),
    ("KT-2001", "Klimagerät Split 3,5kW", "Kälte", "Stk", "780.00", "1290.00", 20, True, True),
    ("EL-3001", "Stromzähler smart 3-phasig", "Elektro", "Stk", "145.00", "249.00", 20, True, True),
    ("HZ-1101", "Heizkörper Typ 22 600x1000", "Heizung", "Stk", "95.00", "168.00", 20, False, True),
    ("HZ-1102", "Thermostatventil 1/2\"", "Heizung", "Stk", "12.50", "24.90", 20, False, True),
    ("HZ-1103", "Umwälzpumpe 25-60", "Heizung", "Stk", "142.00", "235.00", 20, False, True),
    ("HZ-1104", "Ausdehnungsgefäß 18l", "Heizung", "Stk", "28.00", "52.00", 20, False, True),
    ("HZ-1105", "Sicherheitsventil 3 bar", "Heizung", "Stk", "9.80", "19.90", 20, False, True),
    ("RO-4001", "Kupferrohr 22mm", "Installation", "m", "6.20", "11.50", 20, False, True),
    ("RO-4002", "Pressfitting Bogen 22mm", "Installation", "Stk", "2.10", "4.20", 20, False, True),
    ("RO-4003", "Isolierung 22mm", "Installation", "m", "1.40", "2.90", 20, False, True),
    ("EL-3101", "Kabel YM-J 5x2,5", "Elektro", "m", "1.85", "3.40", 20, False, True),
    ("EL-3102", "FI-Schalter 40A", "Elektro", "Stk", "38.00", "69.00", 20, False, True),
    ("DL-9001", "Montagestunde Geselle", "Dienstleistung", "h", "0.00", "78.00", 20, False, False),
    ("DL-9002", "Montagestunde Meister", "Dienstleistung", "h", "0.00", "95.00", 20, False, False),
    ("DL-9003", "Wartungspauschale Therme", "Dienstleistung", "pausch", "0.00", "189.00", 20,
     False, False),
    ("DL-9004", "Anfahrtspauschale", "Dienstleistung", "pausch", "0.00", "45.00", 20, False, False),
    ("DL-9005", "Reparatur-Kleinmaterial", "Material", "pausch", "8.00", "19.00", 20, False, True),
]


def seed_benutzer(session: Session) -> None:
    for login, name, passwort, rolle, stundensatz in BENUTZER:
        if session.exec(select(Benutzer).where(Benutzer.login == login)).first():
            continue
        session.add(
            Benutzer(
                login=login, name=name, passwort_hash=hash_passwort(passwort), rolle=rolle,
                stundensatz=Decimal(stundensatz),
            )
        )


def seed_lager(session: Session) -> Lager:
    lager = session.exec(select(Lager).where(Lager.ist_standard == True)).first()  # noqa: E712
    if lager is None:
        lager = Lager(name="Hauptlager", ist_standard=True)
        session.add(lager)
        session.flush()
    return lager


def _seed_adresse(session: Session, typ: AdressTyp, daten: tuple, zahlungsziel: int) -> None:
    firma, matchcode, kontakt, strasse, plz, ort, uid, telefon, email, branche, merkmale = daten
    if session.exec(select(Adresse).where(Adresse.firma == firma)).first():
        return
    adresse = Adresse(
        typ=typ, firma=firma, matchcode=matchcode, strasse=strasse, plz=plz, ort=ort,
        uid=uid, telefon=telefon, email=email, branche=branche, merkmale=merkmale,
        zahlungsziel_tage=zahlungsziel, adressnr=adresse_service.naechste_adressnr(session),
        briefanrede="Sehr geehrte Damen und Herren,",
    )
    session.add(adresse)
    session.flush()
    anrede, vorname, nachname, funktion = kontakt
    session.add(
        Ansprechpartner(
            adresse_id=adresse.id, anrede=anrede, vorname=vorname, nachname=nachname,
            funktion=funktion, telefon=telefon, email=email, ist_hauptkontakt=True,
        )
    )
    session.flush()
    adresse_service.synchronisiere_hauptkontakt(session, adresse)


def seed_adressen(session: Session) -> None:
    for daten in KUNDEN:
        _seed_adresse(session, AdressTyp.KUNDE, daten, 14)
    for daten in LIEFERANTEN:
        _seed_adresse(session, AdressTyp.LIEFERANT, daten, 30)
    # Demo: eine Lieferadresse (Baustelle) beim Bauträger
    steiner = _hole_kunde(session, "Wohnbau Steiner GmbH")
    vorhanden = select(Lieferadresse).where(Lieferadresse.adresse_id == steiner.id)
    if not session.exec(vorhanden).first():
        session.add(
            Lieferadresse(
                adresse_id=steiner.id, bezeichnung="Baustelle Wohnpark Süd",
                name="Wohnbau Steiner GmbH, Bauleitung", strasse="Am Wohnpark 1", plz="8020",
                ort="Graz", notiz="Anlieferung Mo–Fr 7–15 Uhr, Polier Hr. Rauch 0664 111222",
            )
        )
        session.flush()


def _kalkulation(ek: Decimal, vk: Decimal) -> dict:
    """Leitet eine konsistente Kalkulationskette aus EK/VK ab (Demo: 30 % EK-Rabatt).

    Listenpreis wird so gewählt, dass mit 30 % Einkaufsrabatt der EK herauskommt;
    der Aufschlag wird aus dem gewünschten VK rückgerechnet. VK bleibt der Fixwert.
    """
    if ek > 0:
        rabatt1 = Decimal("30")
        listenpreis = berechnung.runde(ek / Decimal("0.7"))
        ek_kalk = berechnung.kalkuliere_ek(listenpreis, rabatt1, Decimal("0"))
        aufschlag = berechnung.aufschlag_aus_vk(ek_kalk, vk)
    else:
        rabatt1 = Decimal("0")
        listenpreis = Decimal("0")
        ek_kalk = Decimal("0")
        aufschlag = Decimal("0")
    return {
        "listenpreis_netto": listenpreis,
        "rabatt1_prozent": rabatt1,
        "rabatt2_prozent": Decimal("0"),
        "ek_preis": ek_kalk,
        "aufschlag_prozent": aufschlag,
        "vk_preis": vk,
    }


def seed_artikel(session: Session, lager: Lager) -> None:
    for nr, bez, wg, einheit, ek, vk, ust, sn_pflicht, ist_lager in ARTIKEL:
        if session.exec(select(Artikel).where(Artikel.artikelnr == nr)).first():
            continue
        artikel = Artikel(
            artikelnr=nr, bezeichnung=bez, warengruppe=wg, einheit=einheit,
            ust_satz=ust, seriennummernpflichtig=sn_pflicht, ist_lagerartikel=ist_lager,
            matchcode=bez.split()[0].upper()[:12],
            **_kalkulation(Decimal(ek), Decimal(vk)),
        )
        session.add(artikel)
        session.flush()

        if not ist_lager:
            continue

        startmenge = Decimal("3") if sn_pflicht else Decimal("25")
        session.add(
            Lagerbestand(artikel_id=artikel.id, lager_id=lager.id, menge=startmenge)
        )
        session.add(
            Lagerbewegung(
                artikel_id=artikel.id, lager_id=lager.id, menge=startmenge,
                buchungsart=Buchungsart.KORREKTUR,
            )
        )
        if sn_pflicht:
            for i in range(1, 4):
                session.add(
                    Seriennummer(
                        artikel_id=artikel.id,
                        sn=f"{nr}-SN{i:04d}",
                        status=SnStatus.AUF_LAGER,
                        lager_id=lager.id,
                    )
                )


# (firma, artikelnr, sn, tage relativ zu heute, standort) — Kunden 2 und 4
DEMO_ANLAGEN = [
    ("Bäckerei Brotherz e.U.", "HZ-1002", "HZ-1002-DEMO01", 14, "Heizraum Keller"),
    ("Gasthof Post KG", "KT-2001", "KT-2001-DEMO01", -10, "Gastraum EG"),
]


def seed_bezugsquellen(session: Session) -> None:
    """Demo: Vaillant liefert die Thermen (Bezugsquelle), Kupferrohr hat Staffelpreise."""
    vaillant = _hole_kunde(session, "Vaillant Austria GmbH")
    bezugsquellen = (("HZ-1001", "0010021873", "1450.00"), ("HZ-1002", "0010023456", "5200.00"))
    for nr, lief_nr, ek in bezugsquellen:
        artikel = _hole_artikel(session, nr)
        if artikel_service.lieferant_eintrag(session, artikel.id, vaillant.id) is None:
            artikel_service.setze_lieferant(
                session, artikel, vaillant.id, lieferanten_artikelnr=lief_nr,
                ek_preis=Decimal(ek), lieferzeit_tage=5, ist_hauptlieferant=True,
            )
    rohr = _hole_artikel(session, "RO-4001")
    if not artikel_service.staffeln(session, rohr.id):
        artikel_service.setze_staffel(session, rohr, Decimal("50"), Decimal("10.50"))
        artikel_service.setze_staffel(session, rohr, Decimal("100"), Decimal("9.80"))
    rohr.ean = "9001234567890"
    session.add(rohr)
    session.flush()


def _hole_kunde(session: Session, firma: str) -> Adresse:
    return session.exec(select(Adresse).where(Adresse.firma == firma)).one()


def _hole_artikel(session: Session, artikelnr: str) -> Artikel:
    return session.exec(select(Artikel).where(Artikel.artikelnr == artikelnr)).one()


def _seed_demo_anlagen(session: Session) -> None:
    """Zwei Demo-Anlagen direkt über die Models: eine fällig in 14 Tagen, eine überfällig."""
    for firma, artikelnr, sn_text, tage, standort in DEMO_ANLAGEN:
        kunde = _hole_kunde(session, firma)
        artikel = _hole_artikel(session, artikelnr)
        sn = Seriennummer(
            artikel_id=artikel.id, sn=sn_text, status=SnStatus.BEIM_KUNDEN_VERBAUT
        )
        session.add(sn)
        session.flush()
        session.add(
            Anlage(
                anlagenr=anlagen_service.naechste_anlagenr(session),
                adresse_id=kunde.id,
                artikel_id=artikel.id,
                seriennummer_id=sn.id,
                bezeichnung=artikel.bezeichnung,
                hersteller="Vaillant" if artikelnr.startswith("HZ") else "Daikin",
                typ=artikel.bezeichnung.split()[-1],
                seriennummer_text=sn_text,
                art="Heizung" if artikelnr.startswith("HZ") else "Klima",
                anlagengruppe="Wärmeerzeuger" if artikelnr.startswith("HZ") else "Klimageräte",
                standort=standort,
                inbetriebnahme=date.today() - timedelta(days=365),
                naechste_wartung=date.today() + timedelta(days=tage),
                wartungsintervall_monate=12,
                aktiv=True,
            )
        )
    session.flush()
    # Freie Fremdanlage (kein Lagerartikel) mit Komponenten und Wartungsvertrag
    post = _hole_kunde(session, "Gasthof Post KG")
    fremd = anlagen_service.lege_freie_anlage_an(
        session, adresse_id=post.id,
        bezeichnung="Ölkessel Viessmann Vitola 200, Heizzentrale Nebengebäude",
        hersteller="Viessmann", typ="Vitola 200", seriennummer_text="7183 5546 2001",
        art="Heizung", anlagengruppe="Wärmeerzeuger", untergruppe="Öl", standort="Nebengebäude",
        kostenstelle="HZ-02", technische_info="27 kW, Baujahr 2009, Öltank 3.000 l",
        inbetriebnahme=date(2009, 10, 15), naechste_wartung=date.today() + timedelta(days=45),
        wartungsintervall_monate=12, vertrag_nr="WV-2024-017", vertrag_von=date(2024, 1, 1),
        vertrag_bis=date(2026, 12, 31), vertrag_pauschale=Decimal("290.00"),
        vertrag_notiz="Jahreswartung inkl. Abgasmessung, Anfahrt inklusive",
    )
    for bez, herst, typ, sn_k in (
        ("Ölbrenner", "Weishaupt", "WL10", "WL10-88712"),
        ("Umwälzpumpe", "Grundfos", "Alpha2 25-60", ""),
        ("Regelung", "Viessmann", "Vitotronic 200", "VT200-4471"),
    ):
        anlagen_service.fuege_komponente_hinzu(
            session, fremd, bezeichnung=bez, hersteller=herst, typ=typ, seriennummer_text=sn_k
        )
    session.flush()


def seed_demo_durchlauf(session: Session) -> None:
    """Demo-Belegkette Angebot→Auftrag→Lieferschein→Rechnung über die Services.

    Idempotent: existiert bereits ein Angebot, wird nichts angelegt.
    Der Lieferschein bucht Lager (Hooks!) und verkauft die erste HZ-1001-Seriennummer.
    """
    if session.exec(select(Beleg).where(Beleg.belegart == Belegart.ANGEBOT)).first():
        return
    registriere_lager_hooks()

    kunde = _hole_kunde(session, "Hotel Alpenblick GmbH")
    therme = _hole_artikel(session, "HZ-1001")
    ventil = _hole_artikel(session, "HZ-1102")
    stunde = _hole_artikel(session, "DL-9001")

    angebot = beleg_service.erstelle_beleg(
        session, Belegart.ANGEBOT, kunde,
        betreff="Angebot Heizungstausch Gastherme inkl. Montage",
    )
    beleg_service.fuege_position_hinzu(session, angebot, therme, menge=Decimal("1"))
    beleg_service.fuege_position_hinzu(session, angebot, ventil, menge=Decimal("8"))
    beleg_service.fuege_position_hinzu(session, angebot, stunde, menge=Decimal("6"))
    beleg_service.setze_status(session, angebot, BelegStatus.GEDRUCKT)

    auftrag = beleg_service.erzeuge_folgebeleg(session, angebot)
    auftrag.auftragsart = "liefer"
    angebot.angebotsergebnis = "erhalten"
    session.add(auftrag)
    session.add(angebot)
    beleg_service.setze_status(session, auftrag, BelegStatus.GEDRUCKT)

    lieferschein = beleg_service.erzeuge_folgebeleg(session, auftrag)
    ls_position = session.exec(
        select(BelegPosition).where(
            BelegPosition.beleg_id == lieferschein.id,
            BelegPosition.artikel_id == therme.id,
        )
    ).one()
    sns = seriennummer_service.verfuegbare_sns(session, therme.id)
    seriennummer_service.weise_sns_zu(session, ls_position, [sns[0].id])
    beleg_service.setze_status(session, lieferschein, BelegStatus.GEDRUCKT)  # bucht Lager

    rechnung = beleg_service.erzeuge_folgebeleg(session, lieferschein)
    beleg_service.setze_status(session, rechnung, BelegStatus.GEDRUCKT)

    _seed_demo_anlagen(session)
    _seed_demo_zeiten(session)


# (Login des Mitarbeiters, Tage vor heute, Stunden, Tätigkeit)
DEMO_ZEITEN = [
    ("mo", 2, "4.5", "Therme demontiert, Anschlüsse vorbereitet"),
    ("mo", 1, "7.25", "Neue Therme montiert und in Betrieb genommen"),
    ("sb", 1, "1.5", "Einweisung Kunde, Protokoll erstellt"),
]


def _seed_demo_zeiten(session: Session) -> None:
    """Regie-Auftrag im Entwurf mit drei erfassten Arbeitszeiten.

    Bewusst ein EIGENER Auftrag im Status Entwurf: nur dort lässt sich „Zeiten
    übernehmen" ausprobieren (der Auftrag der Demo-Belegkette ist am Ende erledigt
    und damit unveränderlich).
    """
    kunde = _hole_kunde(session, "Gasthof Post KG")
    material = _hole_artikel(session, "DL-9005")
    auftrag = beleg_service.erstelle_beleg(
        session, Belegart.AUFTRAG, kunde,
        betreff="Regiearbeiten Heizungstausch",
        auftragsart="service",
        kopftext="Regiearbeiten Heizungstausch (Demo für die Arbeitszeiterfassung)",
    )
    beleg_service.fuege_position_hinzu(session, auftrag, material, menge=Decimal("2"))

    for login, tage, stunden, taetigkeit in DEMO_ZEITEN:
        mitarbeiter = session.exec(select(Benutzer).where(Benutzer.login == login)).one()
        zeiterfassung_service.erfasse_zeit(
            session,
            benutzer_id=mitarbeiter.id,
            beleg_id=auftrag.id,
            datum=date.today() - timedelta(days=tage),
            stunden=Decimal(stunden),
            stundensatz=mitarbeiter.stundensatz,
            taetigkeit=taetigkeit,
        )


# (kuerzel, titel, text, kategorie)
TEXTBAUSTEINE = [
    ("gew", "Gewährleistung", "Auf die erbrachte Leistung gewähren wir 2 Jahre "
     "Gewährleistung gemäß den gesetzlichen Bestimmungen.", "fuss"),
    ("zahl", "Zahlungsbedingungen", "Zahlbar innerhalb von 14 Tagen ohne Abzug.", "fuss"),
    ("angb", "Angebotsgültigkeit", "Dieses Angebot ist 30 Tage ab Ausstellungsdatum "
     "gültig.", "kopf"),
    ("mont", "Montagehinweis", "Montage inkl. Anschluss an bestehende Installation, "
     "Inbetriebnahme und Einweisung.", "position"),
    ("ents", "Altgerät-Entsorgung", "Fachgerechte Demontage und Entsorgung des "
     "Altgeräts inbegriffen.", "position"),
]


def seed_textbausteine(session: Session) -> None:
    from app.models import Textbaustein, TextbausteinKategorie

    for kuerzel, titel, text, kategorie in TEXTBAUSTEINE:
        if session.exec(select(Textbaustein).where(Textbaustein.kuerzel == kuerzel)).first():
            continue
        session.add(
            Textbaustein(
                kuerzel=kuerzel, titel=titel, text=text,
                kategorie=TextbausteinKategorie(kategorie),
            )
        )


def seed_set_artikel(session: Session) -> None:
    """Demo-Set „Heizungspaket" aus Therme + Speicher + Montage."""
    from app.models import SetKomponente

    if session.exec(select(Artikel).where(Artikel.artikelnr == "SET-HZ1")).first():
        return
    set_artikel = Artikel(
        artikelnr="SET-HZ1", bezeichnung="Heizungspaket Gastherme komplett",
        warengruppe="Heizung", einheit="Set", ist_set=True, ist_lagerartikel=False,
    )
    session.add(set_artikel)
    session.flush()

    # (Komponenten-Artikelnr, Menge) — Bestandsartikel aus dem Seed
    komponenten = [("HZ-1001", "1"), ("HZ-1003", "1"), ("HZ-1103", "1"), ("DL-9001", "6")]
    for sort, (nr, menge) in enumerate(komponenten):
        komp = session.exec(select(Artikel).where(Artikel.artikelnr == nr)).first()
        if komp is not None:
            session.add(
                SetKomponente(
                    set_artikel_id=set_artikel.id, komponente_artikel_id=komp.id,
                    menge=Decimal(menge), sort=sort,
                )
            )


def seed_rollen(session: Session) -> None:
    """Drei Rollen (idempotent) — bei create_all statt Migration.

    ``recht_zeiterfassung`` steht bei sachbearbeiter UND monteur auf BEARBEITEN:
    beide sollen ihre eigenen Arbeitszeiten erfassen und korrigieren können.
    Fremde Zeiten sieht dadurch niemand — das bleibt dem Admin vorbehalten
    (siehe app/services/zeiterfassung_service.py).
    """
    from app.models import BEREICHE, Rollenprofil, Zugriff

    vorhanden = {r.name for r in session.exec(select(Rollenprofil)).all()}
    if "admin" not in vorhanden:
        session.add(Rollenprofil(
            name="admin", beschreibung="Vollzugriff", ist_admin=True, ist_standard=True,
            darf_kalkulation_sehen=True,
            **{f"recht_{b}": Zugriff.BEARBEITEN for b in BEREICHE},
        ))
    if "sachbearbeiter" not in vorhanden:
        session.add(Rollenprofil(
            name="sachbearbeiter", beschreibung="Alles Operative, keine Einstellungen",
            ist_admin=False, ist_standard=True, darf_kalkulation_sehen=True,
            recht_adressen=Zugriff.BEARBEITEN, recht_artikel=Zugriff.BEARBEITEN,
            recht_verkauf=Zugriff.BEARBEITEN, recht_einkauf=Zugriff.BEARBEITEN,
            recht_lager=Zugriff.BEARBEITEN, recht_anlagen=Zugriff.BEARBEITEN,
            recht_zeiterfassung=Zugriff.BEARBEITEN,
            recht_einstellungen=Zugriff.KEIN, recht_benutzer=Zugriff.KEIN,
        ))
    if "monteur" not in vorhanden:
        # Beispiel für eine eingeschränkte Rolle: nur Service, keine Preise/Marge
        session.add(Rollenprofil(
            name="monteur", beschreibung="Monteur: Anlagen, Service & eigene Arbeitszeiten",
            ist_admin=False, ist_standard=False, darf_kalkulation_sehen=False,
            recht_adressen=Zugriff.ANSEHEN, recht_artikel=Zugriff.ANSEHEN,
            recht_verkauf=Zugriff.KEIN, recht_einkauf=Zugriff.KEIN,
            recht_lager=Zugriff.ANSEHEN, recht_anlagen=Zugriff.BEARBEITEN,
            recht_zeiterfassung=Zugriff.BEARBEITEN,
            recht_einstellungen=Zugriff.KEIN, recht_benutzer=Zugriff.KEIN,
        ))


def run(session: Session) -> None:
    seed_rollen(session)
    seed_benutzer(session)
    lager = seed_lager(session)
    seed_adressen(session)
    seed_artikel(session, lager)
    seed_bezugsquellen(session)
    seed_textbausteine(session)
    seed_set_artikel(session)
    session.commit()
    seed_demo_durchlauf(session)
    session.commit()


def main() -> None:
    with Session(engine) as session:
        run(session)
        anzahl_belege = len(session.exec(select(Beleg)).all())
        anzahl_anlagen = len(session.exec(select(Anlage)).all())
        anzahl_zeiten = len(session.exec(select(Zeiterfassung)).all())
    print("Seed abgeschlossen. Logins: admin/admin, sb/sb, mo/mo")
    print(f"Belege: {anzahl_belege}, Anlagen: {anzahl_anlagen}, Arbeitszeiten: {anzahl_zeiten}")


if __name__ == "__main__":
    main()
