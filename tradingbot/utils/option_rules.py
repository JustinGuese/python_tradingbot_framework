"""
Entry, exit and sizing rules of the option_* bots, as pure functions.

Kept out of the bot classes so the synthetic backtest
(scripts/onetime_option_bots_backtest.py) runs exactly the rules the live bots
run; only the prices differ (live chain vs Black-Scholes on an IV proxy).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from . import option_math as om

logger = logging.getLogger(__name__)


def sma(close: pd.Series, window: int) -> float:
    tail = close.dropna().iloc[-window:]
    return float(tail.mean()) if len(tail) >= window else float("nan")


def trend_side(close: float, sma50: float, sma200: float) -> str | None:
    """Return "bull" when close > SMA50 > SMA200, "bear" when stacked the other way, else None."""
    if any(np.isnan(x) for x in (close, sma50, sma200)):
        return None
    if close > sma50 > sma200:
        return "bull"
    if close < sma50 < sma200:
        return "bear"
    return None


def business_days(start: date, end: date) -> int:
    """Weekdays from start (inclusive) to end (exclusive); negative when end < start."""
    return int(np.busday_count(start, end))


def earnings_clear(earnings: date | None, expiry: date, today: date) -> bool:
    """
    True when no scheduled earnings fall between today and expiry. An unknown
    date counts as clear (logged): blocking on a flaky calendar would stop the
    bot for good, and AAPL's dates are almost always published.
    """
    if earnings is None:
        logger.warning("Earnings date unknown; treating %s..%s as clear", today, expiry)
        return True
    return not today <= earnings <= expiry


# ------------------------------------------------------------------
# Premium selling: credit spreads and iron condors
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CreditRules:
    short_delta: float = 0.30
    width: float = 10.0
    target_dte: int = 35  # first expiry at least this far out: ~35-42 days
    min_iv_hv: float = 1.10  # course: sell when implied vol is well above historical
    hv_window: int = 20
    max_vix: float = 35.0  # no new short premium into a panic
    max_adx: float | None = None  # iron condor: only in a range-bound tape
    max_risk_pct: float = 0.25  # worst-case loss per position, share of the book
    take_profit: float = 0.50  # close at this share of the credit
    stop_loss: float = 2.0  # close when the loss reaches this multiple of the credit
    exit_dte: int = 21  # close with this few days left: gamma risk outgrows the theta left
    # "trend": bull put spread in an uptrend, bear call spread in a downtrend.
    # "bull": bull put spreads only (in any tape that is not a downtrend) — the
    # side that collects the equity drift and the put skew instead of fighting them.
    sides: str = "trend"
    call_delta: float | None = None  # iron condor call side; None = short_delta


def credit_side(close: float, sma50: float, sma200: float, rules: CreditRules) -> str | None:
    """Which vertical to sell today: "bull", "bear", or None."""
    side = trend_side(close, sma50, sma200)
    if rules.sides == "bull":
        return None if side == "bear" or np.isnan(sma200) else "bull"
    return side


def premium_selling_ok(iv_hv: float | None, vix: float | None, adx: float | None, rules: CreditRules) -> bool:
    if iv_hv is None or iv_hv < rules.min_iv_hv:
        return False
    if vix is not None and vix >= rules.max_vix:
        return False
    return not (rules.max_adx is not None and (adx is None or adx >= rules.max_adx))


def credit_exit_reason(credit: float, pnl: float, dte: int | None, rules: CreditRules) -> str | None:
    """Why to close a credit position now, or None to keep holding."""
    if dte is not None and dte <= rules.exit_dte:
        return f"{dte} DTE <= {rules.exit_dte}"
    if credit <= 0:
        return None
    if pnl >= rules.take_profit * credit:
        return f"take profit: {pnl:.2f} >= {rules.take_profit:.0%} of credit {credit:.2f}"
    if -pnl >= rules.stop_loss * credit:
        return f"stop loss: {pnl:.2f} <= -{rules.stop_loss:g}x credit {credit:.2f}"
    return None


# ------------------------------------------------------------------
# LEAP call: stock replacement
# ------------------------------------------------------------------


@dataclass(frozen=True)
class LeapRules:
    delta: float = 0.70
    target_dte: int = 540  # 1.5y: inside the course's 1-2 year LEAP window
    roll_dte: int = 180  # roll with ~6 months left, before theta accelerates
    max_iv_hv: float = 1.20  # course: buy options when IV is not rich
    hv_window: int = 60
    exit_buffer: float = 0.03  # exit below SMA200 x (1 - buffer), to avoid whipsaw
    leverage: float = 1.0  # delta-dollars per dollar of book
    # Trim back to `leverage` once delta-dollars exceed this multiple of the book.
    # None never trims: gains then compound the exposure (to ~1.6x on average).
    max_leverage: float | None = None


def leap_contracts(book_value: float, spot: float, delta: float, leverage: float) -> int:
    """Whole contracts whose delta-dollars come closest to leverage x book."""
    per_contract = delta * spot * 100
    return max(0, round(book_value * leverage / per_contract)) if per_contract > 0 else 0


def leap_trim_contracts(
    delta_dollars: float, book_value: float, delta_dollars_per_contract: float, rules: LeapRules
) -> int:
    """Contracts to sell so exposure returns to rules.leverage x book (0 if within max_leverage)."""
    if rules.max_leverage is None or book_value <= 0 or delta_dollars_per_contract <= 0:
        return 0
    if delta_dollars <= rules.max_leverage * book_value:
        return 0
    return math.ceil((delta_dollars - rules.leverage * book_value) / delta_dollars_per_contract)


def leap_signal(close: float, sma200: float, rules: LeapRules) -> int:
    """1 = may hold/enter, -1 = must exit, 0 = in the buffer band (hold what you have, no entry)."""
    if np.isnan(sma200):
        return 0
    if close > sma200:
        return 1
    if close < sma200 * (1 - rules.exit_buffer):
        return -1
    return 0


# ------------------------------------------------------------------
# Catalyst call: long call into earnings, sold before the announcement
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CatalystRules:
    delta: float = 0.75  # course: ITM call, delta .75, when bullish
    entry_min_bdays: int = 10  # enter 10-25 trading days before earnings,
    entry_max_bdays: int = 25  # before the pre-earnings IV run-up is priced in
    days_after_earnings: int = 14  # expiry at least this long after earnings
    max_iv_hv: float = 1.0  # course: buy when implied vol is low
    hv_window: int = 20
    trend_window: int = 50
    position_pct: float = 0.30
    take_profit: float = 0.50
    stop_loss: float = 0.40


def catalyst_entry_ok(today: date, earnings: date | None, rules: CatalystRules) -> bool:
    if earnings is None:
        return False
    return rules.entry_min_bdays <= business_days(today, earnings) <= rules.entry_max_bdays


def catalyst_exit_reason(
    today: date, earnings: date | None, expiry: date, pnl_pct: float, rules: CatalystRules
) -> str | None:
    """
    Sell the day before earnings: keep the run-up in implied vol, skip the
    crush. If the next known earnings is already past this expiry, the catalyst
    we bought for has been missed (the bot did not run in time), so exit too.
    """
    if pnl_pct >= rules.take_profit:
        return f"take profit {pnl_pct:.0%}"
    if pnl_pct <= -rules.stop_loss:
        return f"stop loss {pnl_pct:.0%}"
    if earnings is not None and (business_days(today, earnings) <= 1 or earnings > expiry):
        return f"earnings {earnings}: exit before the announcement"
    return None


def short_premium_exit_reason(
    credit: float, pnl: float, dte: int | None, take_profit: float | None, exit_dte: int | None
) -> str | None:
    """Close a short-premium position at a share of its credit, or with this few days left."""
    if exit_dte is not None and dte is not None and dte <= exit_dte:
        return f"{dte} DTE <= {exit_dte}"
    if take_profit is not None and credit > 0 and pnl >= take_profit * credit:
        return f"take profit: {pnl:.2f} >= {take_profit:.0%} of credit {credit:.2f}"
    return None


# ------------------------------------------------------------------
# Pricing mismatch: implied vol against a fair-vol forecast
# ------------------------------------------------------------------


@dataclass(frozen=True)
class MispricingRules:
    target_dte: int = 35
    # IV - fair vol, in vol points, that counts as mispriced. The rich bar sits
    # above the ~5-point premium implied vol usually carries over realized
    # (the variance risk premium), so only an unusual gap trades.
    rich_gap: float = 0.06
    cheap_gap: float = 0.03
    exit_gap: float = 0.01  # the gap has closed: the mispricing is gone
    max_vix: float = 40.0  # no short vol into a panic
    # Rich: iron butterfly, wings at wing_sigmas x the expected move to expiry.
    wing_sigmas: float = 1.0
    take_profit: float = 0.25  # of the credit (butterflies rarely reach 50%)
    stop_loss: float = 1.0  # loss as a multiple of the credit
    max_risk_pct: float = 0.20
    # Cheap: long straddle, delta-hedged with shares every run.
    premium_pct: float = 0.10  # debit as a share of the book
    hedge_band_pct: float = 0.02  # re-hedge once |net delta| x spot > this x book
    straddle_take_profit: float = 0.30  # of the debit
    max_hold_days: int = 15
    exit_dte: int = 10
    # A gap is AAPL-specific (worth a news check) when AAPL's IV relative to
    # ^VXN exceeds its usual relative-vol ratio by this factor.
    specific_ratio: float = 1.15


def daily_close(data: pd.DataFrame) -> pd.Series:
    """`close` of a getYFData frame indexed by naive calendar date (the form the vol helpers expect)."""
    ts = pd.to_datetime(data.get("timestamp", data.index), utc=True)
    idx = pd.DatetimeIndex(ts).tz_localize(None).normalize()
    return pd.Series(data["close"].to_numpy(dtype=float), index=idx).dropna()


@dataclass(frozen=True)
class FairVol:
    fair: float  # annualised vol the option should be priced at
    diffusion: float  # HAR forecast of ex-earnings vol to expiry
    jump: float  # RMS earnings-day move (applied only when earnings fall inside)
    earnings_inside: bool


def fair_volatility_for(
    close: pd.Series,
    report_dates: Sequence[date],
    today: date,
    expiry: date,
    earnings_inside: bool,
    jump_events: int = 12,
) -> FairVol:
    """
    What the expiry's vol should be: HAR-RV diffusion vol over the trading days
    to expiry, fitted with earnings-reaction days removed, plus the stock's RMS
    earnings move over its last `jump_events` reports when one falls inside.
    """
    returns = om.log_returns(close)
    reactions = om.earnings_reaction_returns(close, report_dates)
    horizon = max(business_days(today, expiry), 1)
    diffusion = om.har_rv_forecast(returns, horizon, exclude=reactions.index)
    jump = om.earnings_jump(reactions.tail(jump_events).tolist()) if len(reactions) else float("nan")
    T = om.year_fraction(expiry, today)
    fair = om.fair_volatility(diffusion, T, jump if earnings_inside and jump == jump else 0.0)
    return FairVol(fair, diffusion, jump, earnings_inside)


def mispricing_side(iv: float | None, fair: float | None, rules: MispricingRules) -> str | None:
    """ "rich" when implied vol exceeds fair by rich_gap, "cheap" when below by cheap_gap, else None."""
    if iv is None or fair is None or math.isnan(iv) or math.isnan(fair):
        return None
    gap = iv - fair
    if gap >= rules.rich_gap:
        return "rich"
    if gap <= -rules.cheap_gap:
        return "cheap"
    return None


def butterfly_width(spot: float, iv: float, T: float, rules: MispricingRules) -> float:
    """Wing distance: wing_sigmas standard deviations of the move to expiry."""
    return rules.wing_sigmas * spot * iv * math.sqrt(max(T, 0.0))


def mispricing_exit_reason(
    side: str,
    gap: float | None,
    pnl: float,
    entry_value: float,
    dte: int | None,
    held_days: int,
    rules: MispricingRules,
) -> str | None:
    """Why to close a mispricing trade now: DTE, profit, stop, time, or the gap closing."""
    if dte is not None and dte <= rules.exit_dte:
        return f"{dte} DTE <= {rules.exit_dte}"
    if side == "rich":
        credit = max(0.0, -entry_value)
        if credit > 0 and pnl >= rules.take_profit * credit:
            return f"take profit: {pnl:.2f} >= {rules.take_profit:.0%} of credit {credit:.2f}"
        if credit > 0 and -pnl >= rules.stop_loss * credit:
            return f"stop loss: {pnl:.2f} <= -{rules.stop_loss:g}x credit {credit:.2f}"
        if gap is not None and gap <= rules.exit_gap:
            return f"gap closed: IV - fair = {gap:+.3f}"
        return None
    debit = max(0.0, entry_value)
    if debit > 0 and pnl >= rules.straddle_take_profit * debit:
        return f"take profit: {pnl:.2f} >= {rules.straddle_take_profit:.0%} of debit {debit:.2f}"
    if held_days >= rules.max_hold_days:
        return f"held {held_days} days >= {rules.max_hold_days}"
    if gap is not None and gap >= -rules.exit_gap:
        return f"gap closed: IV - fair = {gap:+.3f}"
    return None


# Headlines that describe a pending binary risk: the kind of news that makes a
# rich option price correct rather than mispriced.
EVENT_PATTERN = re.compile(
    r"\b(lawsuit|sues|sued|antitrust|court|ruling|verdict|trial|judge|doj|ftc|sec probe|probe|investigation|"
    r"recall|guidance|profit warning|merger|acquisition|acquire[sd]?|takeover|buyout|tariffs?|ban|sanctions?|"
    r"export controls?|ceo|resign\w*|downgrade)\b",
    re.IGNORECASE,
)

NEWS_SYSTEM_PROMPT = (
    "You read stock news headlines and decide whether they describe a PENDING event that could move the stock "
    "sharply before a given date: a court ruling, regulatory decision, merger vote, guidance update, product ban, "
    "tariff decision, management change. Past, settled or routine news (analyst notes, product reviews, "
    "already-reported results) is not pending. Reply with JSON only: "
    '{"pending_event": true|false, "event": "<one short phrase, empty if none>"}'
)


def classify_news(
    headlines: Sequence[str], symbol: str, until: date, ai: Callable[[str, str], str] | None
) -> str | None:
    """
    The pending event the headlines describe, or None. Asks `ai` (a cheap LLM
    call taking (system, user) and returning text) for JSON; if it is missing,
    fails or answers garbage, falls back to EVENT_PATTERN on the headlines.
    """
    if not headlines:
        return None
    if ai is not None:
        user = f"Symbol: {symbol}\nDate range: today until {until}\n\nHeadlines:\n" + "\n".join(
            f"- {h}" for h in headlines[:15]
        )
        try:
            raw = ai(NEWS_SYSTEM_PROMPT, user)
            match = re.search(r"\{.*\}", raw or "", re.DOTALL)
            parsed = json.loads(match.group(0)) if match else None
            if isinstance(parsed, dict) and isinstance(parsed.get("pending_event"), bool):
                event = str(parsed.get("event") or "").strip()
                return (event or "unspecified pending event") if parsed["pending_event"] else None
            logger.warning("News classifier returned no usable JSON (%r); using keywords", (raw or "")[:200])
        except Exception as e:
            logger.warning("News classifier failed (%s); using keywords", e)
    for h in headlines:
        if EVENT_PATTERN.search(h):
            return h
    return None


@dataclass(frozen=True)
class GapExplanation:
    """Why implied vol differs from fair vol, and whether what is left is worth trading."""

    iv: float
    fair: float
    side: str | None
    tradeable: bool
    reason: str
    notes: tuple[str, ...] = ()

    @property
    def gap(self) -> float:
        return self.iv - self.fair

    def summary(self) -> str:
        return f"IV {self.iv:.1%} vs fair {self.fair:.1%} (gap {self.gap:+.1%}): {self.reason}" + "".join(
            f"; {n}" for n in self.notes
        )


def explain_gap(
    iv: float,
    fair: float,
    rules: MispricingRules,
    *,
    earnings_inside: bool,
    implied_move: float | None = None,
    hist_move: float | None = None,
    relative_iv: float | None = None,
    news_event: str | None = None,
    vix: float | None = None,
    smile_notes: Sequence[str] = (),
) -> GapExplanation:
    """
    Walk through the usual reasons an option looks mispriced, most mechanical
    first, and keep only the residual as a trading signal:
      1. an earnings report inside the expiry (a jump fair vol cannot time);
      2. market-wide vol (AAPL's IV moved with ^VXN) vs AAPL-specific;
      3. for an AAPL-specific rich gap, a pending event in the news, which
         makes the price right rather than rich;
      4. a panic tape, where short vol is not opened at any price.
    `relative_iv` is AAPL IV / ^VXN divided by its usual relative-vol ratio.
    """
    side = mispricing_side(iv, fair, rules)
    notes: list[str] = list(smile_notes)
    if implied_move is not None and hist_move is not None:
        notes.append(f"earnings move priced {implied_move:.1%} vs historical {hist_move:.1%}")
    if side is None:
        return GapExplanation(iv, fair, None, False, "within the normal band", tuple(notes))
    if earnings_inside:
        return GapExplanation(iv, fair, side, False, "earnings inside the expiry: the jump explains it", tuple(notes))
    specific = relative_iv is not None and relative_iv >= rules.specific_ratio
    if relative_iv is not None:
        notes.append(
            f"AAPL IV at {relative_iv:.2f}x its usual ratio to ^VXN ({'AAPL-specific' if specific else 'market-wide'})"
        )
    if side == "rich" and vix is not None and vix >= rules.max_vix:
        return GapExplanation(
            iv, fair, side, False, f"VIX {vix:.0f} >= {rules.max_vix:.0f}: no short vol", tuple(notes)
        )
    if side == "rich" and specific and news_event:
        return GapExplanation(iv, fair, side, False, f"priced for a pending event: {news_event}", tuple(notes))
    if news_event:
        notes.append(f"news: {news_event}")
    return GapExplanation(iv, fair, side, True, f"unexplained {side} gap: trade it", tuple(notes))


# ------------------------------------------------------------------
# The wheel: cash-secured puts, then covered calls on assigned shares
# ------------------------------------------------------------------


@dataclass(frozen=True)
class WheelRules:
    put_delta: float = 0.30
    call_delta: float = 0.30
    target_dte: int = 35
    take_profit: float | None = 0.50  # buy back at this share of the credit; None holds to expiry
    exit_dte: int | None = None  # None: let it expire (and be assigned)
    min_iv_hv: float | None = None  # only sell puts when IV/HV is at least this
    hv_window: int = 20
    call_floor: str | None = "basis"  # "basis": no covered call struck below the shares' cost
    trend_filter: bool = False  # sell puts only while close > SMA200
    avoid_earnings: bool = False


def wheel_put_ok(iv_hv: float | None, close: float, sma200: float, earnings_ok: bool, rules: WheelRules) -> bool:
    if rules.min_iv_hv is not None and (iv_hv is None or iv_hv < rules.min_iv_hv):
        return False
    if rules.trend_filter and (np.isnan(sma200) or close <= sma200):
        return False
    return earnings_ok or not rules.avoid_earnings


def wheel_call_floor(share_cost: float | None, rules: WheelRules) -> float | None:
    """Lowest strike a covered call may use: the per-share cost of the assigned stock."""
    return share_cost if rules.call_floor == "basis" and share_cost else None


# ------------------------------------------------------------------
# Poor man's covered call: LEAP call + short near-dated calls
# ------------------------------------------------------------------


@dataclass(frozen=True)
class PMCCRules:
    long_delta: float = 0.80
    long_dte: int = 540
    long_roll_dte: int = 180
    short_delta: float = 0.30
    short_dte: int = 35
    short_take_profit: float | None = 0.50
    short_exit_dte: int | None = 7
    short_roll_delta: float = 0.60  # buy the short back once it is this deep: roll up and out
    max_iv_hv: float = 1.20  # LEAP entry, as the LEAP bot
    hv_window: int = 60
    exit_buffer: float = 0.0
    leverage: float = 1.0
    max_leverage: float | None = 1.5


def pmcc_leap_rules(rules: PMCCRules) -> LeapRules:
    """The LEAP half of the PMCC runs the LEAP bot's rules with the PMCC's parameters."""
    return LeapRules(
        delta=rules.long_delta,
        target_dte=rules.long_dte,
        roll_dte=rules.long_roll_dte,
        max_iv_hv=rules.max_iv_hv,
        hv_window=rules.hv_window,
        exit_buffer=rules.exit_buffer,
        leverage=rules.leverage,
        max_leverage=rules.max_leverage,
    )


def pmcc_min_short_strike(long_strike: float, long_cost: float, spot: float) -> float:
    """
    The short call's strike floor: LEAP strike plus the extrinsic value paid
    for it. Assigned below that, the diagonal locks in a loss.
    """
    return long_strike + max(long_cost - max(spot - long_strike, 0.0), 0.0)


def pmcc_short_exit_reason(
    credit: float, pnl: float, dte: int | None, short_delta: float | None, rules: PMCCRules
) -> str | None:
    if short_delta is not None and abs(short_delta) >= rules.short_roll_delta:
        return f"short call delta {short_delta:.2f} >= {rules.short_roll_delta}: roll up and out"
    return short_premium_exit_reason(credit, pnl, dte, rules.short_take_profit, rules.short_exit_dte)


# ------------------------------------------------------------------
# Collar: shares + long put + short call
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CollarRules:
    put_delta: float = 0.25
    call_delta: float = 0.25
    target_dte: int = 90
    roll_dte: int = 21
    # When to wear the hedge: "always", "below_sma200" (only once the trend
    # breaks), or "iv_cheap" (only when puts are cheap: IV/HV <= max_iv_hv).
    mode: str = "always"
    max_iv_hv: float = 1.0
    hv_window: int = 20
    cash_buffer: float = 0.03  # cash kept beside the shares for the collar's net debit


def collar_wanted(close: float, sma200: float, iv_hv: float | None, rules: CollarRules) -> bool:
    if rules.mode == "always":
        return True
    if rules.mode == "below_sma200":
        return not np.isnan(sma200) and close < sma200
    if rules.mode == "iv_cheap":
        return iv_hv is not None and iv_hv <= rules.max_iv_hv
    raise ValueError(f"CollarRules.mode={rules.mode!r}")


# ------------------------------------------------------------------
# Earnings calendar: short the inflated front expiry, long the next one
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarRules:
    entry_min_bdays: int = 3
    entry_max_bdays: int = 8
    front_max_days_after: int = 10  # front expiry at most this many days after the report
    back_min_days_after_front: int = 21
    min_term_ratio: float = 1.15  # front IV / back IV: the front must hold the event premium
    min_implied_vs_hist: float = 1.0  # implied earnings move / historical RMS move
    debit_pct: float = 0.05  # debit (= max loss) as a share of the book
    take_profit: float = 0.25
    stop_loss: float = 0.50
    right: str = "C"


def calendar_entry_window(today: date, earnings: date | None, rules: CalendarRules) -> bool:
    if earnings is None:
        return False
    return rules.entry_min_bdays <= business_days(today, earnings) <= rules.entry_max_bdays


def calendar_signal_ok(
    front_iv: float | None,
    back_iv: float | None,
    implied_move: float | None,
    hist_move: float | None,
    rules: CalendarRules,
) -> bool:
    if front_iv is None or back_iv is None or back_iv <= 0:
        return False
    if front_iv / back_iv < rules.min_term_ratio:
        return False
    if implied_move is None or hist_move is None or not hist_move > 0:
        return False
    return implied_move / hist_move >= rules.min_implied_vs_hist


def calendar_exit_reason(
    today: date, opened_on: date | None, last_report: date | None, pnl_pct: float, dte: int | None, rules: CalendarRules
) -> str | None:
    """Exit once the report is out (the crush is the trade), at a profit/stop, or near the front expiry."""
    if last_report is not None and opened_on is not None and opened_on <= last_report < today:
        return f"earnings {last_report} reported: take the crush"
    if pnl_pct >= rules.take_profit:
        return f"take profit {pnl_pct:.0%}"
    if pnl_pct <= -rules.stop_loss:
        return f"stop loss {pnl_pct:.0%}"
    if dte is not None and dte <= 2:
        return f"front expiry in {dte} days"
    return None
