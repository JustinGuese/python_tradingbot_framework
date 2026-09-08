"""
The indicator vocabulary the strategy builder offers.

`DataService.get_yf_data_with_ta` runs `ta.add_all_ta_features`, which produces 86
columns on top of OHLCV. `TA_COLUMNS` is that full set — the validator accepts any
of them, so a spec written by hand or by an older client keeps working.

`CURATED` is the much shorter list the app puts in its dropdown. Offering all 86 to
someone assembling their first strategy is a worse product than offering the thirty
they have heard of.

The window sizes in the labels are `ta`'s defaults, and two of them surprise people:
**ATR is 10, not 14**, and SMA/EMA "fast/slow" are 12/26 rather than the 50/200 a
chartist would expect. Labelling them without the number would quietly mislead, so
every label carries its period.

`tests/test_indicator_catalog.py` asserts TA_COLUMNS still matches what the pinned
`ta` version emits, so a dependency bump cannot silently invalidate saved specs.
"""

from dataclasses import dataclass

# Groups, in the order `ta` emits them.
GROUPS = ("momentum", "trend", "volatility", "volume", "others")

# Units tell the UI what input control to show for the right-hand side, and what a
# sensible default constant is.
#   oscillator_0_100 : bounded 0..100      -> slider, default 50
#   oscillator_pm100 : bounded -100..0     -> slider
#   price            : same scale as close -> compare against another price/indicator
#   ratio            : small number around 0
#   unbounded        : anything            -> free numeric entry
UNITS = ("oscillator_0_100", "oscillator_pm100", "price", "ratio", "unbounded")


@dataclass(frozen=True)
class IndicatorInfo:
    key: str
    group: str
    label: str
    unit: str
    hint: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "unit": self.unit,
            "hint": self.hint,
        }


# --------------------------------------------------------------------------- #
#  Price columns — always available alongside the indicators                   #
# --------------------------------------------------------------------------- #

PRICE_FIELDS = ("open", "high", "low", "close", "volume")

PRICE_INFO = (
    IndicatorInfo("close", "price", "Close", "price", "The bar's closing price."),
    IndicatorInfo("open", "price", "Open", "price", "The bar's opening price."),
    IndicatorInfo("high", "price", "High", "price", "Highest price in the bar."),
    IndicatorInfo("low", "price", "Low", "price", "Lowest price in the bar."),
    IndicatorInfo("volume", "price", "Volume", "unbounded", "Shares or contracts traded in the bar."),
)


# --------------------------------------------------------------------------- #
#  Curated set for the builder dropdown                                        #
# --------------------------------------------------------------------------- #

CURATED_INFO = (
    # Momentum
    IndicatorInfo(
        "momentum_rsi",
        "momentum",
        "RSI (14)",
        "oscillator_0_100",
        "Relative Strength Index. Below 30 is commonly read as oversold, above 70 as overbought.",
    ),
    IndicatorInfo(
        "momentum_stoch",
        "momentum",
        "Stochastic %K (14)",
        "oscillator_0_100",
        "Where the close sits within the recent high-low range.",
    ),
    IndicatorInfo(
        "momentum_stoch_signal",
        "momentum",
        "Stochastic %D (3)",
        "oscillator_0_100",
        "3-period average of %K. Cross %K above %D for a classic entry.",
    ),
    IndicatorInfo(
        "momentum_wr",
        "momentum",
        "Williams %R (14)",
        "oscillator_pm100",
        "Like the Stochastic but scaled -100 to 0. Below -80 is oversold.",
    ),
    IndicatorInfo(
        "momentum_roc",
        "momentum",
        "Rate of Change (12)",
        "ratio",
        "Percent change versus 12 bars ago. Positive means rising.",
    ),
    IndicatorInfo(
        "momentum_tsi",
        "momentum",
        "True Strength Index",
        "unbounded",
        "Double-smoothed momentum. Zero-line crosses mark trend changes.",
    ),
    IndicatorInfo(
        "momentum_ao",
        "momentum",
        "Awesome Oscillator",
        "unbounded",
        "Difference between a 5- and 34-bar midpoint average.",
    ),
    IndicatorInfo(
        "momentum_kama",
        "momentum",
        "KAMA",
        "price",
        "Adaptive moving average that speeds up in trends and slows in chop.",
    ),
    # Trend
    IndicatorInfo(
        "trend_macd",
        "trend",
        "MACD",
        "unbounded",
        "12-period EMA minus 26-period EMA.",
    ),
    IndicatorInfo(
        "trend_macd_signal",
        "trend",
        "MACD Signal (9)",
        "unbounded",
        "9-period EMA of the MACD. MACD crossing above it is the standard buy signal.",
    ),
    IndicatorInfo(
        "trend_macd_diff",
        "trend",
        "MACD Histogram",
        "unbounded",
        "MACD minus its signal line. Above zero means bullish momentum.",
    ),
    IndicatorInfo(
        "trend_sma_fast",
        "trend",
        "SMA fast (12)",
        "price",
        "12-bar simple moving average of the close.",
    ),
    IndicatorInfo(
        "trend_sma_slow",
        "trend",
        "SMA slow (26)",
        "price",
        "26-bar simple moving average. Fast above slow is a golden cross.",
    ),
    IndicatorInfo(
        "trend_ema_fast",
        "trend",
        "EMA fast (12)",
        "price",
        "12-bar exponential moving average, more responsive than the SMA.",
    ),
    IndicatorInfo(
        "trend_ema_slow",
        "trend",
        "EMA slow (26)",
        "price",
        "26-bar exponential moving average.",
    ),
    IndicatorInfo(
        "trend_adx",
        "trend",
        "ADX (14)",
        "oscillator_0_100",
        "Trend strength regardless of direction. Above 25 means a real trend.",
    ),
    IndicatorInfo(
        "trend_adx_pos",
        "trend",
        "+DI (14)",
        "oscillator_0_100",
        "Positive directional movement. Above -DI means buyers are in control.",
    ),
    IndicatorInfo(
        "trend_adx_neg",
        "trend",
        "-DI (14)",
        "oscillator_0_100",
        "Negative directional movement.",
    ),
    IndicatorInfo(
        "trend_cci",
        "trend",
        "CCI (20)",
        "unbounded",
        "Commodity Channel Index. Beyond +/-100 signals a strong move.",
    ),
    IndicatorInfo(
        "trend_aroon_ind",
        "trend",
        "Aroon Oscillator (25)",
        "oscillator_pm100",
        "Aroon Up minus Aroon Down. Positive favours an uptrend.",
    ),
    # Volatility
    IndicatorInfo(
        "volatility_atr",
        "volatility",
        "ATR (10)",
        "price",
        "Average True Range over 10 bars — note this is 10, not the more common 14.",
    ),
    IndicatorInfo(
        "volatility_bbh",
        "volatility",
        "Bollinger upper (20, 2σ)",
        "price",
        "Two standard deviations above the 20-bar average.",
    ),
    IndicatorInfo(
        "volatility_bbm",
        "volatility",
        "Bollinger middle (20)",
        "price",
        "The 20-bar simple moving average at the centre of the bands.",
    ),
    IndicatorInfo(
        "volatility_bbl",
        "volatility",
        "Bollinger lower (20, 2σ)",
        "price",
        "Two standard deviations below the 20-bar average.",
    ),
    IndicatorInfo(
        "volatility_bbp",
        "volatility",
        "Bollinger %B",
        "ratio",
        "Position within the bands: 0 at the lower band, 1 at the upper.",
    ),
    IndicatorInfo(
        "volatility_bbw",
        "volatility",
        "Bollinger width",
        "ratio",
        "Band width relative to the middle band. Low values mark a squeeze.",
    ),
    # Volume
    IndicatorInfo(
        "volume_obv",
        "volume",
        "On-Balance Volume",
        "unbounded",
        "Running total of volume, signed by the day's direction.",
    ),
    IndicatorInfo(
        "volume_mfi",
        "volume",
        "Money Flow Index (14)",
        "oscillator_0_100",
        "A volume-weighted RSI. Below 20 is oversold, above 80 overbought.",
    ),
    IndicatorInfo(
        "volume_cmf",
        "volume",
        "Chaikin Money Flow (20)",
        "ratio",
        "Buying versus selling pressure, roughly -1 to +1.",
    ),
    IndicatorInfo(
        "volume_vwap",
        "volume",
        "VWAP (14)",
        "price",
        "Volume-weighted average price.",
    ),
    # Others
    IndicatorInfo(
        "others_dr",
        "others",
        "Bar return %",
        "ratio",
        "Percent change from the previous bar's close.",
    ),
)

CURATED = frozenset(info.key for info in CURATED_INFO)


# --------------------------------------------------------------------------- #
#  Full catalogue                                                              #
# --------------------------------------------------------------------------- #

# Every column ta.add_all_ta_features() adds on top of OHLCV, in emission order.
# Regenerate with tests/test_indicator_catalog.py if the `ta` pin ever moves.
TA_COLUMN_ORDER: tuple[str, ...] = (
    # volume (10)
    "volume_adi",
    "volume_obv",
    "volume_cmf",
    "volume_fi",
    "volume_em",
    "volume_sma_em",
    "volume_vpt",
    "volume_vwap",
    "volume_mfi",
    "volume_nvi",
    # volatility (21)
    "volatility_bbm",
    "volatility_bbh",
    "volatility_bbl",
    "volatility_bbw",
    "volatility_bbp",
    "volatility_bbhi",
    "volatility_bbli",
    "volatility_kcc",
    "volatility_kch",
    "volatility_kcl",
    "volatility_kcw",
    "volatility_kcp",
    "volatility_kchi",
    "volatility_kcli",
    "volatility_dcl",
    "volatility_dch",
    "volatility_dcm",
    "volatility_dcw",
    "volatility_dcp",
    "volatility_atr",
    "volatility_ui",
    # trend (34)
    "trend_macd",
    "trend_macd_signal",
    "trend_macd_diff",
    "trend_sma_fast",
    "trend_sma_slow",
    "trend_ema_fast",
    "trend_ema_slow",
    "trend_vortex_ind_pos",
    "trend_vortex_ind_neg",
    "trend_vortex_ind_diff",
    "trend_trix",
    "trend_mass_index",
    "trend_dpo",
    "trend_kst",
    "trend_kst_sig",
    "trend_kst_diff",
    "trend_ichimoku_conv",
    "trend_ichimoku_base",
    "trend_ichimoku_a",
    "trend_ichimoku_b",
    "trend_stc",
    "trend_adx",
    "trend_adx_pos",
    "trend_adx_neg",
    "trend_cci",
    "trend_visual_ichimoku_a",
    "trend_visual_ichimoku_b",
    "trend_aroon_up",
    "trend_aroon_down",
    "trend_aroon_ind",
    "trend_psar_up",
    "trend_psar_down",
    "trend_psar_up_indicator",
    "trend_psar_down_indicator",
    # momentum (18)
    "momentum_rsi",
    "momentum_stoch_rsi",
    "momentum_stoch_rsi_k",
    "momentum_stoch_rsi_d",
    "momentum_tsi",
    "momentum_uo",
    "momentum_stoch",
    "momentum_stoch_signal",
    "momentum_wr",
    "momentum_ao",
    "momentum_roc",
    "momentum_ppo",
    "momentum_ppo_signal",
    "momentum_ppo_hist",
    "momentum_pvo",
    "momentum_pvo_signal",
    "momentum_pvo_hist",
    "momentum_kama",
    # others (3)
    "others_dr",
    "others_dlr",
    "others_cr",
)

TA_COLUMNS: frozenset[str] = frozenset(TA_COLUMN_ORDER)

# What the spec validator accepts as an `indicator` operand: any TA column.
# Price columns go through the separate `price` operand, so they are not here.
KNOWN_INDICATORS: frozenset[str] = TA_COLUMNS


def curated_payload() -> list[dict]:
    """The `/indicators` response: price fields first, then the curated indicators."""
    return [info.to_dict() for info in PRICE_INFO + CURATED_INFO]
