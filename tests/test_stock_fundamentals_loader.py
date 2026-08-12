"""Regression tests for the news/earnings/insider loader's timestamp handling.

These pin the bug that froze `stock_news`, `stock_earnings` and
`stock_insider_trades` at their first successful batch (2026-02-02) while every
subsequent nightly run reported thousands of rows "added":

`transaction_date` / `report_date` were built timezone-AWARE, but every datetime
column in `db.py` is a bare `DateTime`, so Postgres stores TIMESTAMP WITHOUT TIME
ZONE and hands rows back NAIVE. The dedup sets are keyed on those datetimes, so a
key built from freshly fetched data never matched the key built from the stored
row. Every row looked new on every run, the insert hit
`uq_stock_insider_trades_key`, and the single end-of-run commit rolled back all
three tables for all symbols.

The invariant worth protecting is narrow and mechanical: anything this module
puts in a dedup key, or writes to a datetime column, must be naive UTC.
"""

from datetime import UTC, datetime, timedelta, timezone

import pandas as pd

from tradingbot.utils.stock_fundamentals_loader import (
    _insider_key,
    _naive_utc,
    _news_fields,
    _published_at_from_unix,
)

# Verbatim shape of a real yfinance get_news() entry, captured 2026-08-12. Every
# field the loader wants now lives under "content"; the flat keys it used to read
# are simply absent.
NESTED_NEWS_ITEM = {
    "id": "abc123",
    "content": {
        "title": "Forget big tech: the real AI money is in plumbing",
        "canonicalUrl": {
            "url": "https://finance.yahoo.com/video/forget-big-tech-real-ai-100000112.html",
            "site": "finance",
        },
        "clickThroughUrl": {"url": "https://finance.yahoo.com/video/other.html"},
        "previewUrl": None,
        "pubDate": "2026-08-12T10:00:00Z",
        "displayTime": "",
        "provider": {"displayName": "Yahoo Finance Video", "url": "https://finance.yahoo.com/"},
    },
}

# The pre-2024 flat shape, still handled so a schema flip back cannot silently
# empty the table again.
FLAT_NEWS_ITEM = {
    "title": "Legacy headline",
    "link": "https://example.com/legacy",
    "publisher": "Reuters",
    "publisher_url": "https://reuters.com",
    "date": 1786528800,
    "related_tickers": ["AAPL", "MSFT"],
}


class TestNewsFieldShapes:
    """stock_news held zero rows for its entire existence.

    yfinance nested every field under "content", so `link` resolved to "" and the
    loader skipped every article. StockNewsSentimentBot reads this table as its
    whole strategy, so it has never had anything to act on.
    """

    def test_nested_payload_resolves_every_field(self):
        f = _news_fields(NESTED_NEWS_ITEM)
        assert f["link"] == "https://finance.yahoo.com/video/forget-big-tech-real-ai-100000112.html"
        assert f["title"] == "Forget big tech: the real AI money is in plumbing"
        assert f["publisher"] == "Yahoo Finance Video"
        assert f["publisher_url"] == "https://finance.yahoo.com/"
        assert f["published_raw"] == "2026-08-12T10:00:00Z"

    def test_nested_link_is_non_empty(self):
        """The single condition that silently emptied the table."""
        assert _news_fields(NESTED_NEWS_ITEM)["link"], "empty link means the article is skipped"

    def test_empty_display_time_falls_through_to_pubdate(self):
        """displayTime is often "" — a naive `or` chain ordering would lose the date."""
        assert _news_fields(NESTED_NEWS_ITEM)["published_raw"] == "2026-08-12T10:00:00Z"

    def test_nested_pubdate_parses_to_naive_utc(self):
        published = _published_at_from_unix(_news_fields(NESTED_NEWS_ITEM)["published_raw"])
        assert published.tzinfo is None
        assert published == datetime(2026, 8, 12, 10, 0)

    def test_flat_payload_still_works(self):
        f = _news_fields(FLAT_NEWS_ITEM)
        assert f["link"] == "https://example.com/legacy"
        assert f["title"] == "Legacy headline"
        assert f["publisher"] == "Reuters"
        assert f["related_tickers"] == ["AAPL", "MSFT"]

    def test_unusable_item_yields_empty_link(self):
        """No URL anywhere -> caller skips it rather than writing a blank row."""
        assert _news_fields({"content": {"title": "no url"}})["link"] == ""
        assert _news_fields({})["link"] == ""

    def test_falls_back_through_url_candidates(self):
        """canonicalUrl missing -> clickThroughUrl is used rather than giving up."""
        item = {"content": {"clickThroughUrl": {"url": "https://example.com/click"}}}
        assert _news_fields(item)["link"] == "https://example.com/click"

    def test_constructed_row_carries_every_resolved_field(self, monkeypatch):
        """Asserts on the StockNews object, not just the extraction helper.

        Checking `_news_fields` alone is not enough: the row constructor has its
        own set of lookups, and publisher/publisher_url were still reading the
        dead flat keys after the extractor was fixed — so articles loaded with a
        NULL publisher. Only building the row catches that.
        """
        import tradingbot.utils.stock_fundamentals_loader as loader

        class FakeTicker:
            def __init__(self, symbol):
                pass

            def get_news(self, count, tab):
                return [NESTED_NEWS_ITEM]

        monkeypatch.setattr(loader.yf, "Ticker", FakeTicker)

        rows = loader._load_news_for_symbol("AAPL", set())
        assert len(rows) == 1
        row = rows[0]
        assert row.symbol == "AAPL"
        assert row.title == "Forget big tech: the real AI money is in plumbing"
        assert row.link.endswith("forget-big-tech-real-ai-100000112.html")
        assert row.publisher == "Yahoo Finance Video", "publisher must not fall back to the dead flat key"
        assert row.publisher_url == "https://finance.yahoo.com/"
        assert row.published_at == datetime(2026, 8, 12, 10, 0)
        assert row.published_at.tzinfo is None


class TestNaiveUtc:
    def test_strips_tzinfo(self):
        assert _naive_utc(datetime(2026, 6, 16, 12, 30, tzinfo=UTC)).tzinfo is None

    def test_converts_before_stripping(self):
        """A non-UTC input must be SHIFTED to UTC, not just truncated.

        Truncating +02:00 would store 12:30 for what is really 10:30 UTC — a
        silent two-hour error that no constraint would ever catch.
        """
        berlin = timezone(timedelta(hours=2))
        assert _naive_utc(datetime(2026, 6, 16, 12, 30, tzinfo=berlin)) == datetime(2026, 6, 16, 10, 30)

    def test_naive_input_is_treated_as_utc_and_unchanged(self):
        assert _naive_utc(datetime(2026, 6, 16, 12, 30)) == datetime(2026, 6, 16, 12, 30)

    def test_accepts_pandas_timestamp(self):
        out = _naive_utc(pd.Timestamp("2026-06-16 12:30", tz="UTC"))
        assert out.tzinfo is None and out == datetime(2026, 6, 16, 12, 30)

    def test_published_at_is_naive_for_every_input_form(self):
        """All three branches — None, unix int, parseable value — must agree."""
        unix = int(datetime(2026, 6, 16, 12, 30, tzinfo=UTC).timestamp())
        assert _published_at_from_unix(None).tzinfo is None
        assert _published_at_from_unix(unix) == datetime(2026, 6, 16, 12, 30)
        assert _published_at_from_unix("2026-06-16 12:30").tzinfo is None


class TestInsiderKeyMatchesStoredRows:
    def test_fetched_key_equals_stored_key(self):
        """The exact failure: fetched (aware) vs stored (naive) must collide.

        `_naive_utc` is what the loader now applies to the fetched value; the
        naive datetime is what a SELECT returns for a TIMESTAMP WITHOUT TIME ZONE
        column. If these two keys differ, the row is re-inserted and the run dies
        on the unique constraint.
        """
        fetched = _insider_key("AAPL", _naive_utc(datetime(2025, 11, 12, tzinfo=UTC)), "ADAMS KATHERINE L", "", 3750)
        stored = _insider_key("AAPL", datetime(2025, 11, 12), "ADAMS KATHERINE L", "", 3750)
        assert fetched == stored

    def test_aware_key_would_not_have_matched(self):
        """Guards the test above from passing vacuously.

        If this ever starts failing, naive and aware datetimes have become
        comparable and the regression test upstream no longer proves anything.
        """
        aware = _insider_key("AAPL", datetime(2025, 11, 12, tzinfo=UTC), "ADAMS KATHERINE L", "", 3750)
        stored = _insider_key("AAPL", datetime(2025, 11, 12), "ADAMS KATHERINE L", "", 3750)
        assert aware != stored

    def test_dedup_set_membership_holds(self):
        """End-to-end shape of the bug: the set lookup the loader actually does."""
        existing = {_insider_key("AAPL", datetime(2025, 11, 12), "ADAMS KATHERINE L", "", 3750)}
        incoming = _insider_key("AAPL", _naive_utc(pd.Timestamp("2025-11-12", tz="UTC")), "ADAMS KATHERINE L", "", 3750)
        assert incoming in existing, "row would be re-inserted and violate uq_stock_insider_trades_key"

    def test_loader_emits_naive_dates_from_aware_yfinance_data(self, monkeypatch):
        """The test that actually pins the call site.

        The checks above all invoke `_naive_utc` themselves, so they would still
        pass if someone reverted `_load_insider_for_symbol` to the aware form.
        This one feeds the loader the tz-aware frame yfinance really returns and
        asserts on what comes out — both the row written to the DB and the key
        added to the dedup set.
        """
        import tradingbot.utils.stock_fundamentals_loader as loader

        df = pd.DataFrame(
            {
                "Start Date": [pd.Timestamp("2025-11-12", tz="UTC")],
                "Insider": ["ADAMS KATHERINE L"],
                "Transaction": [""],
                "Shares": [3750.0],
                "Value": [34236.0],
            }
        )

        class FakeTicker:
            def __init__(self, symbol):
                self.insider_transactions = df

        monkeypatch.setattr(loader.yf, "Ticker", FakeTicker)

        seen: set[tuple] = set()
        rows = loader._load_insider_for_symbol("AAPL", seen)

        assert len(rows) == 1
        assert rows[0].transaction_date.tzinfo is None, "aware datetime would be re-inserted every run"
        assert rows[0].transaction_date == datetime(2025, 11, 12)

        # And the second run must skip it, which is the whole point.
        stored_key = _insider_key("AAPL", datetime(2025, 11, 12), "ADAMS KATHERINE L", "", 3750.0)
        assert stored_key in seen
        assert loader._load_insider_for_symbol("AAPL", {stored_key}) == []

    def test_none_fields_normalize(self):
        """yfinance omits transaction_type for many rows; None and '' are one key."""
        assert _insider_key("AAPL", datetime(2025, 11, 12), None, None, None) == (
            "AAPL",
            datetime(2025, 11, 12),
            "",
            "",
            0.0,
        )
