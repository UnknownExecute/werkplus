"""Nebenläufigkeit der Lagerbuchung: kein Lost Update, Retry, fachlicher Fehler.

Der Thread-Test läuft gegen eine echte dateibasierte SQLite-DB (WAL) — nur dort
gibt es überhaupt konkurrierende Schreiber. Muster wie in
``tests/test_nummernkreis.py::test_race_acht_threads_lueckenlos``.
"""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401 — Metadata vollständig registrieren
from app.models.artikel import Artikel
from app.models.benutzer import Benutzer, Rolle
from app.models.lager import Buchungsart, Lager, Lagerbestand, Lagerbewegung
from app.routers import lager as lager_router
from app.services import lager_service
from app.services.auth_service import hash_passwort
from app.services.fehler import LagerbuchungFehlgeschlagen, WerkplusFehler
from app.services.lager_service import bestand, buche

THREADS = 8
BUCHUNGEN_JE_THREAD = 10


@pytest.fixture
def lager(session) -> Lager:
    lager = Lager(name="Hauptlager", ist_standard=True)
    session.add(lager)
    session.flush()
    return lager


@pytest.fixture
def artikel(session) -> Artikel:
    artikel = Artikel(
        artikelnr="A-900",
        bezeichnung="Dichtring 3/4",
        einheit="Stk",
        ek_preis=Decimal("0.40"),
        vk_preis=Decimal("1.20"),
    )
    session.add(artikel)
    session.flush()
    return artikel


# ------------------------------------------------------------------ Lost Update


@pytest.mark.slow
def test_race_acht_threads_kein_lost_update(tmp_path):
    """8 Threads buchen je 10x +1 auf denselben Artikel/Lager — Bestand muss exakt 80 sein."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lager_race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    with Session(engine) as vorbereitung:
        lager = Lager(name="Hauptlager", ist_standard=True)
        artikel = Artikel(artikelnr="A-901", bezeichnung="Kugelhahn", einheit="Stk")
        vorbereitung.add(lager)
        vorbereitung.add(artikel)
        vorbereitung.commit()
        lager_id = lager.id
        artikel_id = artikel.id

    fehler: list[Exception] = []
    sperre = threading.Lock()
    start = threading.Barrier(THREADS)

    def worker():
        try:
            start.wait()
            with Session(engine) as thread_session:
                for _ in range(BUCHUNGEN_JE_THREAD):
                    buche(
                        thread_session,
                        artikel_id=artikel_id,
                        lager_id=lager_id,
                        menge=Decimal("1"),
                        buchungsart=Buchungsart.WARENEINGANG,
                    )
                    thread_session.commit()  # sofortiger Commit je Buchung
        except Exception as exc:  # noqa: BLE001 — im Test gesammelt und geprüft
            with sperre:
                fehler.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    erwartet = Decimal(THREADS * BUCHUNGEN_JE_THREAD)
    with Session(engine) as pruefung:
        zeilen = pruefung.exec(
            select(Lagerbestand).where(Lagerbestand.artikel_id == artikel_id)
        ).all()
        journal = pruefung.exec(
            select(Lagerbewegung).where(Lagerbewegung.artikel_id == artikel_id)
        ).all()
    engine.dispose()

    assert fehler == []
    assert len(zeilen) == 1, "Upsert muss genau eine Bestandszeile je Artikel+Lager halten"
    assert zeilen[0].menge == erwartet, "Lost Update: Bestand stimmt nicht mit den Buchungen"
    assert len(journal) == THREADS * BUCHUNGEN_JE_THREAD


@pytest.mark.slow
def test_race_dezimalstellen_bleiben_exakt(tmp_path):
    """Nebenläufige Buchungen mit Dezimalstellen dürfen nicht über Float rechnen."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lager_dez.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    with Session(engine) as vorbereitung:
        lager = Lager(name="Hauptlager", ist_standard=True)
        artikel = Artikel(artikelnr="A-902", bezeichnung="Kupferrohr", einheit="m")
        vorbereitung.add(lager)
        vorbereitung.add(artikel)
        vorbereitung.commit()
        lager_id = lager.id
        artikel_id = artikel.id

    fehler: list[Exception] = []
    sperre = threading.Lock()
    start = threading.Barrier(4)

    def worker():
        try:
            start.wait()
            with Session(engine) as thread_session:
                for _ in range(10):
                    buche(
                        thread_session,
                        artikel_id=artikel_id,
                        lager_id=lager_id,
                        menge=Decimal("0.1"),
                        buchungsart=Buchungsart.KORREKTUR,
                    )
                    thread_session.commit()
        except Exception as exc:  # noqa: BLE001
            with sperre:
                fehler.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    with Session(engine) as pruefung:
        zeile = pruefung.exec(
            select(Lagerbestand).where(Lagerbestand.artikel_id == artikel_id)
        ).one()
        roh = pruefung.execute(
            text("SELECT menge FROM lagerbestand WHERE artikel_id = :a"), {"a": artikel_id}
        ).scalar_one()
    engine.dispose()

    assert fehler == []
    # 40 x 0.1 == 4.0 exakt — mit Float-Arithmetik in SQL käme 3.9999999999999996 heraus
    assert zeile.menge == Decimal("4.0")
    assert roh == "4.0", f"Bestand darf nicht als Float-Text gespeichert werden: {roh!r}"


# ------------------------------------------------------------------ Retry / Fehlerfall


def _lock_fehler() -> OperationalError:
    return OperationalError("UPDATE lagerbestand", None, Exception("database is locked"))


def test_retry_uebersteht_transienten_lock(session, lager, artikel, monkeypatch):
    """Ein "database is locked" wird nach Backoff erfolgreich wiederholt."""
    monkeypatch.setattr(lager_service, "_BACKOFF_BASIS_SEK", 0.0)
    echte_execute = session.execute
    aufrufe = {"n": 0}

    def wackelig(*args, **kwargs):
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            raise _lock_fehler()
        return echte_execute(*args, **kwargs)

    monkeypatch.setattr(session, "execute", wackelig)

    buche(
        session,
        artikel_id=artikel.id,
        lager_id=lager.id,
        menge=Decimal("5"),
        buchungsart=Buchungsart.WARENEINGANG,
    )
    monkeypatch.undo()

    assert aufrufe["n"] >= 2, "es muss mindestens ein Wiederholungsversuch stattgefunden haben"
    assert bestand(session, artikel.id, lager_id=lager.id) == Decimal("5")


def test_fachlicher_fehler_nach_erschoepften_versuchen(session, lager, artikel, monkeypatch):
    """Dauerlock → LagerbuchungFehlgeschlagen (WerkplusFehler), kein nackter OperationalError."""
    monkeypatch.setattr(lager_service, "_BACKOFF_BASIS_SEK", 0.0)
    monkeypatch.setattr(lager_service, "_MAX_VERSUCHE", 3)
    versuche = {"n": 0}

    def immer_gesperrt(*args, **kwargs):
        versuche["n"] += 1
        raise _lock_fehler()

    monkeypatch.setattr(session, "execute", immer_gesperrt)

    with pytest.raises(LagerbuchungFehlgeschlagen) as info:
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("1"),
            buchungsart=Buchungsart.KORREKTUR,
        )
    monkeypatch.undo()

    assert versuche["n"] == 3, "es muss genau _MAX_VERSUCHE-mal versucht werden"
    assert isinstance(info.value, WerkplusFehler)
    assert isinstance(info.value.__cause__, OperationalError)
    assert "erneut versuchen" in str(info.value)


def test_kein_journaleintrag_wenn_bestand_nicht_fortgeschrieben(
    session, lager, artikel, monkeypatch
):
    """Scheitert die Bestandsfortschreibung, entsteht auch keine Lagerbewegung."""
    monkeypatch.setattr(lager_service, "_BACKOFF_BASIS_SEK", 0.0)
    monkeypatch.setattr(lager_service, "_MAX_VERSUCHE", 2)

    def immer_gesperrt(*args, **kwargs):
        raise _lock_fehler()

    monkeypatch.setattr(session, "execute", immer_gesperrt)

    with pytest.raises(LagerbuchungFehlgeschlagen):
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("2"),
            buchungsart=Buchungsart.KORREKTUR,
        )
    monkeypatch.undo()

    assert session.exec(select(Lagerbewegung)).all() == []


def test_fremder_operational_error_wird_nicht_verschluckt(session, lager, artikel, monkeypatch):
    """Nur "database is locked" wird wiederholt — andere DB-Fehler steigen unverändert auf."""
    monkeypatch.setattr(lager_service, "_BACKOFF_BASIS_SEK", 0.0)

    def anderer_fehler(*args, **kwargs):
        raise OperationalError("INSERT", None, Exception("no such table: lagerbestand"))

    monkeypatch.setattr(session, "execute", anderer_fehler)

    with pytest.raises(OperationalError):
        buche(
            session,
            artikel_id=artikel.id,
            lager_id=lager.id,
            menge=Decimal("1"),
            buchungsart=Buchungsart.KORREKTUR,
        )
    monkeypatch.undo()


def test_verlorener_compare_and_swap_wird_wiederholt(session, lager, artikel, monkeypatch):
    """Fremdschreiber zwischen Lesen und Schreiben: CAS greift nicht, Retry rechnet neu.

    Simuliert über ein UPDATE, das beim ersten Mal nicht ausgeführt wird und
    rowcount=0 meldet (genau das Signal "jemand anderes war schneller").
    """
    monkeypatch.setattr(lager_service, "_BACKOFF_BASIS_SEK", 0.0)
    buche(
        session,
        artikel_id=artikel.id,
        lager_id=lager.id,
        menge=Decimal("4"),
        buchungsart=Buchungsart.WARENEINGANG,
    )

    echte_execute = session.execute
    zustand = {"updates": 0}

    class NullRowcount:
        rowcount = 0

    def cas_verliert_einmal(statement, *args, **kwargs):
        if str(statement).lstrip().upper().startswith("UPDATE"):
            zustand["updates"] += 1
            if zustand["updates"] == 1:
                return NullRowcount()  # UPDATE bewusst NICHT ausführen
        return echte_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", cas_verliert_einmal)
    buche(
        session,
        artikel_id=artikel.id,
        lager_id=lager.id,
        menge=Decimal("3"),
        buchungsart=Buchungsart.KORREKTUR,
    )
    monkeypatch.undo()

    assert zustand["updates"] == 2, "der verlorene CAS muss genau einmal wiederholt worden sein"
    # Kein doppeltes Delta: das Delta wird immer auf den frisch gelesenen Ist-Wert
    # angewendet, nie mehrfach addiert.
    assert bestand(session, artikel.id, lager_id=lager.id) == Decimal("7")


# ------------------------------------------------------------------ Aufrufer (kein 500)


def test_korrektur_route_meldet_fachlich_statt_500(engine, client, monkeypatch):
    """/lager/korrektur zeigt bei Nebenläufigkeitsfehler das Formular mit Hinweis (kein 500)."""
    with Session(engine) as vorbereitung:
        vorbereitung.add(
            Benutzer(
                login="tester",
                name="Tester",
                passwort_hash=hash_passwort("geheim"),
                rolle=Rolle.SACHBEARBEITER,
                aktiv=True,
            )
        )
        lager = Lager(name="Hauptlager", ist_standard=True)
        artikel = Artikel(artikelnr="A-903", bezeichnung="Muffe", einheit="Stk")
        vorbereitung.add(lager)
        vorbereitung.add(artikel)
        vorbereitung.commit()
        lager_id, artikel_id = lager.id, artikel.id

    antwort = client.post(
        "/login", data={"login": "tester", "passwort": "geheim"}, follow_redirects=False
    )
    assert antwort.status_code == 302

    def buche_scheitert(*args, **kwargs):
        raise LagerbuchungFehlgeschlagen("Datenbank belegt — bitte erneut versuchen")

    monkeypatch.setattr(lager_router.lager_service, "buche", buche_scheitert)

    antwort = client.post(
        "/lager/korrektur",
        data={
            "artikel_id": artikel_id,
            "lager_id": lager_id,
            "korrekturart": "differenz",
            "menge": "5",
            "grund": "Inventur",
        },
        follow_redirects=False,
    )
    assert antwort.status_code == 200  # kein 302 (Erfolg) und kein 500
    assert "bitte erneut versuchen" in antwort.text
    assert "Bestandskorrektur" in antwort.text

    with Session(engine) as pruefung:
        assert pruefung.exec(select(Lagerbewegung)).all() == []
