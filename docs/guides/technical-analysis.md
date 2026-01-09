# Technical Analysis Indicators

After calling `getYFDataWithTA()`, the DataFrame includes ~150+ technical analysis indicators.

## Indicator Categories

### Volume Indicators

- `volume_adi` - Accumulation/Distribution Index
- `volume_obv` - On-Balance Volume
- `volume_cmf` - Chaikin Money Flow
- `volume_fi` - Force Index
- `volume_em` - Ease of Movement
- `volume_vpt` - Volume Price Trend
- `volume_vwap` - Volume Weighted Average Price
- `volume_mfi` - Money Flow Index
- `volume_nvi` - Negative Volume Index

### Volatility Indicators

- `volatility_bbh` - Bollinger Bands High
- `volatility_bbl` - Bollinger Bands Low
- `volatility_bbw` - Bollinger Bands Width
- `volatility_atr` - Average True Range
- `volatility_kch` - Keltner Channel High
- `volatility_kcl` - Keltner Channel Low
- `volatility_dch` - Donchian Channel High
- `volatility_dcl` - Donchian Channel Low

### Trend Indicators

- `trend_macd` - MACD
- `trend_macd_signal` - MACD Signal
- `trend_sma_fast` - Fast Simple Moving Average
- `trend_sma_slow` - Slow Simple Moving Average
- `trend_ema_fast` - Fast Exponential Moving Average
- `trend_ema_slow` - Slow Exponential Moving Average
- `trend_adx` - Average Directional Index
- `trend_ichimoku_a` - Ichimoku Cloud A
- `trend_ichimoku_b` - Ichimoku Cloud B

### Momentum Indicators

- `momentum_rsi` - Relative Strength Index
- `momentum_stoch` - Stochastic Oscillator
- `momentum_stoch_signal` - Stochastic Signal
- `momentum_roc` - Rate of Change
- `momentum_wr` - Williams %R
- `momentum_ao` - Awesome Oscillator

## Usage

Access indicators in your `decisionFunction()`:

```python
def decisionFunction(self, row):
    # RSI
    if row["momentum_rsi"] < 30:
        return 1  # Oversold, buy
    
    # MACD
    if row["trend_macd"] > row["trend_macd_signal"]:
        # Bullish crossover
        return 1
    
    # Bollinger Bands
    if row["close"] < row["volatility_bbl"]:
        # Price below lower band, oversold
        return 1
    
    return 0
```

## Indicator Naming

All indicators use lowercase with underscores:
- `trend_sma_slow`
- `momentum_rsi`
- `volatility_bbh`

## Data Handling

Indicators are automatically:
- Prefilled/backfilled to handle NaN values
- Calculated using the `ta` library
- Available in every row of the DataFrame

## Next Steps

- [Bot API Reference](../api/bot.md) - Data fetching methods
- [Example Bots](../examples/example-bots.md) - Real implementations
