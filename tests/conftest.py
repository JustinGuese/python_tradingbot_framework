import sys
from pathlib import Path

# Put tradingbot/ on sys.path so modules using `from utils.X` (the in-container
# import style used by bots and livetrade) also resolve when pytest runs from
# the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tradingbot"))

import pytest
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tradingbot.utils.db import Base
from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.data_service import DataService


@pytest.fixture(scope="session")
def test_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(test_engine):
    """Provide a clean session for each test."""
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()
    # Clean up tables between tests if needed, or just rely on separate sessions
    for table in reversed(Base.metadata.sorted_tables):
        test_engine.execute(table.delete())


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
    return bot
