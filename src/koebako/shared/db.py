"""SQLite engine, sessions, and table initialization."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from koebako.settings import DATABASE_PATH
from koebako.shared.models.note import Note  # noqa: F401
from koebako.shared.models.notification import NotificationOutbox  # noqa: F401

engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


def create_db_and_tables() -> None:
    """Create the storage directory and tables, then enable SQLite WAL mode."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")


def get_session() -> Generator[Session, None, None]:
    """Yield a database session and close it after the request."""
    with Session(engine) as session:
        yield session


def close_db() -> None:
    """Dispose the SQLite connection pool."""
    engine.dispose()
