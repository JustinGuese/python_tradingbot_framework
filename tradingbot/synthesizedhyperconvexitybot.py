"""
Synthesized Hyper-Convexity Engine (SHCE) — Pattern B

Trades 3× leveraged Nasdaq ETFs (TQQQ / SQQQ) aligned to the broader market
cycle using five layered filters:

  1. Stage Analysis  — Stan Weinstein 30-week SMA on QQQ classifies the
                       macro regime into Stage 1-4 (basing/advancing/
                       topping/declining) and picks the instrument direction.
  2. BB/KC Squeeze   — Enter TQQQ only when Bollinger Bands are inside
                       Keltner Channels, signalling a volatility coil that
                       precedes explosive moves.
  3. VIX Trend       — Declining VIX (below its 20-day MA) confirms the
                       gamma-unwind tailwind for leveraged longs.
  4. Sentiment Gate  — CNN Fear & Greed index used as contrarian filter:
                       extreme euphoria (>75) delays new TQQQ entries;
                       extreme fear (<25) closes SQQQ positions early.
  5. Black Swan CB   — Hard circuit breaker: if TQQQ drops ≥20% or QQQ
                       drops ≥7% on any single day, all positions are
                       liquidated and capital rotates to IEF (bonds).

Position sizing:
  Vol-of-Vol Kelly proxy — base fraction (50%) scaled by inverse of the
  recent ATR/price ratio.  High relative volatility → smaller stake.
  Range: 25%–80% of available cash.

Instruments:
  TQQQ  — 3× leveraged Nasdaq-100 (long)
  SQQQ  — 3× inverse  Nasdaq-100  (short proxy)
  IEF   — 7-10yr Treasury ETF    (safe haven / transition hedge)

Reference:
  docs/examples/synthesized-hyper-convexity-engine.md.md

Schedule: 30 21 * * 1-5  (9:30 PM UTC ≈ 30 min after US market close)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from utils.botclass import Bot
from utils.portfolio_utils import get_fear_greed_index

logger = logging.getLogger(__name__)

# ── Instruments ───────────────────────────────────────────────────────────────
UNDERLYING = "QQQ"     # Nasdaq-100 proxy — used for stage analysis
LONG_3X    = "TQQQ"   # 3× long Nasdaq
SHORT_3X   = "SQQQ"   # 3× short Nasdaq
SAFE_HAVEN = "IEF"    # 7-10yr Treasuries — hold during Stage 1 / 3 / crash

# ── Stage analysis ────────────────────────────────────────────────────────────
SMA_WEEKS   = 30   # Weinstein's 30-week SMA
SLOPE_WEEKS = 4    # compare SMA[now] vs SMA[N weeks ago] to judge slope

# ── BB / KC Squeeze ───────────────────────────────────────────────────────────
BB_PERIOD  = 20
BB_STDDEV  = 2.0
ATR_PERIOD = 20
ATR_MULT   = 1.5   # Keltner Channel = EMA ± 1.5 × ATR

# ── VIX trend ─────────────────────────────────────────────────────────────────
VIX_MA_PERIOD = 20   # VIX < 20-day SMA → declining (bullish for longs)

# ── Sentiment (Fear & Greed) ──────────────────────────────────────────────────
FG_EXTREME_GREED = 75   # delay / reduce TQQQ entry at euphoria peaks
FG_EXTREME_FEAR  = 25   # close SQQQ early at capitulation troughs

# ── Black Swan circuit breaker ────────────────────────────────────────────────
CB_TQQQ_DROP = -0.20   # single-day TQQQ drawdown threshold
CB_QQQ_DROP  = -0.07   # single-day QQQ drawdown threshold (market halt proxy)

# ── Position sizing (Vol-of-Vol Kelly) ────────────────────────────────────────
BASE_KELLY = 0.50
MAX_KELLY  = 0.80
MIN_KELLY  = 0.25
# Relative-ATR scaling: 1% vol → MAX_KELLY; 4%+ vol → MIN_KELLY
VOL_LOW  = 0.01
VOL_HIGH = 0.04


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sorted_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("timestamp").reset_index(drop=True)


def _compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    h  = df["high"]
    lo = df["low"]
    pc = df["close"].shift(1)
    tr = pd.concat([(h - lo), (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _detect_stage(weekly_df: pd.DataFrame) -> str:
    """
    Classify the Nasdaq-100 into Weinstein Stage 1-4 using the 30-week SMA.

    Stage 2  (price > rising SMA)  → long (TQQQ)
    Stage 4  (price < falling SMA) → short (SQQQ)
    Stage 1  (price < flat/rising SMA) → basing, wait
    Stage 3  (price > flat/falling SMA) → topping, exit

    Returns 'stage1' | 'stage2' | 'stage3' | 'stage4' | 'unknown'
    """
    df = _sorted_df(weekly_df)
    if len(df) < SMA_WEEKS + SLOPE_WEEKS + 2:
        return "unknown"

    close = df["close"]
    sma = close.rolling(SMA_WEEKS).mean()

    sma_now  = float(sma.iloc[-1])
    sma_prev = float(sma.iloc[-(SLOPE_WEEKS + 1)])
    price    = float(close.iloc[-1])

    if pd.isna(sma_now) or pd.isna(sma_prev):
        return "unknown"

    sma_rising  = sma_now > sma_prev
    price_above = price > sma_now

    if price_above and sma_rising:
        return "stage2"    # Advancing — primary long zone
    elif not price_above and not sma_rising:
        return "stage4"    # Declining — short or cash
    elif price_above and not sma_rising:
        return "stage3"    # Topping — SMA rolling over, exit longs
    else:
        return "stage1"    # Basing — price below a flat/rising SMA, wait


def _detect_bbkc_squeeze(df: pd.DataFrame) -> bool:
    """
    Return True when Bollinger Bands are inside Keltner Channels (TTM Squeeze).
    This signals an unusually low-volatility coil that often precedes an
    explosive directional move — ideal for 3× leverage entry.
    """
    df = _sorted_df(df)
    if len(df) < max(BB_PERIOD, ATR_PERIOD) + 2:
        return False

    close  = df["close"]

    # Bollinger Bands
    bb_mid  = close.rolling(BB_PERIOD).mean()
    bb_std  = close.rolling(BB_PERIOD).std()
    bb_high = bb_mid + BB_STDDEV * bb_std
    bb_low  = bb_mid - BB_STDDEV * bb_std

    # Keltner Channels (EMA ± ATR_MULT × ATR)
    ema     = close.ewm(span=ATR_PERIOD, adjust=False).mean()
    atr     = _compute_atr(df, ATR_PERIOD)
    kc_high = ema + ATR_MULT * atr
    kc_low  = ema - ATR_MULT * atr

    bh = float(bb_high.iloc[-1])
    bl = float(bb_low.iloc[-1])
    kh = float(kc_high.iloc[-1])
    kl = float(kc_low.iloc[-1])

    if any(pd.isna(x) for x in [bh, bl, kh, kl]):
        return False

    return bh < kh and bl > kl   # BBands fully inside KChannels


def _vix_declining(vix_df: pd.DataFrame) -> Optional[bool]:
    """
    Returns True if the latest VIX close is below its 20-day SMA (declining
    trend → dealers unwinding hedges, bullish for TQQQ gamma).
    Returns None when data is insufficient.
    """
    df = _sorted_df(vix_df)
    if len(df) < VIX_MA_PERIOD + 1:
        return None

    close = df["close"]
    ma    = close.rolling(VIX_MA_PERIOD).mean()
    v     = float(close.iloc[-1])
    ma_v  = float(ma.iloc[-1])

    if pd.isna(ma_v):
        return None
    return v < ma_v


def _daily_return(df: pd.DataFrame) -> Optional[float]:
    """Last bar's return vs prior close. None if insufficient data."""
    df = _sorted_df(df)
    if len(df) < 2:
        return None
    c0 = float(df["close"].iloc[-2])
    c1 = float(df["close"].iloc[-1])
    return (c1 - c0) / c0 if c0 > 0 else None


def _kelly_fraction(df: pd.DataFrame) -> float:
    """
    Simplified Vol-of-Vol Kelly: scale BASE_KELLY by the inverse of
    recent ATR/price (relative volatility).

    Low  relative volatility  → position approaches MAX_KELLY.
    High relative volatility  → position approaches MIN_KELLY.
    """
    df = _sorted_df(df)
    if len(df) < ATR_PERIOD + 2:
        return BASE_KELLY

    atr_val = float(_compute_atr(df, ATR_PERIOD).iloc[-1])
    price   = float(df["close"].iloc[-1])

    if price <= 0 or pd.isna(atr_val):
        return BASE_KELLY

    rel_vol = atr_val / price
    raw = MAX_KELLY - (MAX_KELLY - MIN_KELLY) * (rel_vol - VOL_LOW) / (VOL_HIGH - VOL_LOW)
    return float(np.clip(raw, MIN_KELLY, MAX_KELLY))


# ── Bot ───────────────────────────────────────────────────────────────────────

class SynthesizedHyperConvexityBot(Bot):
    """
    Synthesized Hyper-Convexity Engine (SHCE) — Pattern B.

    Positions into TQQQ / SQQQ / IEF based on Weinstein stage analysis,
    BB/KC squeeze confirmation, VIX trend, Fear & Greed sentiment, and a
    hard Black Swan circuit breaker.
    """

    def __init__(self):
        super().__init__("SynthesizedHyperConvexityBot", symbol=None)

    def makeOneIteration(self) -> int:
        # ── 1. Data fetching ─────────────────────────────────────────────────
        # 2 years of weekly QQQ data → enough for 30-week SMA + slope buffer
        qqq_weekly = self.getYFData(UNDERLYING, interval="1wk", period="2y",  saveToDB=True)
        # Short daily window for circuit-breaker last-bar return check
        qqq_daily  = self.getYFData(UNDERLYING, interval="1d",  period="5d")
        # 3 months of TQQQ daily for squeeze detection and Kelly sizing
        tqqq_daily = self.getYFData(LONG_3X,    interval="1d",  period="3mo")
        # VIX daily for trend detection
        vix_daily  = self.getYFData("^VIX",     interval="1d",  period="3mo")

        # ── 2. Fear & Greed ──────────────────────────────────────────────────
        fg_raw = get_fear_greed_index()
        fg     = fg_raw if fg_raw is not None else 50
        logger.info("Fear & Greed index: %d", fg)

        # ── 3. Portfolio state ───────────────────────────────────────────────
        portfolio = self.dbBot.portfolio
        cash      = portfolio.get("USD",        0.0)
        tqqq_pos  = portfolio.get(LONG_3X,      0.0)
        sqqq_pos  = portfolio.get(SHORT_3X,     0.0)
        ief_pos   = portfolio.get(SAFE_HAVEN,   0.0)

        # ── 4. Black Swan circuit breaker ────────────────────────────────────
        tqqq_ret = _daily_return(tqqq_daily) if not tqqq_daily.empty else None
        qqq_ret  = _daily_return(qqq_daily)  if not qqq_daily.empty  else None

        cb_triggered = (
            (tqqq_ret is not None and tqqq_ret <= CB_TQQQ_DROP) or
            (qqq_ret  is not None and qqq_ret  <= CB_QQQ_DROP)
        )
        if cb_triggered:
            logger.warning(
                "BLACK SWAN CB triggered — TQQQ return=%.1f%%, QQQ return=%.1f%%",
                (tqqq_ret or 0) * 100,
                (qqq_ret  or 0) * 100,
            )
            logger.warning(
                f"BLACK SWAN CIRCUIT BREAKER: TQQQ={tqqq_ret:.1%} QQQ={qqq_ret:.1%} "
                f"— liquidating all positions, rotating 100% to {SAFE_HAVEN}"
            )
            if tqqq_pos > 0:
                self.sell(LONG_3X)
            if sqqq_pos > 0:
                self.sell(SHORT_3X)
            if ief_pos > 0:
                self.sell(SAFE_HAVEN)
            new_cash = self.dbBot.portfolio.get("USD", 0)
            if new_cash > 10:
                self.buy(SAFE_HAVEN)
            return -1

        # ── 5. Compute signals ───────────────────────────────────────────────
        stage     = _detect_stage(qqq_weekly) if not qqq_weekly.empty else "unknown"
        squeeze   = _detect_bbkc_squeeze(tqqq_daily) if not tqqq_daily.empty else False
        vix_down  = _vix_declining(vix_daily) if not vix_daily.empty else None
        kelly     = _kelly_fraction(tqqq_daily) if not tqqq_daily.empty else BASE_KELLY

        logger.info(
            f"SHCE | Stage={stage}  Squeeze={squeeze}  VIX_declining={vix_down}"
            f"  F&G={fg}  Kelly={kelly:.0%}"
            f"  | USD={cash:.0f}  {LONG_3X}={tqqq_pos:.3f}"
            f"  {SHORT_3X}={sqqq_pos:.3f}  {SAFE_HAVEN}={ief_pos:.3f}"
        )

        # ── 6. Decision logic ────────────────────────────────────────────────

        if stage == "stage2":
            # Exit inverse / safe-haven positions
            if sqqq_pos > 0:
                self.sell(SHORT_3X)
            if ief_pos > 0:
                self.sell(SAFE_HAVEN)

            if fg >= FG_EXTREME_GREED:
                # Extreme euphoria: don't open new TQQQ position (Stage 3 risk)
                logger.info(f"Stage 2 but extreme greed ({fg}) — holding existing TQQQ, no new entry")
                return 0

            if tqqq_pos <= 0 and cash > 10:
                if squeeze or vix_down:
                    # Full Kelly entry when squeeze or VIX confirms
                    buy_usd = cash * kelly
                    logger.info(
                        f"Stage 2 LONG entry — buying {LONG_3X} ${buy_usd:.0f}"
                        f"  (kelly={kelly:.0%}  squeeze={squeeze}  vix_down={vix_down})"
                    )
                else:
                    # Stage 2 confirmed but no secondary trigger — partial entry
                    buy_usd = cash * MIN_KELLY
                    logger.info(
                        f"Stage 2 partial entry (no squeeze/VIX trigger) — buying {LONG_3X} ${buy_usd:.0f}"
                    )
                self.buy(LONG_3X, quantity_usd=buy_usd)
                return 1

            return 0

        elif stage == "stage4":
            # Exit long / safe-haven positions
            if tqqq_pos > 0:
                self.sell(LONG_3X)
            if ief_pos > 0:
                self.sell(SAFE_HAVEN)

            if fg <= FG_EXTREME_FEAR:
                # Extreme panic: potential Stage 4→1 transition, close shorts early
                logger.info(f"Stage 4 but extreme fear ({fg}) — closing {SHORT_3X}, rotating to {SAFE_HAVEN}")
                if sqqq_pos > 0:
                    self.sell(SHORT_3X)
                new_cash = self.dbBot.portfolio.get("USD", 0)
                if new_cash > 10:
                    self.buy(SAFE_HAVEN)
                return 0

            if sqqq_pos <= 0 and cash > 10:
                buy_usd = cash * kelly
                logger.info(f"Stage 4 SHORT entry — buying {SHORT_3X} ${buy_usd:.0f}  (kelly={kelly:.0%})")
                self.buy(SHORT_3X, quantity_usd=buy_usd)
                return -1

            return 0

        else:
            # Stage 1 (basing), Stage 3 (topping), or unknown → safe haven
            logger.info(f"Stage={stage} — rotating to {SAFE_HAVEN}")
            if tqqq_pos > 0:
                self.sell(LONG_3X)
            if sqqq_pos > 0:
                self.sell(SHORT_3X)
            new_cash = self.dbBot.portfolio.get("USD", 0)
            if new_cash > 10 and ief_pos <= 0:
                self.buy(SAFE_HAVEN)
            return 0



bot = SynthesizedHyperConvexityBot()
bot.run() # EVENT DRIVEN, no backtest possible
# bot.local_development()