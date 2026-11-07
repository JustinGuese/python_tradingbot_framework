"""
Onetime event study: Buyback announcements in SEC Form 8-K (Item 8.01 or 7.01).

Investigates whether long-only holding of S&P 100 stocks following Form 8-K
share repurchase / buyback authorization announcements generates alpha vs QQQ.

Universe: S&P 100 (tradingbot.utils.universes.SP100).
Events: 8-Ks (Item 8.01 or 7.01) matching buyback authorization regex from 2005 to present.
Deduplication: max 1 event per company per 30 days.
Look-ahead guard: entry is the first daily close strictly after acceptance time.
Costs: net of 2 bp/side and 5 bp/side round-trip deduction.

12 Pre-registered Variants (anti-p-hacking grid):
- Hold 5 / 10 / 20 sessions
x Equal-weight (All) vs Size-bucket (Above median market cap)
x Without filter vs With abnormal-return-vs-QQQ announcement reaction filter (>0)
= 3 x 2 x 2 = 12 variants.

Usage:
    PYTHONPATH=. python scripts/onetime_event_buyback_study.py [--cap N] [--no-cache]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
import html
import json
import os
from pathlib import Path
import pickle
import re
import sys
import time as time_mod
import urllib.error
import urllib.request
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

# Pure & math utilities from framework
import tradingbot.utils.event_study as es
from tradingbot.utils.event_study import (
    _bar_close_utc,
    _find_entry_bar_idx,
    _to_utc_timestamp,
    judge,
)
from tradingbot.utils.universes import SP100

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------
CACHE_DIR = Path(".cache")
EDGAR_CACHE_DIR = CACHE_DIR / "edgar"
SUBMISSIONS_CACHE_DIR = EDGAR_CACHE_DIR / "submissions"
DOCS_CACHE_DIR = EDGAR_CACHE_DIR / "docs"

SEC_USER_AGENT = "tradingbot-research"
MAX_EDGAR_REQ_PER_SEC = 5.0
DEFAULT_START_DATE = "2005-01-01"

# Buyback / repurchase authorization regex
BUYBACK_REGEX = re.compile(
    r"("
    r"(?:share|stock)\s+(?:repurchase|buyback)\s+(?:program|plan|authorization|facility)"
    r"|(?:authorized|approved|announced)\b[^\.\n;]{0,100}\b(?:share|stock)?\s*(?:repurchase|buyback)"
    r"|(?:repurchase|buyback)\s+(?:of\s+up\s+to\s+)?(?:\$[\d\.]+\s*(?:billion|million)?|\d+\s*(?:billion|million)\s*(?:shares|dollars))"
    r"|(?:repurchase|buyback)\s+(?:program|authorization)\s+of\s+up\s+to"
    r")",
    re.IGNORECASE,
)

# 12 pre-registered variants
HOLD_WINDOWS: tuple[int, ...] = (5, 10, 20)
SIZE_BUCKETS: tuple[str, ...] = ("all", "above_median")
FILTERS: tuple[bool, ...] = (False, True)

PRE_REGISTERED_VARIANTS: list[tuple[int, str, bool]] = [
    (h, s, f) for h in HOLD_WINDOWS for s in SIZE_BUCKETS for f in FILTERS
]


# ---------------------------------------------------------------------------
# Rate Limiter & EDGAR Cache Fetcher
# ---------------------------------------------------------------------------
class RateLimiter:
    """Enforces max requests per second."""

    def __init__(self, max_per_second: float = MAX_EDGAR_REQ_PER_SEC):
        import threading

        self.interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self.next_slot = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:  # reserve the next free slot, then sleep outside the lock
            now = time_mod.time()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.interval
        if slot > now:
            time_mod.sleep(slot - now)


RATE_LIMITER = RateLimiter(MAX_EDGAR_REQ_PER_SEC)


def fetch_edgar_bytes(url: str, cache_file: Path | None = None, use_cache: bool = True) -> bytes:
    """Fetch URL with rate limiting, generic User-Agent, and disk caching."""
    if use_cache and cache_file is not None and cache_file.exists():
        try:
            return cache_file.read_bytes()
        except Exception:
            pass

    RATE_LIMITER.wait()
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc

    if cache_file is not None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(data)
        except Exception:
            pass

    return data


# ---------------------------------------------------------------------------
# Pure Helper Functions (Text, Date parsing, Deduplication, Size bucketing)
# ---------------------------------------------------------------------------
def clean_html(raw_html: str) -> str:
    """Strip HTML tags and unescape text."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return " ".join(text.split())


def matches_buyback_regex(text: str) -> bool:
    """Test if text contains buyback authorization terms."""
    if not text:
        return False
    return bool(BUYBACK_REGEX.search(text))


def has_item_801_or_701(items_val: object) -> bool:
    """Check if 8-K filing item is Item 8.01 (Other Events) or 7.01 (Reg FD)."""
    if items_val is None:
        return False
    if isinstance(items_val, (list, tuple)):
        return any("8.01" in str(x) or "7.01" in str(x) for x in items_val)
    s = str(items_val)
    return "8.01" in s or "7.01" in s


def parse_acceptance_datetime(dt_str: str | None, filing_date_str: str | None = None) -> pd.Timestamp:
    """
    Parse EDGAR acceptanceDateTime into tz-aware UTC Timestamp.
    Per anti-p-hacking rules: if only a date is known, set timestamp to 23:59:59 UTC
    of the filing date, which guarantees entering at the next session close.
    """
    if dt_str:
        dt_str = str(dt_str).strip()
        # ISO format: 2024-05-02T16:30:15.000Z
        if "T" in dt_str or "-" in dt_str:
            try:
                ts = pd.Timestamp(dt_str)
                if ts.tzinfo is None:
                    return ts.tz_localize(UTC)
                return ts.tz_convert(UTC)
            except Exception:
                pass
        # Compact format: 20240502163015
        if len(dt_str) == 14 and dt_str.isdigit():
            try:
                ts = pd.to_datetime(dt_str, format="%Y%m%d%H%M%S", utc=True)
                return pd.Timestamp(ts)
            except Exception:
                pass

    if filing_date_str:
        d = pd.Timestamp(filing_date_str).date()
        return pd.Timestamp(datetime.combine(d, time(23, 59, 59), tzinfo=UTC))

    raise ValueError(f"Unable to parse timestamp: dt_str={dt_str}, filing_date_str={filing_date_str}")


def dedupe_events_by_symbol(events: pd.DataFrame, window_days: int = 30) -> pd.DataFrame:
    """
    Deduplicate events per company within a rolling window (default 30 days).
    Keeps only the first event and drops subsequent events within window_days.
    """
    if events is None or events.empty:
        return pd.DataFrame(columns=["symbol", "acceptance_dt", "filing_date"])

    df = events.copy().sort_values("acceptance_dt").reset_index(drop=True)
    kept_indices: list[int] = []
    last_event_by_sym: dict[str, pd.Timestamp] = {}

    for idx, row in df.iterrows():
        sym = str(row["symbol"])
        ts = pd.Timestamp(row["acceptance_dt"])
        if sym not in last_event_by_sym:
            kept_indices.append(idx)
            last_event_by_sym[sym] = ts
        else:
            diff_days = (ts - last_event_by_sym[sym]).total_seconds() / 86400.0
            if diff_days >= window_days:
                kept_indices.append(idx)
                last_event_by_sym[sym] = ts

    return df.iloc[kept_indices].reset_index(drop=True)


def classify_market_cap_bucket(
    symbols: list[str],
    market_caps: dict[str, float],
) -> tuple[dict[str, str], float]:
    """
    Partition symbols into above_median vs below_median market cap buckets.
    Returns (symbol_to_bucket, median_val).
    """
    valid = {s: market_caps[s] for s in symbols if s in market_caps and market_caps[s] is not None and market_caps[s] > 0}
    if not valid:
        return {s: "above_median" for s in symbols}, 0.0

    med = float(np.median(list(valid.values())))
    buckets = {}
    for s in symbols:
        val = valid.get(s, med)
        buckets[s] = "above_median" if val >= med else "below_median"
    return buckets, med


def compute_announcement_abnormal_return(
    stock_prices: pd.Series,
    qqq_prices: pd.Series,
    entry_idx: int,
    beta: float = 1.0,
) -> float:
    """
    Announcement reaction abnormal return on the entry bar.
    Entry close vs previous bar close:
        AR = (p_entry - p_prev)/p_prev - beta * (q_entry - q_prev)/q_prev
    Look-ahead safe: computed strictly at the entry close.
    """
    if entry_idx <= 0 or entry_idx >= len(stock_prices):
        return 0.0

    p_entry = float(stock_prices.iloc[entry_idx])
    p_prev = float(stock_prices.iloc[entry_idx - 1])
    if p_prev <= 0 or not np.isfinite(p_entry):
        return 0.0
    r_stock = (p_entry - p_prev) / p_prev

    entry_date = stock_prices.index[entry_idx]
    prev_date = stock_prices.index[entry_idx - 1]

    if entry_date not in qqq_prices.index or prev_date not in qqq_prices.index:
        return r_stock

    q_entry = float(qqq_prices.loc[entry_date])
    q_prev = float(qqq_prices.loc[prev_date])
    if q_prev <= 0 or not np.isfinite(q_entry):
        return r_stock
    r_qqq = (q_entry - q_prev) / q_prev

    return float(r_stock - beta * r_qqq)


def strategy_returns_with_costs(
    signals: pd.DataFrame,
    prices: dict[str, pd.Series],
    hold_days: int,
    cost_per_side: float = 0.0002,
) -> pd.Series:
    """
    Equal-weight book daily returns holding signalled symbols for hold_days sessions,
    with per-side execution costs applied on entry and exit bars.
    Flat 0.0 on days with no active holdings.
    """
    if not prices:
        return pd.Series(dtype=float)

    clean_prices: dict[str, pd.Series] = {}
    for sym, p in prices.items():
        if p is not None and len(p) >= 2:
            s = p.dropna()
            if s.index.has_duplicates:
                s = s[~s.index.duplicated(keep="last")]
            clean_prices[sym] = s.sort_index()

    if not clean_prices:
        return pd.Series(dtype=float)

    all_dates = sorted(set().union(*(set(p.index) for p in clean_prices.values())))
    if len(all_dates) < 2:
        return pd.Series(dtype=float)

    return_dates = all_dates[1:]
    if hold_days <= 0 or signals is None or signals.empty:
        return pd.Series(0.0, index=pd.DatetimeIndex(return_dates))

    active_returns_by_date: dict[pd.Timestamp, dict[str, float]] = {d: {} for d in return_dates}

    for _, sig in signals.iterrows():
        sym = str(sig["symbol"])
        if sym not in clean_prices:
            continue
        p_series = clean_prices[sym]
        sig_ts_utc = _to_utc_timestamp(sig["acceptance_dt"])
        entry_idx = _find_entry_bar_idx(p_series, sig_ts_utc)
        if entry_idx is None:
            continue

        start_hold_idx = entry_idx + 1
        end_hold_idx = min(start_hold_idx + hold_days, len(p_series))

        for j in range(start_hold_idx, end_hold_idx):
            held_date = p_series.index[j]
            prev_price = float(p_series.iloc[j - 1])
            curr_price = float(p_series.iloc[j])
            if prev_price > 0 and np.isfinite(curr_price):
                daily_ret = (curr_price - prev_price) / prev_price
                # Apply cost per side on entry session (first session of holding)
                if j == start_hold_idx:
                    daily_ret -= cost_per_side
                # Apply cost per side on exit session (last session of holding)
                if j == end_hold_idx - 1:
                    daily_ret -= cost_per_side

                if held_date in active_returns_by_date:
                    active_returns_by_date[held_date][sym] = daily_ret

    daily_rets = []
    for d in return_dates:
        held_dict = active_returns_by_date[d]
        if not held_dict:
            daily_rets.append(0.0)
        else:
            daily_rets.append(float(np.mean(list(held_dict.values()))))

    return pd.Series(daily_rets, index=pd.DatetimeIndex(return_dates), dtype=float)


# ---------------------------------------------------------------------------
# SEC EDGAR Data Loaders
# ---------------------------------------------------------------------------
def load_sec_ticker_to_cik(use_cache: bool = True) -> dict[str, int]:
    """Load ticker to CIK mapping from SEC EDGAR company_tickers.json."""
    cache_file = EDGAR_CACHE_DIR / "company_tickers.json"
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        raw = fetch_edgar_bytes(url, cache_file=cache_file, use_cache=use_cache)
        data = json.loads(raw.decode("utf-8"))
        return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
    except Exception as exc:
        warnings.warn(f"Failed to fetch company_tickers.json: {exc}")
        return {}


def load_company_8k_filings(
    symbol: str,
    cik: int,
    start_date: str = DEFAULT_START_DATE,
    use_cache: bool = True,
) -> list[dict]:
    """
    Load 8-K filings with Item 8.01 or 7.01 for a company from EDGAR submissions JSON.
    Recursively inspects older submissions files in `filings.files` if needed.
    """
    cik_str = str(cik).zfill(10)
    cache_file = SUBMISSIONS_CACHE_DIR / f"CIK{cik_str}.json"
    url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"

    try:
        raw = fetch_edgar_bytes(url, cache_file=cache_file, use_cache=use_cache)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        warnings.warn(f"{symbol} (CIK {cik}): Failed to load submissions: {exc}")
        return []

    filings_list: list[dict] = []

    def parse_filing_group(group: dict) -> None:
        forms = group.get("form", [])
        dates = group.get("filingDate", [])
        accessions = group.get("accessionNumber", [])
        items_list = group.get("items", [])
        acceptances = group.get("acceptanceDateTime", [])
        primary_docs = group.get("primaryDocument", [])

        n = len(forms)
        for i in range(n):
            form = str(forms[i]).upper()
            fdate = str(dates[i])
            if form != "8-K" or fdate < start_date:
                continue

            items_val = items_list[i] if i < len(items_list) else None
            if not has_item_801_or_701(items_val):
                continue

            acc = str(accessions[i])
            pdoc = str(primary_docs[i]) if i < len(primary_docs) else ""
            acc_dt = str(acceptances[i]) if i < len(acceptances) else None

            filings_list.append({
                "symbol": symbol,
                "cik": cik,
                "filing_date": fdate,
                "acceptance_dt": parse_acceptance_datetime(acc_dt, fdate),
                "accession_number": acc,
                "primary_document": pdoc,
                "items": items_val,
            })

    # 1. Parse recent filings
    recent = data.get("filings", {}).get("recent", {})
    if recent:
        parse_filing_group(recent)

    # 2. Check older files in filings.files
    older_files = data.get("filings", {}).get("files", [])
    for ofile in older_files:
        filing_to = ofile.get("filingTo", "")
        if filing_to and filing_to < start_date:
            continue
        fname = ofile.get("name")
        if not fname:
            continue
        of_cache = SUBMISSIONS_CACHE_DIR / fname
        of_url = f"https://data.sec.gov/submissions/{fname}"
        try:
            of_raw = fetch_edgar_bytes(of_url, cache_file=of_cache, use_cache=use_cache)
            of_data = json.loads(of_raw.decode("utf-8"))
            parse_filing_group(of_data)
        except Exception:
            pass

    return filings_list


def filter_filings_by_buyback_primary_doc(
    filings: list[dict],
    use_cache: bool = True,
) -> list[dict]:
    """
    Inspect the primary document of each candidate 8-K filing.
    Keeps only filings whose text matches the buyback regex.
    """
    from concurrent.futures import ThreadPoolExecutor

    def check(f: dict) -> dict | None:
        cik = f["cik"]
        acc_clean = f["accession_number"].replace("-", "")
        pdoc = f["primary_document"]
        if not pdoc:
            return None
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{pdoc}"
        cache_file = DOCS_CACHE_DIR / f"{cik}_{acc_clean}_{pdoc}"
        raw_bytes = fetch_edgar_bytes(doc_url, cache_file=cache_file, use_cache=use_cache)
        text = clean_html(raw_bytes.decode("utf-8", errors="replace"))
        return dict(f) if matches_buyback_regex(text) else None

    def safe(f: dict) -> tuple[dict | None, bool]:
        try:
            return check(f), False
        except Exception:
            return None, True

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(safe, filings))
    matched = [m for m, _ in results if m is not None]
    failures = sum(1 for _, failed in results if failed)
    print(f"Filings checked: {len(filings)}, matched: {len(matched)}, fetch failures: {failures}", flush=True)
    return matched


# ---------------------------------------------------------------------------
# Market Caps & Price Fetching
# ---------------------------------------------------------------------------
def load_market_caps(symbols: list[str], use_cache: bool = True) -> dict[str, float]:
    """
    Fetch market caps for S&P 100 constituents with local pickle cache.
    """
    cache_file = CACHE_DIR / "sp100_market_caps.pkl"
    if use_cache and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached = pickle.load(f)
            if isinstance(cached, dict) and set(symbols).issubset(cached.keys()):
                return cached
        except Exception:
            pass

    caps: dict[str, float] = {}
    for s in symbols:
        try:
            t = yf.Ticker(s)
            val = t.fast_info.get("marketCap")
            if val is not None and val > 0:
                caps[s] = float(val)
        except Exception:
            pass

    if use_cache and caps:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "wb") as f:
                pickle.dump(caps, f)
        except Exception:
            pass

    return caps


def load_price_history(
    symbols: list[str],
    start_date: str = "2004-01-01",
    use_cache: bool = True,
) -> dict[str, pd.Series]:
    """Download daily close prices for symbols + QQQ with pickle cache."""
    cache_file = CACHE_DIR / "event_buyback_prices.pkl"
    all_syms = sorted(set(symbols + ["QQQ"]))

    if use_cache and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached = pickle.load(f)
            if isinstance(cached, dict) and set(all_syms).issubset(cached.keys()):
                return {s: cached[s].dropna().sort_index() for s in all_syms}
        except Exception:
            pass

    df = yf.download(all_syms, start=start_date, auto_adjust=True, progress=False)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame()

    prices = {s: df[s].dropna().sort_index() for s in all_syms if s in df.columns}
    if use_cache and prices:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "wb") as f:
                pickle.dump(prices, f)
        except Exception:
            pass

    return prices


# ---------------------------------------------------------------------------
# Precomputed Rolling Betas
# ---------------------------------------------------------------------------
def precompute_betas(prices: dict[str, pd.Series], bench: pd.Series) -> dict[str, pd.Series]:
    """Rolling 250d pre-event beta series vs benchmark for each stock."""
    betas: dict[str, pd.Series] = {}
    bench_clean = bench.dropna().sort_index()
    b_ret = bench_clean.pct_change().dropna()
    b_idx = pd.to_datetime([pd.Timestamp(t).date() for t in b_ret.index])
    b_ret = pd.Series(b_ret.to_numpy(dtype=float), index=b_idx)
    b_ret = b_ret[~b_ret.index.duplicated(keep="last")]

    for sym, p in prices.items():
        if sym == "QQQ":
            continue
        p_clean = p.dropna().sort_index()
        s_ret = p_clean.pct_change().dropna()
        s_idx = pd.to_datetime([pd.Timestamp(t).date() for t in s_ret.index])
        s_ret = pd.Series(s_ret.to_numpy(dtype=float), index=s_idx)
        s_ret = s_ret[~s_ret.index.duplicated(keep="last")]

        joint = pd.concat({"s": s_ret, "b": b_ret}, axis=1, join="inner").sort_index()
        r_cov = joint["s"].rolling(250, min_periods=20).cov(joint["b"])
        r_var = joint["b"].rolling(250, min_periods=20).var()
        rolling_b = (r_cov / r_var).shift(1).fillna(1.0)
        betas[sym] = rolling_b

    return betas


# ---------------------------------------------------------------------------
# Variant Evaluation & Placebo
# ---------------------------------------------------------------------------
@dataclass
class VariantResult:
    hold_days: int
    size_bucket: str
    ar_filter: bool
    n_events: int
    ar_mean_pct: float
    ar_t: float
    ar_win_pct: float
    strat_alpha_2bp_pct: float | None
    strat_t_2bp: float | None
    strat_beta: float | None
    strat_corr: float | None
    h1_alpha_2bp_pct: float | None
    h1_t_2bp: float | None
    h2_alpha_2bp_pct: float | None
    h2_t_2bp: float | None
    strat_alpha_5bp_pct: float | None
    strat_t_5bp: float | None
    passes: bool


def evaluate_variant(
    events_df: pd.DataFrame,
    prices: dict[str, pd.Series],
    qqq_prices: pd.Series,
    hold_days: int,
    size_bucket: str,
    ar_filter: bool,
    market_cap_buckets: dict[str, str],
    precomputed_betas: dict[str, pd.Series],
) -> VariantResult:
    """Evaluate one strategy variant on filtered event signals."""
    if events_df.empty:
        return VariantResult(
            hold_days, size_bucket, ar_filter, 0, 0.0, 0.0, 0.0,
            None, None, None, None, None, None, None, None, None, None, False
        )

    # 1. Size filter
    filtered = events_df.copy()
    if size_bucket == "above_median":
        filtered = filtered[filtered["symbol"].map(lambda s: market_cap_buckets.get(s) == "above_median")]

    # 2. Announcement AR filter
    if ar_filter:
        valid_indices = []
        for idx, row in filtered.iterrows():
            sym = str(row["symbol"])
            p_s = prices.get(sym)
            if p_s is None:
                continue
            entry_idx = _find_entry_bar_idx(p_s, row["acceptance_dt"])
            if entry_idx is None or entry_idx == 0:
                continue
            beta_s = precomputed_betas.get(sym)
            entry_dt = pd.Timestamp(p_s.index[entry_idx]).date()
            beta = float(beta_s.asof(pd.Timestamp(entry_dt))) if beta_s is not None and len(beta_s) > 0 else 1.0
            if not np.isfinite(beta):
                beta = 1.0
            ar_ann = compute_announcement_abnormal_return(p_s, qqq_prices, entry_idx, beta)
            if ar_ann > 0:
                valid_indices.append(idx)
        filtered = filtered.loc[valid_indices].reset_index(drop=True)

    n_events = len(filtered)
    if n_events < 6:
        return VariantResult(
            hold_days, size_bucket, ar_filter, n_events, 0.0, 0.0, 0.0,
            None, None, None, None, None, None, None, None, None, None, False
        )

    # Calculate forward abnormal returns over hold_days
    abnormal_vals = []
    for _, row in filtered.iterrows():
        sym = str(row["symbol"])
        p_s = prices.get(sym)
        if p_s is None:
            continue
        entry_idx = _find_entry_bar_idx(p_s, row["acceptance_dt"])
        if entry_idx is None or entry_idx + hold_days >= len(p_s):
            continue
        entry_p = float(p_s.iloc[entry_idx])
        exit_p = float(p_s.iloc[entry_idx + hold_days])
        if entry_p <= 0:
            continue
        r_stock = (exit_p - entry_p) / entry_p

        entry_date = p_s.index[entry_idx]
        exit_date = p_s.index[entry_idx + hold_days]
        if entry_date not in qqq_prices.index or exit_date not in qqq_prices.index:
            continue
        q_entry = float(qqq_prices.loc[entry_date])
        q_exit = float(qqq_prices.loc[exit_date])
        if q_entry <= 0:
            continue
        r_qqq = (q_exit - q_entry) / q_entry

        beta_s = precomputed_betas.get(sym)
        entry_dt = pd.Timestamp(entry_date).date()
        beta = float(beta_s.asof(pd.Timestamp(entry_dt))) if beta_s is not None and len(beta_s) > 0 else 1.0
        if not np.isfinite(beta):
            beta = 1.0
        abnormal_vals.append(r_stock - beta * r_qqq)

    if abnormal_vals:
        ar_arr = np.array(abnormal_vals)
        ar_mean_pct = float(np.mean(ar_arr) * 100.0)
        ar_std = float(np.std(ar_arr, ddof=1)) if len(ar_arr) > 1 else 0.0
        ar_t = float(ar_mean_pct / (ar_std * 100.0 / np.sqrt(len(ar_arr)))) if ar_std > 0 else 0.0
        ar_win_pct = float(np.mean(ar_arr > 0) * 100.0)
    else:
        ar_mean_pct, ar_t, ar_win_pct = 0.0, 0.0, 0.0

    # Build portfolio returns net of 2 bp and net of 5 bp
    signals_df = filtered[["acceptance_dt", "symbol"]].copy()
    bench_rets = qqq_prices.pct_change().dropna()

    strat_rets_2bp = strategy_returns_with_costs(signals_df, prices, hold_days=hold_days, cost_per_side=0.0002)
    judgement_2bp = judge(strat_rets_2bp, bench_rets)

    strat_rets_5bp = strategy_returns_with_costs(signals_df, prices, hold_days=hold_days, cost_per_side=0.0005)
    judgement_5bp = judge(strat_rets_5bp, bench_rets)

    strat_alpha_2bp_pct = judgement_2bp["alpha"] * 100.0 if judgement_2bp["alpha"] is not None else None
    strat_t_2bp = judgement_2bp["t"]
    strat_beta = judgement_2bp["beta"]
    strat_corr = judgement_2bp["corr"]
    h1_alpha_2bp_pct = judgement_2bp["alpha_h1"] * 100.0 if judgement_2bp["alpha_h1"] is not None else None
    h1_t_2bp = judgement_2bp["t_h1"]
    h2_alpha_2bp_pct = judgement_2bp["alpha_h2"] * 100.0 if judgement_2bp["alpha_h2"] is not None else None
    h2_t_2bp = judgement_2bp["t_h2"]

    strat_alpha_5bp_pct = judgement_5bp["alpha"] * 100.0 if judgement_5bp["alpha"] is not None else None
    strat_t_5bp = judgement_5bp["t"]

    passes = bool(judgement_2bp["passes"])

    return VariantResult(
        hold_days=hold_days,
        size_bucket=size_bucket,
        ar_filter=ar_filter,
        n_events=n_events,
        ar_mean_pct=ar_mean_pct,
        ar_t=ar_t,
        ar_win_pct=ar_win_pct,
        strat_alpha_2bp_pct=strat_alpha_2bp_pct,
        strat_t_2bp=strat_t_2bp,
        strat_beta=strat_beta,
        strat_corr=strat_corr,
        h1_alpha_2bp_pct=h1_alpha_2bp_pct,
        h1_t_2bp=h1_t_2bp,
        h2_alpha_2bp_pct=h2_alpha_2bp_pct,
        h2_t_2bp=h2_t_2bp,
        strat_alpha_5bp_pct=strat_alpha_5bp_pct,
        strat_t_5bp=strat_t_5bp,
        passes=passes,
    )


def run_placebo_test(
    observed_t: float,
    n_events: int,
    hold_days: int,
    prices: dict[str, pd.Series],
    qqq_prices: pd.Series,
    n_draws: int = 2000,
    seed: int = 42,
) -> float:
    """
    Monte Carlo placebo test:
    Draws random same-count event dates and symbols across the timeline,
    computes strategy t-stat vs QQQ, and returns p = P(t_sim >= t_obs).
    """
    if n_events <= 0 or not prices:
        return 1.0

    symbols = list(prices.keys())
    if "QQQ" in symbols:
        symbols.remove("QQQ")
    if not symbols:
        return 1.0

    qqq_clean = qqq_prices.dropna().sort_index()
    bench_rets = qqq_clean.pct_change().dropna()
    dates = list(qqq_clean.index)
    if len(dates) < hold_days + 10:
        return 1.0

    valid_dates = dates[:-hold_days]
    rng = np.random.default_rng(seed)

    exceed_count = 0
    t_start = time_mod.time()
    for i_draw in range(n_draws):
        if i_draw and i_draw % 25 == 0:
            print(f"  placebo {i_draw}/{n_draws} draws, {time_mod.time() - t_start:.0f}s elapsed, exceed so far {exceed_count}", flush=True)
        rand_dates = rng.choice(valid_dates, size=n_events, replace=True)
        rand_syms = rng.choice(symbols, size=n_events, replace=True)
        sim_signals = pd.DataFrame({
            "acceptance_dt": [pd.Timestamp(d).tz_localize(UTC) if pd.Timestamp(d).tzinfo is None else pd.Timestamp(d).tz_convert(UTC) for d in rand_dates],
            "symbol": rand_syms,
        })
        sim_rets = strategy_returns_with_costs(sim_signals, prices, hold_days=hold_days, cost_per_side=0.0002)
        jd = judge(sim_rets, bench_rets)
        t_sim = jd["t"]
        if t_sim is not None and t_sim >= observed_t:
            exceed_count += 1

    return float(exceed_count / n_draws)


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def format_markdown_report(
    results: list[VariantResult],
    verdict: str,
    total_events_found: int,
    deduped_events_count: int,
    symbols_scanned: int,
    placebo_p: float | None = None,
) -> str:
    """Generate docs/backtests/event-buyback-2026-11.md document text."""
    passing = [r for r in results if r.passes]

    lines = [
        "# Form 8-K Buyback Announcements — Backtest Study",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Overview",
        "",
        "Tests whether purchasing S&P 100 constituent stocks following Form 8-K share repurchase",
        "and buyback authorizations (Item 8.01 or 7.01) yields statistically significant abnormal returns",
        "or alpha vs holding benchmark QQQ.",
        "",
        "- **Data Source:** SEC EDGAR Form 8-K filings (2005 to present) retrieved via company submissions JSON and primary documents.",
        f"- **Universe:** S&P 100 constituents ({symbols_scanned} symbols scanned).",
        "- **Benchmark:** QQQ (daily close-to-close).",
        f"- **Events Detected:** {total_events_found} raw matching filings, {deduped_events_count} retained after 30-day per-company deduplication.",
        "- **Look-Ahead Guard:** Entry is strictly the first daily close following filing acceptance time (or next session close if only date is known).",
        "- **Execution Costs:** Net of 2 bp per side (standard) and also reported at 5 bp per side (framework default).",
        "- **Evaluation Bar (anti-p-hacking):** Strategy alpha vs QQQ with `t >= 2.0`, positive alpha in **both** halves (`H1 > 0`, `H2 > 0`), and `beta < 0.5`.",
        "- **Verdict Standard:** `edge` only if passing AND surviving placebo (random same-count draws, p < 0.05); else `unproven` or `no edge`.",
        "",
        f"## Pre-registered Variants Table ({len(results)} variants)",
        "",
        "| Hold | Universe / Sizing | Reaction Filter | N Events | AR Mean | AR t | Win% | Strat Alpha (2bp) | Strat t (2bp) | Beta | H1 Alpha (t) | H2 Alpha (t) | Strat Alpha (5bp) | Strat t (5bp) | Pass? |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        hold_str = f"{r.hold_days}d"
        size_str = "All (EW)" if r.size_bucket == "all" else "Above Median"
        filter_str = "AR > 0" if r.ar_filter else "None"

        ar_mean_str = f"{r.ar_mean_pct:+.2f}%"
        ar_t_str = f"{r.ar_t:.2f}"
        win_str = f"{r.ar_win_pct:.1f}%"

        alpha_2bp_str = f"{r.strat_alpha_2bp_pct:+.2f}%" if r.strat_alpha_2bp_pct is not None else "N/A"
        t_2bp_str = f"{r.strat_t_2bp:.2f}" if r.strat_t_2bp is not None else "N/A"
        beta_str = f"{r.strat_beta:.2f}" if r.strat_beta is not None else "N/A"

        h1_str = f"{r.h1_alpha_2bp_pct:+.2f}% ({r.h1_t_2bp:.2f})" if r.h1_alpha_2bp_pct is not None and r.h1_t_2bp is not None else "N/A"
        h2_str = f"{r.h2_alpha_2bp_pct:+.2f}% ({r.h2_t_2bp:.2f})" if r.h2_alpha_2bp_pct is not None and r.h2_t_2bp is not None else "N/A"

        alpha_5bp_str = f"{r.strat_alpha_5bp_pct:+.2f}%" if r.strat_alpha_5bp_pct is not None else "N/A"
        t_5bp_str = f"{r.strat_t_5bp:.2f}" if r.strat_t_5bp is not None else "N/A"
        pass_str = "**PASS**" if r.passes else "FAIL"

        lines.append(
            f"| {hold_str} | {size_str} | {filter_str} | {r.n_events} | {ar_mean_str} | {ar_t_str} | {win_str} | {alpha_2bp_str} | {t_2bp_str} | {beta_str} | {h1_str} | {h2_str} | {alpha_5bp_str} | {t_5bp_str} | {pass_str} |"
        )

    lines.extend([
        "",
        "## Key Findings",
        "",
    ])

    scored =[r for r in results if r.strat_t_2bp is not None]
    best = max(scored, key=lambda r: r.strat_t_2bp) if scored else None
    n_events = max((r.n_events for r in results), default=0)
    lines.append(f"1. **Sample:** up to {n_events} events per variant.")
    if best is not None:
        lines.append(
            f"2. **Best variant by t (2bp):** hold {best.hold_days}d, size={best.size_bucket}, "
            f"AR filter={best.ar_filter}: alpha {best.strat_alpha_2bp_pct:+.2f}%/yr, t {best.strat_t_2bp:.2f}."
        )
    if passing:
        lines.append(f"3. **Passing Variants:** {len(passing)} variant(s) passed the initial statistical criteria.")
        if placebo_p is not None:
            lines.append(f"   - Placebo test p-value: `{placebo_p:.4f}` over {os.environ.get('PLACEBO_DRAWS', '2000')} random same-count draws (requires p < 0.05).")
    else:
        lines.append("3. **Overall Assessment:** None of the 12 pre-registered variants clear the statistical threshold (`t >= 2.0`, `alpha > 0` in both halves, `beta < 0.5`).")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------
def run_buyback_study(
    cap_tickers: int | None = None,
    use_cache: bool = True,
    output_doc_path: Path | None = None,
) -> tuple[list[VariantResult], str]:
    """Execute complete buyback study workflow end-to-end."""
    print("=" * 60)
    print("Form 8-K Buyback Announcement Study (Step B1)")
    print("=" * 60)

    symbols = list(SP100)
    if cap_tickers is not None and cap_tickers > 0:
        symbols = symbols[:cap_tickers]
        print(f"Smoke run active: capped universe to {len(symbols)} tickers.")
    else:
        print(f"Scanning full universe of {len(symbols)} S&P 100 tickers.")

    # 1. Load SEC EDGAR CIK mapping
    print("Fetching SEC EDGAR ticker-to-CIK mapping...")
    ticker_to_cik = load_sec_ticker_to_cik(use_cache=use_cache)

    # 2. Collect 8-K filings for tickers
    print("Scanning EDGAR 8-K submissions (Item 8.01 or 7.01) from 2005...")
    all_candidate_filings: list[dict] = []
    for s in symbols:
        cik = ticker_to_cik.get(s)
        if not cik:
            continue
        c_filings = load_company_8k_filings(s, cik, start_date=DEFAULT_START_DATE, use_cache=use_cache)
        all_candidate_filings.extend(c_filings)

    print(f"Found {len(all_candidate_filings)} candidate 8-K filings with Item 8.01/7.01.")

    # 3. Filter by primary doc buyback regex
    print("Verifying buyback authorization regex in primary documents...")
    buyback_filings = filter_filings_by_buyback_primary_doc(all_candidate_filings, use_cache=use_cache)
    print(f"Matched {len(buyback_filings)} filings matching buyback authorization regex.")

    raw_events_df = pd.DataFrame(buyback_filings)
    if not raw_events_df.empty:
        raw_events_df["acceptance_dt"] = pd.to_datetime(raw_events_df["acceptance_dt"], utc=True)

    # 4. Deduplicate per company within 30 days
    deduped_events_df = dedupe_events_by_symbol(raw_events_df, window_days=30)
    print(f"Retained {len(deduped_events_df)} buyback events after 30-day deduplication.")

    # 5. Load prices and market caps
    print("Loading daily close price histories (yfinance)...")
    prices = load_price_history(symbols, start_date="2004-01-01", use_cache=use_cache)
    qqq_prices = prices.get("QQQ")
    if qqq_prices is None or qqq_prices.empty:
        raise RuntimeError("Failed to download QQQ benchmark prices")

    print("Loading market caps for size bucketing...")
    caps = load_market_caps(symbols, use_cache=use_cache)
    cap_buckets, med_val = classify_market_cap_bucket(symbols, caps)
    print(f"Universe median market cap: ${med_val:,.0f}")

    print("Precomputing 250-day rolling beta series...")
    betas = precompute_betas(prices, qqq_prices)

    # 6. Evaluate all 12 pre-registered variants
    print(f"\nEvaluating {len(PRE_REGISTERED_VARIANTS)} pre-registered strategy variants...")
    results: list[VariantResult] = []
    for hold_days, size_bucket, ar_filter in PRE_REGISTERED_VARIANTS:
        res = evaluate_variant(
            events_df=deduped_events_df,
            prices=prices,
            qqq_prices=qqq_prices,
            hold_days=hold_days,
            size_bucket=size_bucket,
            ar_filter=ar_filter,
            market_cap_buckets=cap_buckets,
            precomputed_betas=betas,
        )
        results.append(res)
        pass_tag = "PASS" if res.passes else "FAIL"
        a2bp = f"{res.strat_alpha_2bp_pct:+.2f}%" if res.strat_alpha_2bp_pct is not None else "N/A"
        t2bp = f"{res.strat_t_2bp:.2f}" if res.strat_t_2bp is not None else "N/A"
        print(f"  Hold {res.hold_days:2d}d | Size: {res.size_bucket:12s} | Filter: {str(res.ar_filter):5s} | N: {res.n_events:3d} | Alpha(2bp): {a2bp:8s} | t: {t2bp:6s} | {pass_tag}")

    # 7. Placebo test & Verdict
    passing = [r for r in results if r.passes]
    placebo_p = None
    if passing:
        best_pass = max(passing, key=lambda x: x.strat_t_2bp if x.strat_t_2bp is not None else -999)
        n_placebo = int(os.environ.get("PLACEBO_DRAWS", "2000"))
        print(f"\nRunning {n_placebo} Monte Carlo placebo draws for passing variant (t={best_pass.strat_t_2bp:.2f})...")
        placebo_p = run_placebo_test(
            observed_t=best_pass.strat_t_2bp,
            n_events=best_pass.n_events,
            hold_days=best_pass.hold_days,
            prices=prices,
            qqq_prices=qqq_prices,
            n_draws=n_placebo,
        )
        print(f"Placebo p-value: {placebo_p:.4f}")
        verdict = "edge" if placebo_p < 0.05 else "unproven"
    elif any(r.strat_t_2bp is not None and r.strat_t_2bp >= 1.5 for r in results):
        verdict = "unproven"
    else:
        verdict = "no edge"

    print(f"\nFinal Verdict: `{verdict}`")

    # 8. Write markdown report
    if output_doc_path is None:
        output_doc_path = Path("docs/backtests/event-buyback-2026-11.md")

    report_text = format_markdown_report(
        results=results,
        verdict=verdict,
        total_events_found=len(buyback_filings),
        deduped_events_count=len(deduped_events_df),
        symbols_scanned=len(symbols),
        placebo_p=placebo_p,
    )
    output_doc_path.parent.mkdir(parents=True, exist_ok=True)
    output_doc_path.write_text(report_text, encoding="utf-8")
    print(f"Report written to {output_doc_path}")

    return results, verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Form 8-K Buyback Announcement Event Study")
    parser.add_argument("--cap", type=int, default=None, help="Cap universe to N tickers (smoke run)")
    parser.add_argument("--no-cache", action="store_true", help="Bypass cached files")
    parser.add_argument("--output", type=str, default="docs/backtests/event-buyback-2026-11.md", help="Output doc path")
    args = parser.parse_args()

    run_buyback_study(
        cap_tickers=args.cap,
        use_cache=not args.no_cache,
        output_doc_path=Path(args.output),
    )


if __name__ == "__main__":
    main()
