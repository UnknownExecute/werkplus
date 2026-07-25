"""Engine (SQLite mit WAL-Mode) und Session-Dependency."""

from collections.abc import Generator

from sqlalchemy import event
from sqlmodel import Session, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
