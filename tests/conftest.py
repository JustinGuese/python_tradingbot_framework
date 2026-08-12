"""
Shared pytest fixtures.

The import root is configured in pyproject.toml ([tool.pytest.ini_options]
pythonpath) and is the repo root *only*, so every module is reachable under
exactly one name: `tradingbot.X`.

This used to also put `tradingbot/` itself on sys.path, because bots and
livetrade modules imported each other rootlessly (`from utils.X`). Under two
roots the same file loads twice as two unrelated module objects — two `Bot`
classes that fail `isinstance`, and two SQLAlchemy `Base` registries, so
`create_all()` on one would not create the tables the other declared. The
string-path patches below (`"tradingbot.utils...."`) only bind to one of the
two copies, so a test could patch the session and still reach real Postgres.
tests/test_imports.py guards against the second root coming back.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.data_service import DataService
from tradingbot.utils.db import Base

# Modules that call get_db_session() directly rather than accepting a session
# argument. Each did `from .db import get_db_session`, so the name lives in the
# importing module's namespace and has to be patched there, not on db.
_SESSION_CONSUMERS = (
    "tradingbot.utils.portfolio_manager",
    "tradingbot.utils.bot_repository",
)


@pytest.fixture
def test_engine():
    """
    A throwaway in-memory SQLite engine, one per test.

    StaticPool + check_same_thread=False keeps every connection pointed at the
    same in-memory database; the default pool would hand out a fresh (empty)
    database to each connection, so a session opened inside the code under test
    would not see rows the fixture just wrote.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(test_engine):
    """A session on the throwaway database. The engine dies with the test, so
    there is nothing to tear down between tests."""
    session = sessionmaker(bind=test_engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sqlite_db(test_engine, monkeypatch):
    """
    Point the code under test at the throwaway database.

    PortfolioManager.rebalance_portfolio and several BotRepository helpers open
    their own transaction instead of accepting one, so redirecting the session
    factory is the only way to keep them off a real Postgres. Without this a
    test silently connects to whatever POSTGRES_URI points at.

    Note SQLite ignores SELECT ... FOR UPDATE, so get_bot_locked runs here but
    its locking is NOT covered by these tests.
    """
    Session = sessionmaker(bind=test_engine)

    @contextmanager
    def _session():
        session = Session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    for module in _SESSION_CONSUMERS:
        monkeypatch.setattr(f"{module}.get_db_session", _session)
    return _session


@pytest.fixture
def mock_data_service(mocker):
    """Provide a DataService with mocked yfinance calls."""
    service = DataService()
    # Mock yf.download
    mocker.patch("yfinance.download")
    return service


@pytest.fixture
def test_bot(db_session):
    """Create a test bot in the database."""
    bot_name = "TestBot"
    bot = BotRepository.create_or_get_bot(bot_name, session=db_session)
    db_session.commit()
    return bot
