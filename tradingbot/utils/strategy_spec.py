"""
The declarative strategy specification behind the no-code strategy builder.

A spec is plain JSON describing entry and exit conditions over indicator columns.
`RuleBot` turns one into a `decisionFunction`, so a strategy a user assembles in
the app runs through exactly the same backtest and live machinery as a
hand-written bot.

This module is deliberately free of database and Bot imports: the API validates
untrusted input with it on every request, and the cronjob re-validates before
execution. Evaluation lives here too, so there is one definition of what a spec
*means* rather than one in the backtester and another in the app.

Shape::

    {
      "version": 1,
      "tickers": ["SPY"],
      "interval": "1d",
      "period": "2y",
      "entry": {"match": "all", "conditions": [
          {"left": {"indicator": "momentum_rsi"}, "op": "<", "right": {"const": 30}}
      ]},
      "exit":  {"match": "any", "conditions": [
          {"left": {"indicator": "momentum_rsi"}, "op": ">", "right": {"const": 70}}
      ]}
    }

Entry true -> +1 (buy), exit true -> -1 (sell), neither -> 0 (hold). Both true is
treated as 0: a bar that is simultaneously a buy and a sell signal carries no
information, and silently preferring one would hide a contradictory strategy from
the user.

Conditions are a flat list under a single `all`/`any`. That is a deliberate ceiling,
not an oversight — it keeps the editor a list of dropdown rows and covers the great
majority of retail strategies. Nested boolean trees would need a different UI.
"""

from dataclasses import dataclass
from typing import Any

from .indicator_catalog import KNOWN_INDICATORS, PRICE_FIELDS
from .indicators import safe_get

SPEC_VERSION = 1

ALLOWED_INTERVALS = ("1d", "1h")
# Yahoo caps intraday history, and the backtester skips TA warmup bars, so a
# short period on a long-warmup indicator yields too few bars for metrics.
ALLOWED_PERIODS = ("3mo", "6mo", "1y", "2y", "5y")

# Not every period is available at every interval: Yahoo serves at most 730 days
# of hourly bars, and asking for more returns a short frame rather than an error,
# so a "5y hourly" backtest would quietly measure something else entirely.
PERIODS_BY_INTERVAL: dict[str, tuple[str, ...]] = {
    "1d": ("3mo", "6mo", "1y", "2y", "5y"),
    "1h": ("3mo", "6mo", "1y"),
}
MAX_TICKERS = 5
MAX_CONDITIONS = 8
MAX_TICKER_LENGTH = 20

COMPARISON_OPS = ("<", "<=", ">", ">=")
CROSS_OPS = ("crosses_above", "crosses_below")
OPERATORS = COMPARISON_OPS + CROSS_OPS

MATCH_MODES = ("all", "any")

# Prefix for the shifted helper columns RuleBot adds so a crossover can see the
# previous bar. Double underscore keeps them clear of every real `ta` column.
PREV_PREFIX = "__prev_"

# Symbols are passed to yfinance, which uses suffixes and prefixes freely:
# "BRK-B", "EURUSD=X", "^GSPC", "BTC-USD", "BZ=F".
_TICKER_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-=^")


class SpecError(ValueError):
    """A strategy spec is malformed. The message is shown to the user."""


@dataclass(frozen=True)
class Operand:
    """One side of a condition: an indicator column, a price column, or a number."""

    kind: str  # "indicator" | "price" | "const"
    value: Any  # column name (str) or number (float)

    @property
    def column(self) -> str | None:
        """The dataframe column this operand reads, or None for a constant."""
        return None if self.kind == "const" else str(self.value)

    def to_dict(self) -> dict:
        return {self.kind: self.value}


@dataclass(frozen=True)
class Condition:
    left: Operand
    op: str
    right: Operand

    def to_dict(self) -> dict:
        return {"left": self.left.to_dict(), "op": self.op, "right": self.right.to_dict()}


@dataclass(frozen=True)
class ConditionGroup:
    match: str  # "all" | "any"
    conditions: tuple[Condition, ...]

    def to_dict(self) -> dict:
        return {"match": self.match, "conditions": [c.to_dict() for c in self.conditions]}


@dataclass(frozen=True)
class StrategySpec:
    version: int
    tickers: tuple[str, ...]
    interval: str
    period: str
    entry: ConditionGroup
    exit: ConditionGroup

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "tickers": list(self.tickers),
            "interval": self.interval,
            "period": self.period,
            "entry": self.entry.to_dict(),
            "exit": self.exit.to_dict(),
        }


# --------------------------------------------------------------------------- #
#  Validation                                                                  #
# --------------------------------------------------------------------------- #


def _require_mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise SpecError(f"{path}: expected an object, got {type(value).__name__}")
    return value


def _parse_operand(raw: Any, path: str, known_columns: frozenset[str]) -> Operand:
    operand = _require_mapping(raw, path)
    if len(operand) != 1:
        raise SpecError(f"{path}: expected exactly one of 'indicator', 'price' or 'const', got {sorted(operand)}")

    kind, value = next(iter(operand.items()))

    if kind == "const":
        # bool is an int subclass; a JSON `true` here is a mistake, not a 1.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SpecError(f"{path}.const: expected a number, got {value!r}")
        return Operand("const", float(value))

    if kind == "price":
        if value not in PRICE_FIELDS:
            raise SpecError(f"{path}.price: expected one of {list(PRICE_FIELDS)}, got {value!r}")
        return Operand("price", str(value))

    if kind == "indicator":
        if not isinstance(value, str) or value not in known_columns:
            raise SpecError(f"{path}.indicator: unknown indicator {value!r}")
        return Operand("indicator", value)

    raise SpecError(f"{path}: unknown operand type {kind!r}; expected 'indicator', 'price' or 'const'")


def _parse_condition(raw: Any, path: str, known_columns: frozenset[str]) -> Condition:
    condition = _require_mapping(raw, path)

    op = condition.get("op")
    if op not in OPERATORS:
        raise SpecError(f"{path}.op: expected one of {list(OPERATORS)}, got {op!r}")

    left = _parse_operand(condition.get("left"), f"{path}.left", known_columns)
    right = _parse_operand(condition.get("right"), f"{path}.right", known_columns)

    if left.kind == "const" and right.kind == "const":
        raise SpecError(f"{path}: comparing two constants is always true or always false")

    if op in CROSS_OPS and left.kind == "const":
        raise SpecError(f"{path}: a constant cannot cross anything; put the indicator on the left")

    return Condition(left=left, op=op, right=right)


def _parse_group(raw: Any, path: str, known_columns: frozenset[str]) -> ConditionGroup:
    group = _require_mapping(raw, path)

    match = group.get("match", "all")
    if match not in MATCH_MODES:
        raise SpecError(f"{path}.match: expected 'all' or 'any', got {match!r}")

    raw_conditions = group.get("conditions")
    if not isinstance(raw_conditions, list):
        raise SpecError(f"{path}.conditions: expected a list")
    # An empty group is rejected rather than defaulted: `all([])` is True, so an
    # empty exit would sell on every bar while reading as "no exit rule".
    if not raw_conditions:
        raise SpecError(f"{path}.conditions: needs at least one condition")
    if len(raw_conditions) > MAX_CONDITIONS:
        raise SpecError(f"{path}.conditions: at most {MAX_CONDITIONS} conditions, got {len(raw_conditions)}")

    conditions = tuple(
        _parse_condition(c, f"{path}.conditions[{i}]", known_columns) for i, c in enumerate(raw_conditions)
    )
    return ConditionGroup(match=match, conditions=conditions)


def _parse_tickers(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise SpecError("tickers: expected a non-empty list of symbols")
    if len(raw) > MAX_TICKERS:
        raise SpecError(f"tickers: at most {MAX_TICKERS} symbols, got {len(raw)}")

    tickers: list[str] = []
    for ticker in raw:
        if not isinstance(ticker, str) or not ticker.strip():
            raise SpecError(f"tickers: expected a symbol string, got {ticker!r}")
        symbol = ticker.strip().upper()
        if len(symbol) > MAX_TICKER_LENGTH:
            raise SpecError(f"tickers: symbol {symbol!r} is too long")
        invalid = set(symbol) - _TICKER_CHARS
        if invalid:
            raise SpecError(f"tickers: symbol {symbol!r} contains invalid characters {sorted(invalid)}")
        # Dedupe: a repeated ticker would both inflate the equal-weight divisor
        # and be traded twice, matching Bot.__init__'s own guard.
        if symbol not in tickers:
            tickers.append(symbol)
    return tuple(tickers)


def validate_spec(raw: Any, known_columns: frozenset[str] | None = None) -> StrategySpec:
    """
    Parse and validate an untrusted spec dict.

    Args:
        raw: The spec as decoded JSON.
        known_columns: Indicator names to accept. Defaults to the full `ta` catalogue;
                       the API can pass a smaller curated set to match its dropdown.

    Raises:
        SpecError: with a message naming the offending path, safe to show a user.
    """
    columns = known_columns if known_columns is not None else KNOWN_INDICATORS

    spec = _require_mapping(raw, "spec")

    version = spec.get("version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise SpecError(f"version: unsupported spec version {version!r}; this server speaks {SPEC_VERSION}")

    interval = spec.get("interval", "1d")
    if interval not in ALLOWED_INTERVALS:
        raise SpecError(f"interval: expected one of {list(ALLOWED_INTERVALS)}, got {interval!r}")

    period = spec.get("period", "2y")
    if period not in ALLOWED_PERIODS:
        raise SpecError(f"period: expected one of {list(ALLOWED_PERIODS)}, got {period!r}")

    available = PERIODS_BY_INTERVAL.get(interval, ALLOWED_PERIODS)
    if period not in available:
        raise SpecError(
            f"period: {period!r} is not available at the {interval!r} interval; "
            f"choose one of {list(available)}"
        )

    return StrategySpec(
        version=SPEC_VERSION,
        tickers=_parse_tickers(spec.get("tickers")),
        interval=interval,
        period=period,
        entry=_parse_group(spec.get("entry"), "entry", columns),
        exit=_parse_group(spec.get("exit"), "exit", columns),
    )


# --------------------------------------------------------------------------- #
#  Column introspection                                                        #
# --------------------------------------------------------------------------- #


def _groups(spec: StrategySpec) -> tuple[ConditionGroup, ConditionGroup]:
    return (spec.entry, spec.exit)


def referenced_columns(spec: StrategySpec) -> frozenset[str]:
    """Every dataframe column the spec reads. Useful for trimming fetched features."""
    columns = {
        operand.column
        for group in _groups(spec)
        for condition in group.conditions
        for operand in (condition.left, condition.right)
        if operand.column is not None
    }
    return frozenset(columns)


def crossover_columns(spec: StrategySpec) -> frozenset[str]:
    """
    Columns that need a previous-bar copy.

    `decisionFunction` only ever sees one row, so a crossover cannot look back on
    its own. RuleBot precomputes `__prev_<column>` for exactly these.
    """
    columns = {
        operand.column
        for group in _groups(spec)
        for condition in group.conditions
        if condition.op in CROSS_OPS
        for operand in (condition.left, condition.right)
        if operand.column is not None
    }
    return frozenset(columns)


# --------------------------------------------------------------------------- #
#  Evaluation                                                                  #
# --------------------------------------------------------------------------- #


def _operand_value(operand: Operand, row: Any) -> float:
    if operand.kind == "const":
        return float(operand.value)
    return safe_get(row, str(operand.value), default=float("nan"), check_finite=True)


def _previous_value(operand: Operand, row: Any) -> float:
    """The operand's value on the prior bar. Constants do not move."""
    if operand.kind == "const":
        return float(operand.value)
    return safe_get(row, PREV_PREFIX + str(operand.value), default=float("nan"), check_finite=True)


def _is_nan(value: float) -> bool:
    return value != value


def evaluate_condition(condition: Condition, row: Any) -> bool:
    """
    Evaluate one condition against a bar.

    Missing or non-finite inputs make the condition False rather than raising:
    indicator warmup leaves NaNs, and a strategy must not fire on a bar where its
    own inputs are undefined.
    """
    left = _operand_value(condition.left, row)
    right = _operand_value(condition.right, row)
    if _is_nan(left) or _is_nan(right):
        return False

    op = condition.op
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right

    prev_left = _previous_value(condition.left, row)
    prev_right = _previous_value(condition.right, row)
    if _is_nan(prev_left) or _is_nan(prev_right):
        # First evaluated bar has no predecessor: a cross is undefined, not false-y.
        return False

    if op == "crosses_above":
        return prev_left <= prev_right and left > right
    if op == "crosses_below":
        return prev_left >= prev_right and left < right

    raise SpecError(f"unknown operator {op!r}")


def evaluate_group(group: ConditionGroup, row: Any) -> bool:
    results = (evaluate_condition(c, row) for c in group.conditions)
    return all(results) if group.match == "all" else any(results)


def decide(spec: StrategySpec, row: Any) -> int:
    """Return the bot decision for one bar: 1 buy, -1 sell, 0 hold."""
    enter = evaluate_group(spec.entry, row)
    leave = evaluate_group(spec.exit, row)
    if enter and not leave:
        return 1
    if leave and not enter:
        return -1
    return 0
