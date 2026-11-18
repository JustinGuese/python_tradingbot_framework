"""Tests for cash-adjusted alpha statistics (tradingbot/utils/alpha_report.py)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.alpha_report import (
    alpha_stats,
    build_report,
    cash_adjusted_stats,
    format_report,
)


def _generate_returns(n: int = 250, seed: int = 42):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end="2026-10-30", periods=n)
    k = len(days)
    r_bench = pd.Series(rng.normal(0.0005, 0.012, k), index=days)
    r_bot = pd.Series(0.4 * r_bench + rng.normal(0.0002, 0.008, k), index=days)
    return r_bot, r_bench


def test_zero_cash_bot_identical_to_plain_stats():
    """A bot with 0% cash has cash-adjusted stats identical to plain alpha_stats."""
    r_bot, r_bench = _generate_returns()
    plain = alpha_stats(r_bot, r_bench)
    assert plain is not None

    # float usd_share = 0.0
    cash_adj = cash_adjusted_stats(r_bot, r_bench, usd_share=0.0, irx=0.045)
    assert cash_adj is not None
    assert cash_adj.alpha == pytest.approx(plain.alpha)
    assert cash_adj.t == pytest.approx(plain.t)
    assert cash_adj.beta == pytest.approx(plain.beta)
    assert cash_adj.corr == pytest.approx(plain.corr)
    assert cash_adj.n == plain.n

    # series usd_share = 0.0
    zero_series = pd.Series(0.0, index=r_bot.index)
    cash_adj_s = cash_adjusted_stats(r_bot, r_bench, usd_share=zero_series, irx=0.045)
    assert cash_adj_s is not None
    assert cash_adj_s.alpha == pytest.approx(plain.alpha)
    assert cash_adj_s.t == pytest.approx(plain.t)

    # holdings with USD = 0
    holdings = pd.Series([{"USD": 0.0, "AAPL": 100.0} for _ in r_bot.index], index=r_bot.index)
    worth = pd.Series([10000.0 for _ in r_bot.index], index=r_bot.index)
    cash_adj_h = cash_adjusted_stats(r_bot, r_bench, holdings=holdings, portfolio_worth=worth, irx=0.045)
    assert cash_adj_h is not None
    assert cash_adj_h.alpha == pytest.approx(plain.alpha)


def test_all_cash_bot_vs_zero_beta_book_cash_credit_only():
    """An all-cash bot gives alpha ~0 unadjusted vs a zero-beta book;

    with cash adjustment, it earns the cash credit only (alpha ~ irx).
    """
    days = pd.bdate_range(end="2026-10-30", periods=250)
    k = len(days)
    rng = np.random.default_rng(123)

    # All-cash bot has zero trading returns
    r_bot = pd.Series(0.0, index=days)

    # Zero-beta book: orthogonal noise uncorrelated with cash, zero mean
    r_zero_beta = pd.Series(rng.normal(0.0, 0.01, k), index=days)

    # 1. Plain stats: all-cash bot gives alpha ~ 0 vs a zero-beta book
    plain = alpha_stats(r_bot, r_zero_beta)
    assert plain is not None
    assert plain.alpha == pytest.approx(0.0, abs=1e-6)
    assert plain.beta == pytest.approx(0.0, abs=1e-6)

    # 2. Cash-adjusted stats: gets cash credit only (alpha = irx_annual)
    irx_annual = 0.04  # 4% annual risk-free rate
    cash_adj = cash_adjusted_stats(r_bot, r_zero_beta, usd_share=1.0, irx=irx_annual)
    assert cash_adj is not None

    # Beta to zero-beta book is 0
    assert cash_adj.beta == pytest.approx(0.0, abs=1e-4)

    # Alpha is the cash credit only: exactly irx_annual
    assert cash_adj.alpha == pytest.approx(irx_annual, rel=1e-4)


def test_partial_cash_bot_credits_proportionally():
    """A bot with 50% cash gets half the cash credit."""
    r_bot, r_bench = _generate_returns()
    irx = 0.04
    plain = alpha_stats(r_bot, r_bench)
    half_cash = cash_adjusted_stats(r_bot, r_bench, usd_share=0.5, irx=irx)

    assert plain is not None and half_cash is not None
    # Beta stays approximately identical because cash has ~0 cov with equity bench
    assert half_cash.beta == pytest.approx(plain.beta, abs=1e-3)
    # Alpha increases by ~ 0.5 * irx
    expected_alpha_increase = 0.5 * irx
    assert (half_cash.alpha - plain.alpha) == pytest.approx(expected_alpha_increase, rel=1e-2)


def test_holdings_and_portfolio_worth_input():
    """USD share correctly calculated from holdings['USD'] / portfolio_worth."""
    days = pd.bdate_range(end="2026-10-30", periods=100)
    r_bot = pd.Series(0.0001, index=days)
    r_bench = pd.Series(0.0005, index=days)

    holdings = pd.Series([{"USD": 4000.0, "AAPL": 60.0} for _ in days], index=days)
    worth = pd.Series([10000.0 for _ in days], index=days)

    res = cash_adjusted_stats(r_bot, r_bench, holdings=holdings, portfolio_worth=worth, irx=0.04)
    direct = cash_adjusted_stats(r_bot, r_bench, usd_share=0.4, irx=0.04)

    assert res is not None and direct is not None
    assert res.alpha == pytest.approx(direct.alpha)
    assert res.t == pytest.approx(direct.t)


def test_irx_percentage_scaling():
    """irx passed as 4.5 (%) is treated identically to 0.045."""
    r_bot, r_bench = _generate_returns()
    res_pct = cash_adjusted_stats(r_bot, r_bench, usd_share=0.8, irx=4.5)
    res_dec = cash_adjusted_stats(r_bot, r_bench, usd_share=0.8, irx=0.045)

    assert res_pct is not None and res_dec is not None
    assert res_pct.alpha == pytest.approx(res_dec.alpha)
    assert res_pct.t == pytest.approx(res_dec.t)


def test_short_series_returns_none():
    """Series with fewer than 3 returns returns None, matching alpha_stats."""
    r_bot = pd.Series([0.01, 0.02])
    r_bench = pd.Series([0.01, 0.02])
    assert cash_adjusted_stats(r_bot, r_bench, usd_share=0.5, irx=0.04) is None


def test_build_report_and_format_report_with_cash_stats():
    """build_report populates cash_stats when holdings are provided and format_report logs it."""
    today = date(2026, 9, 26)
    days = pd.bdate_range(end="2026-09-25", periods=100)
    rng = np.random.default_rng(99)
    q_rets = pd.Series(rng.normal(0.0005, 0.01, len(days)), index=days)
    bot_rets = pd.Series(0.1 * q_rets + rng.normal(0.0002, 0.005, len(days)), index=days)

    bench_worth = 10_000.0 * (1 + q_rets).cumprod()
    bot_worth = 10_000.0 * (1 + bot_rets).cumprod()

    worth_by_bot = {
        "Benchmark_QQQ": bench_worth,
        "TestBot": bot_worth,
    }
    # 50% cash
    holdings_by_bot = {
        "TestBot": pd.Series([{"USD": float(w) * 0.5} for w in bot_worth], index=days),
    }

    rows = build_report(worth_by_bot, today, holdings_by_bot=holdings_by_bot, irx=0.04)
    assert len(rows) == 1
    row = rows[0]
    assert row.bot_name == "TestBot"
    assert row.cash_stats is not None
    assert row.stats is not None
    # cash_stats alpha should be higher than plain alpha due to cash credit
    assert row.cash_stats.alpha > row.stats.alpha
    assert (row.cash_stats.alpha - row.stats.alpha) == pytest.approx(0.5 * 0.04, rel=0.1)

    table = format_report(rows)
    assert "cAlpha" in table
    assert "ct" in table
    assert "TestBot" in table
