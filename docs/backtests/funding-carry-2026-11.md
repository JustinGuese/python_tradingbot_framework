# Hyperliquid funding-rate carry (2026-11) — RESULT PENDING

Script: `scripts/onetime_funding_carry_study.py` (BTC/ETH/SOL, hourly funding history, delta-neutral
long spot / short perp hedge, 15 bp round trip x 4 re-entries/yr, alpha vs QQQ per CLAUDE.md).

## Status: RESULT PENDING

**It has not produced numbers yet.**
- **Last attempt date**: 2026-11-14
- **Last attempt error**: Public info endpoint returned 429, 502 and read timeouts. Offline mode: live exchange API call skipped during test.
- **Pre-registered specification**:
  - **Basket**: BTC, ETH, SOL (equal-weight)
  - **Structure**: Long spot / short perp (delta-neutral, gross 1.0x per side)
  - **Base cost**: 15 bp round trip x 4 re-entries/yr (60 bp/yr drag)
  - **Sensitivity**: 5 bp and 20 bp round trip
  - **Benchmark**: QQQ
  - **Secondary baseline**: Equal-weight spot hold of BTC/ETH/SOL
  - **Acceptance bar**: Alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5

The script retries with backoff; rerun it when the API is healthy and reachable. Nothing about this
strategy is evidenced until then, and no bot exists.
Note: the hedge is a short perp, which the paper-bot framework does not model, so this stays a study.
