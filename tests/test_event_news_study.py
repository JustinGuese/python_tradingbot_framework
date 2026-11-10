"""
Pure unit tests for stock news event study (Step E2).
No DB access, no network access.
"""

from __future__ import annotations

import pandas as pd

from scripts.onetime_event_news_study import (
    assign_terciles,
    dedupe_headlines,
    score_headline,
)


def test_lexicon_scoring_on_fixed_headlines():
    """
    Test sentiment scoring on 4 fixed headlines, specifically testing:
    1. Positive headline
    2. Negative headline
    3. Negated positive headline (e.g., 'not beat')
    4. Neutral headline
    """
    # 1. Positive headline
    pos_headline = "Apple beats revenue expectations and surges to record high"
    pos_score = score_headline(pos_headline)
    assert pos_score > 0, f"Expected positive score, got {pos_score}"

    # 2. Negative headline
    neg_headline = "Tesla misses delivery target and stock plunges on weak outlook"
    neg_score = score_headline(neg_headline)
    assert neg_score < 0, f"Expected negative score, got {neg_score}"

    # 3. Negated positive headline (e.g., 'not beat' / 'fails to beat')
    negated_headline_1 = "Intel does not beat quarterly profit estimates"
    negated_score_1 = score_headline(negated_headline_1)
    assert negated_score_1 < 0, f"Expected negative score for 'not beat', got {negated_score_1}"

    negated_headline_2 = "Boeing fails to beat delivery forecast"
    negated_score_2 = score_headline(negated_headline_2)
    assert negated_score_2 < 0, f"Expected negative score for 'fails to beat', got {negated_score_2}"

    # 4. Neutral headline
    neutral_headline = "General Motors holds annual shareholder meeting in Detroit"
    neutral_score = score_headline(neutral_headline)
    assert neutral_score == 0, f"Expected zero score for neutral headline, got {neutral_score}"


def test_dedupe_identical_titles_per_symbol_per_day():
    """
    Dedupe identical titles per symbol per day.
    Keeps first occurrence, preserves distinct days and distinct symbols.
    """
    raw_data = [
        # Same symbol, same day, identical title -> duplicate
        {"symbol": "AAPL", "published_at": "2026-03-01 10:00:00+00:00", "title": "Apple surges on AI"},
        {"symbol": "AAPL", "published_at": "2026-03-01 14:30:00+00:00", "title": "Apple surges on AI  "},
        # Same symbol, different day, identical title -> keep both
        {"symbol": "AAPL", "published_at": "2026-03-02 10:00:00+00:00", "title": "Apple surges on AI"},
        # Different symbol, same day, identical title -> keep both
        {"symbol": "MSFT", "published_at": "2026-03-01 10:00:00+00:00", "title": "Apple surges on AI"},
        # Same symbol, same day, different title -> keep both
        {"symbol": "AAPL", "published_at": "2026-03-01 11:00:00+00:00", "title": "Apple announces buyback"},
    ]
    df = pd.DataFrame(raw_data)
    deduped = dedupe_headlines(df)

    assert len(deduped) == 4, f"Expected 4 rows after deduplication, got {len(deduped)}"

    # First row kept, second row dropped
    assert deduped.iloc[0]["published_at"] == "2026-03-01 10:00:00+00:00"
    assert deduped.iloc[1]["published_at"] == "2026-03-02 10:00:00+00:00"
    assert deduped.iloc[2]["symbol"] == "MSFT"
    assert deduped.iloc[3]["title"] == "Apple announces buyback"


def test_assign_terciles():
    """Verify headlines are split into terciles by score."""
    df = pd.DataFrame(
        {
            "score": [-3, -1, 0, 0, 1, 3],
            "title": ["t1", "t2", "t3", "t4", "t5", "t6"],
        }
    )
    res = assign_terciles(df)
    assert "tercile" in res.columns
    counts = res["tercile"].value_counts()
    assert counts["bottom"] == 2
    assert counts["middle"] == 2
    assert counts["top"] == 2
