import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401 — Metadata vollständig registrieren
from app.database import get_session
from app.main import app as fastapi_app
from app.services import beleg_service


@pytest.fixture(autouse=True)
def _hook_registries_isolieren():
    """Buchungs-Hooks je Test zurücksetzen (app.main registriert sie beim Import).

    Tests, die Lagerwirkung brauchen, registrieren die Hooks selbst — so ist
    jeder Test unabhängig von Ausführungsreihenfolge und Importseiteneffekten.
    """
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()
    yield
    beleg_service._buchungs_hooks.clear()
    beleg_service._storno_hooks.clear()


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine):
    def _test_session():
        with Session(engine) as session:
            yield session

    fastapi_app.dependency_overrides[get_session] = _test_session
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()
