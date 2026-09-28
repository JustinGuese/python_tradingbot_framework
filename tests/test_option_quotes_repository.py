"""utils/option_quotes_repository.py: the ON CONFLICT DO NOTHING bulk-insert path.

The actual insert statement uses sqlalchemy.dialects.postgresql.insert(), which
SQLite can't execute (see tests/test_historic_interval.py for the same
constraint on HistoricDataRepository) -- so only the pure validation and the
empty-input short-circuit are covered here, not a real DB round trip.
"""

import pytest

from tradingbot.utils.option_quotes_repository import bulk_insert_quotes


def test_empty_rows_is_a_noop():
    assert bulk_insert_quotes([]) == 0


def test_rejects_rows_missing_required_keys():
    with pytest.raises(ValueError, match="contract_symbol"):
        bulk_insert_quotes([{"underlying": "AAPL"}])
