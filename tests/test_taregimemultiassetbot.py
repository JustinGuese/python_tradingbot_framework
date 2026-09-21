"""TARegimeMultiAssetBot must read each asset's OWN history, never another leg's."""

import pandas as pd

import tradingbot.taregimemultiassetbot as mod
from tradingbot.taregimemultiassetbot import UNIVERSE, TARegimeMultiAssetBot


def test_decision_uses_the_current_tickers_frame(monkeypatch):
    seen = {}

    def fake_decision(row, data, **params):
        seen["data"] = data
        seen["params"] = params
        return 1

    monkeypatch.setattr(mod, "ta_regime_decision", fake_decision)
    # Bypass Bot.__init__: it runs DDL and inserts a portfolio row.
    bot = TARegimeMultiAssetBot.__new__(TARegimeMultiAssetBot)
    bot._ta_params = {"hurst_window": 50}
    frames = {t: pd.DataFrame({"close": [float(i)]}) for i, t in enumerate(UNIVERSE, start=1)}
    bot.datas = frames
    bot._current_ticker = "GLD"

    assert bot.decisionFunction(frames["GLD"].iloc[0]) == 1
    assert seen["data"] is frames["GLD"]
    assert seen["params"] == {"hurst_window": 50}


def test_universe_is_us_listed_only():
    """Collective2 drops foreign listings and crypto to cash; none belong here."""
    assert all(not any(ch in t for ch in ".-^") for t in UNIVERSE)
