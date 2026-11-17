"""Tests for 13F manager eligibility and conviction scores (synthetic panels)."""

import pandas as pd

from tradingbot.utils.inst13f_signal import conviction_scores, eligible_filers

QENDS = pd.date_range("2019-03-31", periods=10, freq="QE")
AS_OF = "2021-08-01"


def _panel(specs, late_last=False):
    """specs: cik -> function(quarter_idx) -> {ticker: value}."""
    filings, holdings = [], []
    for cik, fn in specs.items():
        for i, qe in enumerate(QENDS):
            acc = f"{cik}-{i}"
            filed = qe + pd.Timedelta(days=40)
            if late_last and i == len(QENDS) - 1:
                filed = pd.Timestamp("2030-01-01")
            filings.append(
                {
                    "accession": acc,
                    "cik": cik,
                    "filer_name": f"F{cik}",
                    "period_end": qe,
                    "filed_at": filed,
                    "is_amendment": False,
                }
            )
            for t, v in fn(i).items():
                holdings.append(
                    {
                        "accession": acc,
                        "cusip": f"C_{t}",
                        "ticker": t,
                        "value_usd": v,
                        "shares": 1,
                        "put_call": None,
                    }
                )
    return pd.DataFrame(filings), pd.DataFrame(holdings)


def _conc(i):
    d = {f"T{k}": 100.0 for k in range(10)}
    d.update({f"S{k}": 10.0 for k in range(10)})
    return d


def test_filing_after_as_of_is_invisible():
    f, h = _panel({1: _conc})
    # as_of before the 8th filing was accepted -> only 7 quarters visible
    early = QENDS[7] + pd.Timedelta(days=10)
    assert eligible_filers(f, h, early) == set()
    assert eligible_filers(f, h, QENDS[7] + pd.Timedelta(days=41)) == {1}
    f2, h2 = _panel({1: _conc}, late_last=True)
    s = conviction_scores(f2, h2, "2024-01-01")
    assert not s.empty
    assert eligible_filers(f2, h2, "2024-01-01") == {1}  # latest filing still hidden


def test_concentration_and_turnover_filters():
    def flat(i):  # top-10 = 25% -> out
        return {f"U{k}": 100.0 for k in range(40)}

    def churn(i):  # 100% turnover
        return {f"{'A' if i % 2 else 'B'}{k}": 100.0 for k in range(20)}

    def few(i):  # < 10 positions
        return {f"F{k}": 100.0 for k in range(5)}

    f, h = _panel({1: _conc, 2: flat, 3: churn, 4: few})
    assert eligible_filers(f, h, AS_OF) == {1}


def test_score_zero_or_absent_for_unheld_names():
    f, h = _panel({1: _conc, 2: lambda i: {f"U{k}": 100.0 for k in range(40)}})
    s = conviction_scores(f, h, AS_OF)
    assert s["T0"] == 1.0
    assert "U0" not in s.index  # held only by an ineligible manager
    assert s.get("NOPE", 0.0) == 0.0
    assert not s.index.isna().any()
