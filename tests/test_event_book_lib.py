import numpy as np
import pandas as pd

import scripts.event_book_lib as lib


def test_entry_is_first_close_strictly_after_filing_dst_aware():
    idx = pd.DatetimeIndex(["2024-07-01", "2024-07-02", "2024-07-03", "2024-01-09", "2024-01-10"]).sort_values()
    # Summer (EDT): 16:00 NY = 20:00 UTC. A filing at 20:30 UTC on 2024-07-02 is AFTER that day's close,
    # so entry is 07-03. The old fixed 21:00 UTC close would have entered on 07-02, a close that had already happened.
    acc = pd.Series(pd.to_datetime(["2024-07-02 20:30:00", "2024-07-02 19:59:00", "2024-01-09 21:30:00"], utc=True))
    pos = lib.entry_positions(idx, acc)
    assert idx[pos[0]] == pd.Timestamp("2024-07-03")
    assert idx[pos[1]] == pd.Timestamp("2024-07-02")
    # Winter (EST): 16:00 NY = 21:00 UTC; 21:30 UTC is after the close.
    assert idx[pos[2]] == pd.Timestamp("2024-01-10")


def test_book_holds_exactly_hold_sessions_and_charges_cost_on_entry_and_exit():
    n = 8
    R = np.zeros((n, 1))
    R[2:6, 0] = 0.01  # sessions 2..5 earn 1%
    # entry at close of session 1: held through sessions 2,3,4 (hold=3)
    out = lib.book_returns(np.array([1]), np.array([0]), 3, R, cost=0.001)
    assert out[1] == 0 and out[5] == 0  # entry day and the session after the hold earn nothing
    np.testing.assert_allclose(out[2], 0.01 - 0.001)  # entry cost on first held session
    np.testing.assert_allclose(out[3], 0.01)
    np.testing.assert_allclose(out[4], 0.01 - 0.001)  # exit cost on last held session


def test_dedupe_keeps_first_event_per_symbol_within_window():
    ev = pd.DataFrame(
        {
            "symbol": ["A", "A", "A", "B"],
            "acceptance_dt": pd.to_datetime(["2020-01-01", "2020-01-10", "2020-03-01", "2020-01-05"], utc=True),
        }
    )
    kept = lib.dedupe(ev, 30)
    assert list(kept["symbol"]) == ["A", "B", "A"]
    assert kept["acceptance_dt"].dt.strftime("%Y-%m-%d").tolist() == ["2020-01-01", "2020-01-05", "2020-03-01"]
