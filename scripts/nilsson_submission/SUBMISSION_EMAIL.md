# NilssonHedge submission — AdaptiveMeanReversionBot

## Files to attach
- `AdaptiveMeanReversionBot_monthly_returns.xlsx` — monthly returns (Strategy vs QQQ, year×month matrix + long form)
- `AdaptiveMeanReversionBot_monthly_returns.csv` — same data, CSV
- `AdaptiveMeanReversionBot_factsheet.pdf` — one-page presentation / factsheet
- (optional) `AdaptiveMeanReversionBot_equity_curve.png`

---

## Web form — "Message for NilssonHedge" field

Systematic single-instrument trend-following strategy on QQQ (Nasdaq-100 ETF).
Long when the 200-day trend is intact and volatility is calm; moves to cash on a
confirmed trend breakdown. No leverage, no shorts, no overnight derivatives.

Live track record since 22 Mar 2026 (paper book, $10,000 notional), executed via
Interactive Brokers, returns net of modelled costs. Attached: monthly returns
(Excel + CSV) and a one-page factsheet. Please add guese.justin@gmail.com to the
monthly mailing list.

Manager: Justin Güse — guese.justin@gmail.com

---

## Fallback email (to info@nilssonhedge.com if the form fails)

Subject: New strategy listing — AdaptiveMeanReversionBot (systematic QQQ trend)

Hi NilssonHedge team,

I'd like to add my strategy to your database.

- Strategy: AdaptiveMeanReversionBot
- Style: fully systematic, rules-based trend-following on QQQ (Nasdaq-100 ETF),
  long/cash only, no leverage or shorts
- Track record: live since 22 Mar 2026, $10,000 notional book, executed via
  Interactive Brokers, returns net of modelled trading costs
- Return since inception: +19.2% (as of 1 Jul 2026)
- Please add guese.justin@gmail.com to the monthly mailing list

Attached:
- Monthly returns in Excel and CSV
- A one-page factsheet (PDF)

Happy to provide further documentation (DDQ / pitchbook) on request.

Best regards,
Justin Güse
guese.justin@gmail.com

---

## Notes / caveats (for your own reference — do NOT put in the submission)
- Track record is ~3.5 months; annualized figures (CAGR 88%, Sortino) are
  extrapolations and will normalize as history accrues — the factsheet flags this.
- The book is a $10k notional paper account, not external client capital. State it
  as such; NilssonHedge lists emerging/small managers, so a short honest record is fine.
- Over this specific bull window the strategy (+19.2%) trails QQQ buy-and-hold
  (+24.6%) because it sat in cash for its first ~2.5 weeks before the first entry
  (bought QQQ on 9 Apr 2026 and has held since). This is expected behavior.
- Source of truth: `portfolio_worth` table, bot_name='AdaptiveMeanReversionBot',
  namespace tradingbots-2025. Regenerate with `build_package.py` after refreshing
  `raw/strategy.csv` and `raw/benchmark.csv` from the DB.
