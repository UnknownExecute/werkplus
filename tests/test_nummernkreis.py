"""Tests des Nummernkreis-Services: Format, Jahreskreise, Race-Sicherheit."""

import threading

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401 — Metadata vollständig registrieren
from app.models.beleg import Belegart
from app.models.nummernkreis import Nummernkreis
from app.services.fehler import UngueltigeBelegart, WerkplusFehler
from app.services.nummernkreis_service import (
    formatiere_belegnr,
    naechste_belegnr,
    setze_stand,
    staende,
)


def test_format_erste_nummer(session):
    """Kplus-Format: zweistelliges Jahr + laufende Nummer, kein Präfix."""
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "260001"
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "260002"


def test_eigener_zaehler_je_belegart(session):
    """Jede Belegart zählt für sich — Angebot und Rechnung dürfen dieselbe Nummer tragen."""
    for belegart in Belegart:
        assert naechste_belegnr(session, belegart, jahr=2026) == "260001"
    assert naechste_belegnr(session, Belegart.ANGEBOT, jahr=2026) == "260002"
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "260002"


def test_mehr_als_vier_stellen(session):
    session.add(Nummernkreis(belegart="rechnung", jahr=2026, praefix="", letzte_nummer=9999))
    session.flush()
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "2610000"


def test_jahreswechsel_getrennte_kreise(session):
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2025) == "250001"
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "260001"
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2025) == "250002"
    kreise = session.exec(select(Nummernkreis).where(Nummernkreis.belegart == "rechnung")).all()
    assert {(k.jahr, k.letzte_nummer) for k in kreise} == {(2025, 2), (2026, 1)}


def test_ungueltige_belegart(session):
    with pytest.raises(UngueltigeBelegart):
        naechste_belegnr(session, "kassabon", jahr=2026)  # type: ignore[arg-type]


def test_formatiere_belegnr():
    assert formatiere_belegnr(2026, 1) == "260001"
    assert formatiere_belegnr(2031, 123) == "310123"
    assert formatiere_belegnr(2026, 12345) == "2612345"


def test_stand_setzen_schliesst_an_alte_nummerierung_an(session):
    """Einstellungen: Startwert setzen, damit die Nummern nahtlos an Kplus anschließen."""
    assert staende(session, 2026)[Belegart.ANGEBOT] == 0
    setze_stand(session, Belegart.ANGEBOT, 2026, 23031)
    assert staende(session, 2026)[Belegart.ANGEBOT] == 23031
    assert naechste_belegnr(session, Belegart.ANGEBOT, jahr=2026) == "2623032"
    # andere Belegarten unberührt
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "260001"


def test_stand_nicht_unter_vergebene_nummer(session):
    naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026)
    naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026)
    with pytest.raises(WerkplusFehler):
        setze_stand(session, Belegart.RECHNUNG, 2026, 1)
    with pytest.raises(WerkplusFehler):
        setze_stand(session, Belegart.RECHNUNG, 2026, -1)
    setze_stand(session, Belegart.RECHNUNG, 2026, 2)  # gleich bleiben ist erlaubt
    assert naechste_belegnr(session, Belegart.RECHNUNG, jahr=2026) == "260003"


@pytest.mark.slow
def test_race_acht_threads_lueckenlos(tmp_path):
    """8 Threads ziehen je 25 Nummern parallel — alle 200 eindeutig und lückenlos."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    nummern: list[str] = []
    fehler: list[Exception] = []
    sperre = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        try:
            start.wait()
            with Session(engine) as thread_session:
                for _ in range(25):
                    nr = naechste_belegnr(thread_session, Belegart.RECHNUNG, jahr=2026)
                    thread_session.commit()  # sofortiger Commit je Nummer
                    with sperre:
                        nummern.append(nr)
        except Exception as exc:  # noqa: BLE001 — im Test gesammelt und geprüft
            with sperre:
                fehler.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    engine.dispose()

    assert fehler == []
    assert len(nummern) == 200
    assert len(set(nummern)) == 200, "Belegnummern müssen eindeutig sein"
    laufende = sorted(int(nr[2:]) for nr in nummern)
    assert laufende == list(range(1, 201)), "Belegnummern müssen lückenlos sein"
