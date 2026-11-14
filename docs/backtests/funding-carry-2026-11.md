# Hyperliquid funding-rate carry (2026-11-14) — RESULT PENDING

Script: `scripts/onetime_funding_carry_study.py` (BTC/ETH/SOL, hourly funding history, delta-neutral
long spot / short perp hedge, 15 bp round trip x 4 re-entries/yr, alpha vs QQQ per CLAUDE.md).

**It has not produced numbers yet.** On 2026-11-14 the public `info` endpoint returned 429, 502 and then
read timeouts, so the run never completed. The script retries with backoff; rerun it when the API is
healthy and paste the table here. Nothing about this strategy is evidenced until then, and no bot exists.
Note: the hedge is a short perp, which the paper-bot framework does not model, so this stays a study.
