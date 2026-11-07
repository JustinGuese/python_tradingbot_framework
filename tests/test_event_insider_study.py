"""
Unit tests for insider cluster buys event study (scripts/onetime_event_insider_study.py).

Pure tests: strictly no DB, no network access.
Acceptance:
- Parser unit-tested on a saved Form 4 XML fixture.
- Cluster formation requires >= 2 (or 3) distinct insiders within 10 days.
- Look-ahead guard enters first daily close strictly after filing is public.
- Net costs correctly apply 2 bp and 5 bp per side.
"""

from datetime import date, datetime
import numpy as np
import pandas as pd
import pytest

from scripts.onetime_event_insider_study import (
    event_strategy_returns_with_costs,
    find_insider_clusters,
    parse_form4_xml,
)

FORM4_XML_PURCHASE_FIXTURE = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2024-03-15</periodOfReport>
    <issuer>
        <issuerCik>0000019617</issuerCik>
        <issuerName>JPMorgan Chase &amp; Co.</issuerName>
        <issuerTradingSymbol>JPM</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001198642</rptOwnerCik>
            <rptOwnerName>DIMON JAMES</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>true</isOfficer>
            <officerTitle>Chairman &amp; CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
            </securityTitle>
            <transactionDate>
                <value>2024-03-15</value>
            </transactionDate>
            <transactionCoding>
                <transactionCode>P</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>50000</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>185.50</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""

FORM4_XML_SALE_FIXTURE = """<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>4</documentType>
    <periodOfReport>2024-04-01</periodOfReport>
    <issuer>
        <issuerCik>0000320193</issuerCik>
        <issuerName>Apple Inc.</issuerName>
        <issuerTradingSymbol>AAPL</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001780525</rptOwnerCik>
            <rptOwnerName>Newstead Jennifer</rptOwnerName>
        </reportingOwnerId>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate>
                <value>2024-04-01</value>
            </transactionDate>
            <transactionCoding>
                <transactionCode>S</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>1000</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>170.00</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_form4_xml_fixture():
    """Unit test parser on saved Form 4 XML fixture: open-market purchase (code P)."""
    records = parse_form4_xml(FORM4_XML_PURCHASE_FIXTURE)
    assert len(records) == 1
    rec = records[0]
    assert rec["symbol"] == "JPM"
    assert rec["insider"] == "DIMON JAMES"
    assert rec["trans_date"] == "2024-03-15"
    assert rec["trans_code"] == "P"
    assert rec["shares"] == 50000.0
    assert rec["price"] == 185.50
    assert rec["total_usd"] == 50000.0 * 185.50
    assert rec["acquired_disposed"] == "A"


def test_parse_form4_xml_sale_distinction():
    """Verify parser distinguishes open market sales (code S) from purchases (code P)."""
    records = parse_form4_xml(FORM4_XML_SALE_FIXTURE)
    assert len(records) == 1
    rec = records[0]
    assert rec["symbol"] == "AAPL"
    assert rec["trans_code"] == "S"
    assert rec["trans_code"] != "P"


def test_cluster_detection_two_distinct_insiders_within_10_days():
    """
    Cluster requires >= 2 distinct insiders buying within 10 days.
    Event day must be the filing date of the second insider.
    """
    trades = pd.DataFrame([
        {
            "symbol": "JPM",
            "insider": "Alice Director",
            "trans_date": date(2024, 3, 1),
            "filing_date": date(2024, 3, 3),
            "shares": 1000,
            "price": 100.0,
            "total_usd": 100000.0,
            "trans_code": "P",
        },
        {
            "symbol": "JPM",
            "insider": "Bob Officer",
            "trans_date": date(2024, 3, 6),
            "filing_date": date(2024, 3, 8),
            "shares": 500,
            "price": 102.0,
            "total_usd": 51000.0,
            "trans_code": "P",
        },
    ])

    clusters = find_insider_clusters(trades, min_cluster_size=2, min_total_usd=50000.0, window_days=10)
    assert len(clusters) == 1
    ev = clusters.iloc[0]
    assert ev["symbol"] == "JPM"
    assert ev["cluster_size"] == 2
    assert ev["event_date"] == date(2024, 3, 8)  # Filing date of the second
    assert ev["total_usd"] == 151000.0


def test_cluster_detection_same_insider_does_not_form_cluster():
    """Multiple buys by the same insider do NOT satisfy distinct insiders requirement."""
    trades = pd.DataFrame([
        {
            "symbol": "MSFT",
            "insider": "Solo Executive",
            "trans_date": date(2024, 3, 1),
            "filing_date": date(2024, 3, 3),
            "shares": 1000,
            "price": 400.0,
            "total_usd": 400000.0,
            "trans_code": "P",
        },
        {
            "symbol": "MSFT",
            "insider": "Solo Executive",  # Same insider!
            "trans_date": date(2024, 3, 5),
            "filing_date": date(2024, 3, 7),
            "shares": 1000,
            "price": 405.0,
            "total_usd": 405000.0,
            "trans_code": "P",
        },
    ])

    clusters = find_insider_clusters(trades, min_cluster_size=2, min_total_usd=50000.0, window_days=10)
    assert len(clusters) == 0


def test_cluster_detection_dollar_threshold_and_window_filter():
    """
    Buys separated by > 10 days, or total dollar purchases < min_total_usd,
    must NOT trigger a cluster.
    """
    # Case 1: Insiders separated by 15 days (> 10 days)
    trades_separated = pd.DataFrame([
        {
            "symbol": "AAPL",
            "insider": "Insider 1",
            "trans_date": date(2024, 1, 1),
            "filing_date": date(2024, 1, 2),
            "total_usd": 100000.0,
            "trans_code": "P",
        },
        {
            "symbol": "AAPL",
            "insider": "Insider 2",
            "trans_date": date(2024, 1, 18),  # 17 days later
            "filing_date": date(2024, 1, 19),
            "total_usd": 100000.0,
            "trans_code": "P",
        },
    ])
    assert len(find_insider_clusters(trades_separated, min_cluster_size=2, min_total_usd=50000.0, window_days=10)) == 0

    # Case 2: 2 insiders within 5 days, but total $ = 30k < 50k
    trades_small = pd.DataFrame([
        {
            "symbol": "AAPL",
            "insider": "Insider 1",
            "trans_date": date(2024, 2, 1),
            "filing_date": date(2024, 2, 2),
            "total_usd": 15000.0,
            "trans_code": "P",
        },
        {
            "symbol": "AAPL",
            "insider": "Insider 2",
            "trans_date": date(2024, 2, 4),
            "filing_date": date(2024, 2, 5),
            "total_usd": 15000.0,
            "trans_code": "P",
        },
    ])
    assert len(find_insider_clusters(trades_small, min_cluster_size=2, min_total_usd=50000.0, window_days=10)) == 0


def test_strategy_returns_cost_deduction():
    """Verify net costs: exactly 2 bp (0.0002) and 5 bp (0.0005) deducted per side."""
    dates = pd.date_range("2024-01-01", periods=15, freq="B")
    prices = {"XYZ": pd.Series([100.0 + i for i in range(15)], index=dates)}

    # Signal on day 1 (index 1)
    signals = pd.DataFrame([{"symbol": "XYZ", "ts": pd.Timestamp("2024-01-02 23:59:59", tz="UTC")}])

    gross = event_strategy_returns_with_costs(signals, prices, hold_days=3, cost_per_side=0.0)
    net_2bp = event_strategy_returns_with_costs(signals, prices, hold_days=3, cost_per_side=0.0002)
    net_5bp = event_strategy_returns_with_costs(signals, prices, hold_days=3, cost_per_side=0.0005)

    # Active return days: entry at close of index 2 (2024-01-03).
    # Holding period 3 days: index 3, 4, 5.
    active_days = gross[gross != 0.0].index
    assert len(active_days) == 3

    # On entry day (first active day), return should be reduced by cost_per_side
    first_day = active_days[0]
    assert np.isclose(net_2bp[first_day], gross[first_day] - 0.0002)
    assert np.isclose(net_5bp[first_day], gross[first_day] - 0.0005)

    # On exit day (last active day), return should be reduced by cost_per_side
    last_day = active_days[-1]
    assert np.isclose(net_2bp[last_day], gross[last_day] - 0.0002)
    assert np.isclose(net_5bp[last_day], gross[last_day] - 0.0005)
