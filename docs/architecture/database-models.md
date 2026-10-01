# Database Models

The system uses PostgreSQL with SQLAlchemy ORM. All models are defined in `tradingbot/utils/db.py`.

## Bot Model

Stores bot configuration and portfolio state.

```python
class Bot(Base):
    name: str  # Primary key
    description: str  # Optional description
    portfolio: dict  # JSON: {"USD": 10000, "QQQ": 5.5, ...}
    created_at: datetime
    updated_at: datetime
```

**Portfolio Format**: `{"USD": cash_amount, "SYMBOL": quantity, ...}`

## Trade Model

Logs all trade executions.

```python
class Trade(Base):
    id: int  # Auto-increment primary key
    bot_name: str  # Foreign key to Bot.name
    symbol: str  # Trading symbol
    isBuy: bool  # True for buy, False for sell
    quantity: float  # Number of shares/units
    price: float  # Price per unit
    timestamp: datetime  # Execution time
    profit: float  # Profit (for sells, nullable)
```

## HistoricData Model

Caches market data for performance.

```python
class HistoricData(Base):
    symbol: str  # Primary key (part of composite)
    timestamp: datetime  # Primary key (part of composite)
    open: float
    high: float
    low: float
    close: float
    volume: float
```

## RunLog Model

Tracks bot execution history.

```python
class RunLog(Base):
    id: int  # Auto-increment primary key
    bot_name: str  # Foreign key to Bot.name
    start_time: datetime  # When run started
    success: bool  # Whether run succeeded
    result: str  # Result message (nullable)
```

## PortfolioWorth Model

Historical portfolio valuations.

```python
class PortfolioWorth(Base):
    bot_name: str  # Primary key (part of composite)
    date: datetime  # Primary key (part of composite)
    portfolio_worth: float  # Total value in USD
    holdings: dict  # JSON snapshot of holdings
    created_at: datetime
```

## BotAlphaReport Model

Alpha vs QQQ per bot, weekly, over each bot's own live window. Written every
Saturday by `tradingbot/alphareport.py` (`utils/alpha_report.py`) from
`portfolio_worth`:
- weekend rows and day pairs more than 4 days apart are dropped;
- a worth of 0 or less counts as a failed valuation and is dropped too;
- the benchmark is `Benchmark_QQQ` over the same dates.

It uses the same formula as `backtest_bot`'s alpha keys (`alpha_report.alpha_stats`).

```python
class BotAlphaReport(Base):
    id: int  # Auto-increment primary key
    report_date: date  # the Saturday it was written (indexed)
    bot_name: str  # indexed
    window_start: date  # first and last shared weekday
    window_end: date
    n_obs: int  # daily returns used
    alpha: float  # annualised, nullable (too little data)
    alpha_t: float
    beta: float
    corr: float
    max_dd: float  # negative fraction
    bot_return: float  # over the window
    qqq_return: float  # QQQ over the same window
    verdict: str  # see below
    created_at: datetime
```

**Verdicts:**

| Verdict | When |
|---|---|
| `too short` | fewer than 60 daily returns |
| `edge` | t ≥ 2 |
| `pause candidate` | t ≤ −2 |
| `levered QQQ` | corr ≥ 0.8 and beta ≥ 1.5 |
| `QQQ clone` | beta and corr ≥ 0.8 |
| `unproven` | anything else |

**Unique constraint**: `(report_date, bot_name)`, so a rerun upserts, and older
report dates keep the history of each verdict.

## StockNews Model

News articles per symbol from yfinance (loaded daily with portfolio worth).

```python
class StockNews(Base):
    id: int  # Auto-increment primary key
    symbol: str  # Trading symbol (indexed)
    title: str  # Article title
    link: str  # Article URL
    publisher: str  # Publisher name (nullable)
    publisher_url: str  # Publisher URL (nullable)
    published_at: datetime  # When the article was published (UTC)
    related_tickers: list  # JSON array of related tickers (nullable)
    created_at: datetime
```

**Unique constraint**: `(symbol, link)` so the same article is not stored twice for a symbol. Index on `(symbol, published_at)` for efficient queries.

## StockEarnings Model

Earnings dates and results per symbol from yfinance. Two jobs load it:
- the nightly portfolio-worth run, for held symbols;
- `corporateeventssnapshot` (12:00 UTC weekdays), for the option universe.

Both upsert through `utils/corporate_events.upsert_earnings`, which does two things:
- an estimate-only row gets its actual EPS and surprise once reported (the
  loader used to insert only, so they never arrived);
- rows match on the report's New York date, so a calendar placeholder and the
  timed stamp are one report.

```python
class StockEarnings(Base):
    id: int  # Auto-increment primary key
    symbol: str  # Trading symbol (indexed)
    report_date: datetime  # Report timestamp, naive UTC, time of day kept
    eps_estimate: float  # Estimated EPS (nullable)
    reported_eps: float  # Reported EPS (nullable)
    surprise_pct: float  # Surprise percentage (nullable)
    fiscal_period: str  # Fiscal period if available (nullable)
    after_close: bool  # True after the close, False before the open, None unknown
    created_at: datetime
```

**Unique constraint**: `(symbol, report_date)` to avoid duplicate earnings rows. Index on `symbol`.

`after_close` comes from the New York time of yfinance's stamp. 12:00 or
later means after the close, earlier means before the open, and a bare
midnight means unknown.

## DividendEvent Model

Ex-dividend dates per symbol, written by `corporateeventssnapshot`
(`utils/corporate_events.py`):
- the last 10 years of paid dividends (`source="history"`);
- the next announced ex-date with the last amount paid (`source="calendar"`).

A paid amount replaces an announced one. An announced future date the calendar
no longer lists is deleted, because the date moved. The table is read by
`options.next_dividend`, and through it by the early-assignment checks and
the short-call bots.

```python
class DividendEvent(Base):
    id: int  # Auto-increment primary key
    symbol: str  # indexed
    ex_date: date
    amount: float  # per share
    source: str  # "history" or "calendar"
    created_at: datetime
```

**Unique constraint**: `(symbol, ex_date)`.

## CorporateEventRefresh Model

When each symbol's earnings and dividends were last refreshed, one row per
symbol. The readers (`options.earnings_events`, `next_earnings_date`,
`earnings_history` and `next_dividend`) trust the tables only for a symbol
refreshed within 3 days. Then an empty answer is real, such as a stock that
pays no dividend. Otherwise they fall back to yfinance and log a warning.

```python
class CorporateEventRefresh(Base):
    symbol: str  # Primary key
    refreshed_at: datetime  # naive UTC
    n_earnings: int  # rows yfinance returned on that refresh
    n_dividends: int
```

## StockInsiderTrade Model

Insider transactions per symbol from yfinance (loaded daily with portfolio worth).

```python
class StockInsiderTrade(Base):
    id: int  # Auto-increment primary key
    symbol: str  # Trading symbol (indexed)
    transaction_date: datetime  # Date of the transaction
    insider_name: str  # Name of the insider (nullable)
    transaction_type: str  # Type e.g. Purchase, Sale (nullable)
    shares: float  # Number of shares (nullable)
    value: float  # Transaction value if available (nullable)
    created_at: datetime
```

**Unique constraint**: `(symbol, transaction_date, insider_name, transaction_type, shares)`. Index on `(symbol, transaction_date)`.

## OptionQuote Model

Option-chain snapshots from yfinance. They are written on demand when a bot
trades or values an option (see `tradingbot/utils/options.py`), never on a
schedule.

```python
class OptionQuote(Base):
    id: int  # Auto-increment primary key
    underlying: str  # e.g. "AAPL" (indexed)
    contract_symbol: str  # OCC symbol, e.g. "AAPL261030C00200000" — the portfolio key
    expiration: datetime  # Expiry date, naive UTC midnight
    option_type: str  # "C" or "P"
    strike: float
    bid: float  # Per-share premium; NULL when snapshotted outside regular hours
    ask: float  # Per-share premium; NULL when snapshotted outside regular hours
    last_price: float  # Per-share premium
    volume: float
    open_interest: float
    implied_volatility: float
    snapshot_at: datetime  # Shared by every row of one fetch
    created_at: datetime
```

**Unique constraint**: `(contract_symbol, snapshot_at)`. Index on
`(contract_symbol, snapshot_at)`.

## MacroEvent Model

Scheduled macro releases: FOMC statements (a static table transcribed from
federalreserve.gov) and CPI / jobs-report dates (FRED's release calendar,
future scheduled dates included). Filled weekly by
`tradingbot/macrocalendarsnapshot.py`; read by option bots that avoid opening
short vol right before one. See `tradingbot/utils/macro_calendar.py`.

```python
class MacroEvent(Base):
    id: int  # Auto-increment primary key
    kind: str  # "FOMC" / "CPI" / "NFP"
    event_date: date  # Indexed
    source: str  # e.g. "federalreserve.gov" or "FRED release 10"
    created_at: datetime
```

**Unique constraint**: `(kind, event_date)`.

## PredictionMarketSnapshot Model

One daily price per prediction-market contract, for the hand-curated Kalshi
and Polymarket macro series in `tradingbot/utils/prediction_market_series.py`
(Fed path and decisions, CPI, payrolls, U3, GDP, recession, shutdown). Filled
daily by `tradingbot/predictionmarketsnapshot.py`; `--backfill` pulls each
market's full history (Kalshi from 2021-07, Polymarket from 2023-09).
`tradingbot/utils/prediction_market_features.py` turns the rows into
point-in-time features.

```python
class PredictionMarketSnapshot(Base):
    id: int  # Auto-increment primary key
    date: date  # US/Eastern day the price covers
    observed_at: datetime  # when it was observed (Kalshi: midnight ET after `date`)
    venue: str  # "kalshi" / "polymarket"
    series: str  # curated key, e.g. "fed_rate", "recession"
    event_ticker: str  # Kalshi event, or Polymarket event slug
    market_ticker: str  # Kalshi market ticker, or Polymarket market slug
    label: str  # outcome name of an event contract
    contract_type: str  # close_above / close_below / range / touch / event
    strike: float  # threshold (close_above/close_below) or range floor
    strike_cap: float  # range cap
    expiry: datetime  # contract close, naive UTC
    prob: float  # yes price 0..1 (last trade, else bid/ask mid)
    bid: float
    ask: float
    volume: float  # contracts traded that day
    open_interest: float
    result: str  # "yes" / "no" once settled
    captured_at: datetime
```

**Unique constraint**: `(venue, market_ticker, date)`. **Index**: `(series, date)`.

**Look-ahead:** a price for day D is observed after D's 16:00 ET close, so a
daily bar D may only use rows with `date <= D - 1`. `as_of_bars` applies that
shift.

## VolSurfaceSnapshot Model

One row per underlying per day: the day's `OptionQuote` capture condensed
into constant-maturity IV, skew, term slope, a HAR fair-vol forecast and its
gap to IV, and options "TA" (put/call ratios, dealer gamma exposure, max
pain, unusual activity). Written after each daily capture
(`tradingbot/utils/vol_surface.py`); every ranking bot (cross-vol, the
scanner, dispersion) reads it instead of re-scanning raw quotes.

```python
class VolSurfaceSnapshot(Base):
    id: int  # Auto-increment primary key
    underlying: str  # Indexed
    snapshot_date: date  # Indexed
    spot: float
    atm_iv_7: float  # Constant-maturity ATM IV, 7/30/60/90/180 days
    atm_iv_30: float
    atm_iv_60: float
    atm_iv_90: float
    atm_iv_180: float
    iv_25p_30: float  # 25-delta put/call IV at 30 days
    iv_25c_30: float
    rr25_30: float  # 25d put IV - 25d call IV (risk reversal)
    fly25_30: float  # wings average - ATM (butterfly)
    cp_spread_30: float  # call IV - put IV at matched strikes within 10% of spot (Cremers-Weinbaum)
    term_slope: float  # atm_iv_90 / atm_iv_30
    fair_vol_30: float  # HAR-RV forecast, 30 days
    vrp_30: float  # atm_iv_30 - fair_vol_30 (the variance risk premium)
    pc_volume: float  # Put/call ratio, by volume
    pc_oi: float  # Put/call ratio, by open interest
    gex_usd: float  # Dealer $ gamma exposure per 1% move (calls +, puts -)
    max_pain: float  # Strike where open option holders lose the most at expiry
    unusual_count: int  # Contracts trading > 3x open interest
    n_contracts: int
    created_at: datetime
```

**Unique constraint**: `(underlying, snapshot_date)`.

## ImpliedCorrelation Model

Daily implied correlation of an index (SPY) against its largest captured
members, from 30-day ATM IVs and market-cap weights. Feeds
`option_DispersionBot`, which trades nothing until 60 observations exist.

```python
class ImpliedCorrelation(Base):
    id: int  # Auto-increment primary key
    index_symbol: str  # e.g. "SPY"
    snapshot_date: date  # Indexed
    value: float  # (index_iv^2 - sum(w_i^2 sigma_i^2)) / sum(cross terms)
    index_iv: float
    n_names: int  # Members with a surface row that day
    weight_coverage: float  # n_names / captured members
    created_at: datetime
```

**Unique constraint**: `(index_symbol, snapshot_date)`.

## OptionRiskSnapshot Model

Daily risk of every option bot's book, per underlying: dollar greeks,
scenario P&L under spot/vol shocks, worst-case loss at expiry, and reserved
margin. Written after the close by `tradingbot/optionrisksnapshot.py`
(`tradingbot/utils/option_risk.py`); marks come from the newest stored quote,
never a refetch.

```python
class OptionRiskSnapshot(Base):
    id: int  # Auto-increment primary key
    bot_name: str  # Indexed
    underlying: str
    snapshot_date: date  # Indexed
    spot: float
    value: float
    delta_usd: float
    gamma_usd: float  # $ delta change per 1% move
    vega: float  # $ per vol point
    theta: float  # $ per day
    stress_down20: float  # P&L: spot -20%
    stress_down10: float
    stress_up10: float
    stress_vol_up10: float  # P&L: vol +10 points, spot unchanged
    stress_crash: float  # P&L: spot -20%, vol +30 points
    max_loss: float  # Worst case at expiry, from entry prices
    margin: float  # Reserved margin (utils/options.margin_requirement)
    created_at: datetime
```

**Unique constraint**: `(bot_name, underlying, snapshot_date)`.

## MispricingScanRow Model

One candidate from the daily vol-mispricing scan
(`tradingbot/utils/mispricing_scan.py`), written after each capture and read
by the cross-vol and scanner bots to shortlist which live chains to load.

```python
class MispricingScanRow(Base):
    id: int  # Auto-increment primary key
    scan_date: date  # Indexed
    underlying: str  # Indexed
    expiry: date  # Nullable
    kind: str  # "vrp" | "svi" | "event" | "parity"
    strike: float  # Nullable (set for "svi" / "parity")
    right: str  # "C" / "P", nullable
    iv: float  # Nullable
    fair: float  # Nullable: forecast vol ("vrp") or SVI-fitted IV ("svi")
    z: float  # Nullable: z-score of the gap against the name's own history
    half_spread_vol: float  # Nullable: bid/ask half-spread in vol terms
    score: float  # Nullable: ranking value (z for "vrp", resid/half-spread for "svi")
    note: str  # Human-readable summary, nullable
    created_at: datetime
```

No unique constraint — several rows per underlying per day (one per `kind`,
and several `svi`/`parity` rows per strike).

- **"vrp"**: ATM IV against a Yang-Zhang HAR forecast, z-scored against the
  name's own `vol_surface.vrp_30` history (`z` is `None` below 60 observations).
- **"svi"**: a strike off an arbitrage-free SVI fit (`utils/svi.py`) by more
  than half its own bid/ask spread in vol terms.
- **"event"**: the implied earnings move against the historical RMS move
  (logged for the record; `option_EarningsCrushBot` computes this live rather
  than reading it, since it needs the report's exact timing).
- **"parity"**: a strike pair outside the American put-call band at bid/ask —
  bad data (stale quote, dividend, borrow), excluded from the rest, never
  traded.

## TelegramMessage Model

Monitored Telegram channel messages with AI summaries (written by the Telegram monitor CronJob).

```python
class TelegramMessage(Base):
    id: int  # Auto-increment primary key
    channel: str  # Channel username or ID (indexed)
    message_id: int  # Telegram message ID (unique per channel)
    text: str  # Original message text (nullable, max 4000 chars)
    summary: str  # AI-generated 1-3 sentence summary (nullable)
    symbol: str  # Primary ticker extracted by AI e.g. "AAPL" (nullable, indexed)
    acted_on: bool  # True once the signals bot has evaluated this message (default False)
    published_at: datetime  # When the message was posted in Telegram (UTC)
    created_at: datetime
```

**Unique constraint**: `(channel, message_id)` — same message never stored twice.
**Indexes**: `(channel, published_at)`, `symbol` — efficient queries by symbol or channel timeline.

`acted_on` is set to `True` by `telegramsignalsbankbot` **before** the AI classification call — crash-safe deduplication without a separate tracking table.

See [Telegram Monitor Guide](../guides/telegram-monitor.md) and [Telegram Signals Bot Guide](../guides/telegram-signals-bot.md) for setup and usage.

## Session Management

Always use the context manager:

```python
from tradingbot.utils.db import get_db_session

with get_db_session() as session:
    bot = session.query(Bot).filter_by(name="MyBot").first()
    # Context manager commits automatically
```

The context manager handles:
- Automatic commit on success
- Automatic rollback on exceptions
- Connection retry logic (3 attempts with exponential backoff)
- Proper session cleanup

## Next Steps

- [Database API Reference](../api/database.md) - Complete API docs
- [Architecture Overview](overview.md) - System design
