"""
The indicator catalogue must match what `ta` actually emits.

Saved strategy specs reference these column names by string. If a `ta` upgrade
renames or drops a column, every spec using it silently stops firing — the
condition just evaluates False forever, which looks like a bad strategy rather
than a broken one. This test turns that into a failed build instead.
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.indicator_catalog import (
    CURATED,
    CURATED_INFO,
    GROUPS,
    PRICE_FIELDS,
    PRICE_INFO,
    TA_COLUMN_ORDER,
    TA_COLUMNS,
    UNITS,
    curated_payload,
)

OHLCV = ("open", "high", "low", "close", "volume")


@pytest.fixture(scope="module")
def emitted_columns():
    """The TA columns ta.add_all_ta_features() adds, in emission order."""
    from ta import add_all_ta_features

    bars = 300
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, bars))
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": rng.uniform(1e5, 2e5, bars),
        }
    )
    with warnings.catch_warnings():
        # ta is noisy about division-by-zero on synthetic data; irrelevant here.
        warnings.simplefilter("ignore")
        enriched = add_all_ta_features(frame, open="open", high="high", low="low", close="close", volume="volume")
    return tuple(c for c in enriched.columns if c not in OHLCV)


def test_catalogue_matches_the_pinned_ta_version(emitted_columns):
    missing = set(emitted_columns) - TA_COLUMNS
    stale = TA_COLUMNS - set(emitted_columns)

    assert not stale, (
        f"TA_COLUMN_ORDER lists columns `ta` no longer emits: {sorted(stale)}. "
        "Any saved spec referencing one of these is now silently inert."
    )
    assert not missing, (
        f"`ta` emits columns the catalogue does not list: {sorted(missing)}. "
        "Add them to TA_COLUMN_ORDER so specs can use them."
    )


def test_catalogue_preserves_emission_order(emitted_columns):
    """Order is cosmetic but keeps diffs readable when the pin moves."""
    assert emitted_columns == TA_COLUMN_ORDER


def test_no_duplicate_columns():
    assert len(TA_COLUMN_ORDER) == len(TA_COLUMNS)


def test_curated_indicators_all_exist():
    unknown = CURATED - TA_COLUMNS
    assert not unknown, f"curated dropdown references non-existent columns: {sorted(unknown)}"


def test_curated_entries_are_well_formed():
    for info in CURATED_INFO:
        assert info.group in GROUPS, f"{info.key}: unknown group {info.group!r}"
        assert info.unit in UNITS, f"{info.key}: unknown unit {info.unit!r}"
        assert info.label and info.hint, f"{info.key}: needs a label and a hint"


def test_price_info_covers_every_price_field():
    assert {info.key for info in PRICE_INFO} == set(PRICE_FIELDS)


def test_payload_keys_are_unique():
    """Duplicate keys would render as duplicate dropdown rows."""
    payload = curated_payload()
    keys = [entry["key"] for entry in payload]
    assert len(keys) == len(set(keys))
    assert len(payload) == len(PRICE_INFO) + len(CURATED_INFO)


def test_payload_is_json_shaped():
    for entry in curated_payload():
        assert set(entry) == {"key", "group", "label", "unit", "hint"}
        assert all(isinstance(v, str) for v in entry.values())
