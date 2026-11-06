# Signals Bank Follow-The-Signal Study (2026-11-06)

**Verdict:** `no edge` (for tradeable long-only execution; theoretical short-only edge unexecutable without shorting support).

Study of trading signals published in Telegram channel `-1001998690333` ("The Signals Bank - FREE"), recorded in the `telegram_messages` database table from 2026-03-10 to 2026-11-05.

---

## 1. Data Audit & Mapping Summary

- **Total channel messages queried:** 1,381 rows (non-null `symbol` and `summary`).
- **Successfully mapped to Yahoo Finance tickers:** 1,274 rows (92.3%).
- **Unmapped / dropped noise:** 107 rows (7.7%) dropped (channel metadata, promotion tokens, bare currencies like `USD`, `BROKER`, `TSB`).
- **Direction parsed via simple keyword rule (BUY/LONG vs SELL/SHORT):**
  - BUY / LONG signals: 144
  - SELL / SHORT signals: 191
  - Ambiguous / commentary / performance recaps without entry: 939
- **Unique tradeable tickers mapped:** 41 distinct assets (FX pairs, Gold `GC=F`, Silver `SI=F`, WTI `CL=F`, S&P 500 `^GSPC`, Dow `^DJI`, Nasdaq `^NDX`, DAX `^GDAXI`, BTC, ETH, SOL, XRP, and US equities).
- **Benchmark:** QQQ over the identical sample window (2026-03-10 → 2026-11-05, 168 trading sessions).

---

## 2. Pre-Registered Strategy Variants (Equal-Weight Book vs QQQ)

Every variant was pre-registered in the script header before evaluation. Following framework protocol, the sample was split in half by event date (H1: March 2026 – July 2026, H2: July 2026 – November 2026) to enforce out-of-sample consistency. Passing requires $t \ge 2.0$, $\alpha > 0$ in both halves, and $\beta < 0.5$.

| Variant | Subset | Hold Days | Alpha | t-stat | Beta | Corr | n | Alpha H1 | Alpha H2 | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| `all_signed_h1` | All Signed | 1 | +0.3265 | 2.80 | -0.1034 | -0.2445 | 168 | +0.2875 | +0.4218 | **YES** |
| `all_signed_h3` | All Signed | 3 | +0.4597 | 3.37 | -0.0731 | -0.1511 | 168 | +0.2990 | +0.6537 | **YES** |
| `all_signed_h5` | All Signed | 5 | +0.4426 | 2.80 | -0.0465 | -0.0835 | 168 | +0.2619 | +0.7044 | **YES** |
| `long_only_h1` | Long Only | 1 | +0.1081 | 0.59 | 0.0778 | 0.1195 | 168 | -0.1677 | +0.3364 | **NO** |
| `long_only_h3` | Long Only | 3 | +0.1665 | 1.05 | 0.1781 | 0.3058 | 168 | -0.0971 | +0.4000 | **NO** |
| `long_only_h5` | Long Only | 5 | -0.0014 | -0.01 | 0.3587 | 0.4684 | 168 | -0.4546 | +0.4380 | **NO** |
| `short_only_h1` | Short Only | 1 | +0.1992 | 1.70 | -0.2076 | -0.4497 | 168 | +0.3057 | +0.1841 | **NO** |
| `short_only_h3` | Short Only | 3 | +0.3242 | 3.03 | -0.2195 | -0.5040 | 168 | +0.3886 | +0.2910 | **YES** |
| `short_only_h5` | Short Only | 5 | +0.3778 | 3.33 | -0.2496 | -0.5306 | 168 | +0.4359 | +0.3672 | **YES** |

---

## 3. Per-Signal Forward and Abnormal Returns Breakdown

Returns measured from entry close (first session close strictly after message timestamp, zero look-ahead) across horizons $+1$, $+3$, and $+5$ sessions. Abnormal returns are computed against benchmark QQQ using rolling 250-day pre-event beta (fallback 1.0).

### Long-Only Subset (144 signals — Framework Executable)

| Horizon | Mean Raw Return | Mean Signed Raw Return | Mean Abnormal Return | Mean Signed Abnormal Return |
|---|---|---|---|---|
| +1 session | -0.086% | -0.086% | -0.187% | -0.187% |
| +3 sessions | -0.110% | -0.110% | -0.330% | -0.330% |
| +5 sessions | -0.457% | -0.457% | -0.735% | -0.735% |

### Short-Only Subset (191 signals — Theoretical / Cannot Short)

| Horizon | Mean Raw Return | Mean Signed Raw Return | Mean Abnormal Return | Mean Signed Abnormal Return |
|---|---|---|---|---|
| +1 session | -0.074% | +0.074% | -0.025% | +0.025% |
| +3 sessions | -0.342% | +0.342% | -0.339% | +0.339% |
| +5 sessions | -0.479% | +0.479% | -0.460% | +0.460% |

### All Signed Signals (335 signals combined)

| Horizon | Mean Raw Return | Mean Signed Raw Return | Mean Abnormal Return | Mean Signed Abnormal Return |
|---|---|---|---|---|
| +1 session | -0.079% | +0.005% | -0.094% | -0.066% |
| +3 sessions | -0.242% | +0.148% | -0.335% | +0.053% |
| +5 sessions | -0.470% | +0.081% | -0.576% | -0.044% |

---

## 4. Key Takeaways & Architectural Recommendation

1. **Long-Only Fails the CLAUDE.md Bar:**
   - Under the framework's hard limitations (AGENTS.md: *"No short selling — sell() only sells existing holdings; going short is not supported"*), only the long-only strategy could ever be executed by a bot.
   - Long-only has $t = 0.59$ (1-day hold), $t = 1.05$ (3-day hold), and $t = -0.01$ (5-day hold). Crucially, it suffers negative alpha in H1 for all horizons ($-16.8\%$, $-9.7\%$, $-45.5\%$).
   - Raw and abnormal forward returns are consistently negative across all tested holding periods.

2. **Source of All Alpha is the Short Side:**
   - The theoretical `all_signed` strategy displays apparent statistical significance ($t = 3.37$, $\alpha = 45.9\%$), but decomposition proves 100% of this effect comes from short signals correctly fading assets during downturns.
   - Without margin borrowing and short selling infrastructure in Kubernetes cron executions, this cannot be captured.

3. **Conclusion:**
   - Do not build a live follow-the-signal execution bot based on this channel's buy signals. The data holds **no executable edge** for long-only portfolios.
