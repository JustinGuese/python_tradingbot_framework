# PLAN v3: the Collective2 strategy on more venues (eToro demo, MQL5 Signals via MT5, Numerai)

Working file, deleted after merge. Saved outside the repo because `PLAN.md` currently belongs to
the option-bots gembuild run (P1/S1/F1/C1, still running on `agy/work`).

## Scope

"The Collective2 strategy" = the blend live on C2 strategy 155809898:
`{"TARegimeMultiAssetBot": 0.39, "TSMOMTrendBot": 0.24, "GoldenButterflyMomBot": 0.37}`.
The copier framework (`LiveBroker` ABC, `registry.py`, `runner.py`) is already venue-agnostic.
C2 itself is untouched. Every new copier runs that same blend.

| Venue | Mechanism | What gets built |
|---|---|---|
| **eToro** | REST (`ETORO_*` keys present) | Fix the adapter (pending orders, v2 endpoints, symbol coverage), then enable the CronJob. |
| **MQL5 Signals** | A signal is an MT5 account, not an API. | MT5 bridge + `MT5Broker` on **any MT5 account** (demo is fine to start). One manual signal registration. |
| ~~Darwinex Zero~~ | Dropped: $50/month, and it only buys the DARWIN track record. | Not built. `MT5Broker` can reach it later with no code change. |
| **Numerai Signals** | Daily ranking over 100+ of its ~5,000 stocks, uploaded with numerapi | Separate signal job. The blend only has about 30 ETFs, so it can't feed this. |

## Design decisions (made, not asked)

1. **MT5 bridge = self-hosted Wine + `mt5linux` (RPyC) Deployment in the cluster.** There is no new
   vendor and no recurring fee, and it exposes the standard `MetaTrader5` Python API. The cost is
   fragility: Wine, a display (Xvfb/VNC), and a terminal that must stay logged in. If the smoke test
   shows it can't hold a session, the fallback is MetaApi cloud behind the same `MT5Client` interface.
   The bridge image is a **pinned third-party image** (a supply-chain risk with broker credentials in
   it). Building our own is a hardening follow-up, since the GitLab template only builds the root Dockerfile.
2. **MT5 rejects market orders when the market is closed** (it doesn't queue them like eToro). The
   current copier slots (21:xx UTC) are after the US close, so an MT5 copier there would never
   fill. MT5 copiers run **next session at 15:00 UTC Mon-Fri**, which is inside US hours in both EST and
   EDT. They trade the blend's targets from the night before.
3. **One `MT5Broker`, one registry entry (`mt5_mql5`)**, env prefix `MT5_MQL5_*`. The class is
   broker-agnostic, so any MT5 server works. **Which broker is not decided.** I couldn't confirm that any
   MT5 broker lists QQQ/TLT-type ETFs (FXTM lists ETF CFDs with suffixes such as `SPY.P`). So symbol
   resolution must try suffixes (`.US`, `.P`, `.O`, none), and **the coverage tool picks the broker**: open a free
   demo, run `coverage`, keep it only if the blend's ETFs resolve. A demo account can publish only a
   **free** MQL5 signal. A paid one needs a real account. Free is right for building the record.
4. **Numerai is submitted live and unstaked.** Staking NMR is real money and stays your call. The
   signal is 12-1 momentum, cross-sectionally ranked, and labelled **unproven** in code and docs.
   Numerai's own scoring is the validation.
5. Safety that stays on whether or not a job is live: `strictMapping`, the `minOrderUsd` band,
   `startingDeadlineSeconds: 300`, cancel-then-sync, sandbox flag on `live_equity`, MT5 volume
   step/min/max clamping, and account currency handling: USD as-is, EUR converted at the live `EURUSD=X` rate
   (the demo you opened is EUR), anything else raises (no silent FX mix-up).

## What I need from you (not blocking the build)

- ~~An MT5 account~~ **Done:** MetaQuotes-Demo, hedged, 100,000 EUR, saved as `MT5_MQL5_*` in `.env.secret` (plus the
  read-only password for the MQL5 signal registration). Still unverified: that MetaQuotes-Demo lists the blend's ETFs, and that
  MQL5 accepts it as a signal source. The coverage run settles the first; the second is checked at registration.
  Original ask: Open it at a broker whose symbol list includes US ETFs (see decision 3). If you pick
  one, I'll check coverage before you rely on it. Your existing `DARWINEX_*` keys stay unused.
- ~~`NUMERAI_MODEL_NAME`~~ **Done:** `datafortress` (the only Signals model on your account, found via the API), saved in `.env.secret`.
- eToro: **demo** (`demo: true`), as you said.
- `MQL5_API_KEY` is in `.env.secret`, but I found no MQL5 order or signal API that uses it, so it is
  unused. MQL5 signals are published by registering the MT5 account once at mql5.com (manual, documented in H1).

The MT5 secrets now exist. The MT5 CronJob is switched on in the ops step once the bridge is up and `coverage --broker mt5_mql5` passes.
`.env.secret` also needs re-encrypting to `.env.secret.enc` through `ske` so the cluster secret picks up the new keys (ops step).

---

## Batch 0: contracts and independent work (4 steps, disjoint files)

### M0: MT5 client contract + fake
Files: `tradingbot/livetrade/mt5/__init__.py`, `tradingbot/livetrade/mt5/client.py`,
`tests/mt5_fake.py`, `tests/test_mt5_client.py`, `pyproject.toml`
- `MT5Client` Protocol, the subset of the `MetaTrader5` API used: `initialize/login`, `shutdown`,
  `account_info`, `positions_get`, `orders_get`, `symbol_info`, `symbol_info_tick`, `symbols_get`,
  `symbol_select`, `order_send`, `last_error`.
- `connect_rpyc(host, port) -> MT5Client` using `mt5linux`, imported lazily (like the other adapters).
  Add the `mt5linux` and `rpyc` dependencies to `pyproject.toml` (I regenerate `uv.lock`).
- `tests/mt5_fake.py`: `FakeMT5` implementing the Protocol, with configurable account currency,
  hedging vs netting, symbol specs (`volume_min/step/max`), and a **market-closed** switch where
  `order_send` returns retcode 10018. Later steps build on it.
- Tests (2): fake enforces market-closed rejection; fake nets/hedges positions as configured.

### E1: eToro pending-order cancel + v2 endpoints
Files: `tradingbot/livetrade/etoro.py`, `tests/test_livetrade_etoro.py`
- Implement `cancel_open_orders() -> int`: list pending open and close orders, cancel each through the
  current endpoints (v2, `demo/` prefix when `self.demo`; verify paths at api-portal.etoro.com
  `llms.txt` first), return the count. Fall back to v1 only where v2 has no equivalent.
- Cancelling an already filled or cancelled order is not an error. If **listing** pending orders fails,
  raise: the copier must abort rather than submit on top of unknown working orders.
- Don't change sizing or lot-closing behaviour (existing tests stay green).
- Tests (3): cancels all pending and returns the count; tolerates an already-cancelled order; listing
  failure propagates.

### E2: venue-generic symbol coverage tool
Files: `tradingbot/livetrade/coverage.py` (new), `tests/test_livetrade_coverage.py` (new)
- `blend_universe()`: union of every ticker the three blend bots can hold. Read their universe
  constants (`tradingbot/utils/universes.py` and the bot modules) instead of hardcoding a list.
- `check_coverage(broker: LiveBroker, tickers)` returns `{resolved, missing, untradeable}` using
  `broker.map_symbol` and `broker.is_tradeable`. It never writes anything.
- `python -m tradingbot.livetrade.coverage --broker <registry key>` prints the table and exits 1 on
  any gap. It builds the broker through the registry. Credentials come from env and are never printed.
- Tests (2): universe contains known members of all three bots; gaps are reported.

### N0: Numerai signal builder (pure function)
Files: `tradingbot/utils/numerai_signal.py` (new), `tests/test_numerai_signal.py` (new)
- `build_signal(prices: DataFrame, universe: list[str], asof) -> Series`: 12-1 momentum (skip the last
  month), cross-sectional rank scaled strictly inside (0, 1), the **median** for tickers with
  missing history (never NaN). It uses only data at or before `asof` (no lookahead).
- Raises if fewer than 100 tickers have valid values (Numerai's live minimum).
- Tests (3): values strictly in (0,1) with no NaN; adding a future price row doesn't change the
  signal (no lookahead); under 100 valid tickers raises.

---

## Batch 1: needs Batch 0 (4 steps, disjoint files)

### M1: `MT5Broker` (the adapter)
Files: `tradingbot/livetrade/mt5/broker.py`, `tests/test_livetrade_mt5.py`
- `MT5Broker(client, name, login, server)` is a `LiveBroker`. `name` is `"mt5_mql5"` here, but stays a
  parameter so another MT5 account can be added later with no code change; `symbol_map.json` and
  `live_equity` key on it.
- `get_cash` / `get_total_equity`: report in USD. USD accounts pass through; EUR accounts multiply by `EURUSD=X` from
  `DataService` (fail if the rate is unavailable, never default to 1.0); any other currency raises `ConfigError`.
- `get_positions`: net volume per symbol (hedging accounts hold several tickets per symbol).
- `place_order`: round volume to `volume_step`, clamp to `volume_min/max`, skip with a warning if
  below min, set `deviation` and a filling mode the symbol supports. SELL closes tickets by position id
  (netting- and hedging-safe) and never oversells. A rejection (e.g. 10018 market closed) logs an error
  with the retcode and **raises**, so the run exits 1 instead of looking successful.
- `map_symbol` / `search_symbol` / `is_tradeable` via `symbols_get`, trying the bare ticker plus the suffixes
  `.US`, `.P`, `.O`, `.N` (exact match first; refuse an ambiguous match rather than guess). A
  symbol the server doesn't list is untradeable and its weight stays in cash. `symbol_select` before use.
- `cancel_open_orders` removes pending orders via `orders_get` + `order_send(TRADE_ACTION_REMOVE)`.
  `cash_proxy = None` unless `SHV` is listed. `is_sandbox` is configurable. `account_ref = login`.
- Tests (6, using `FakeMT5`): volume rounding/clamping; market-closed rejection raises; EUR
  account is converted at the given rate and a missing rate raises; an unsupported currency raises; hedged tickets net correctly and a SELL doesn't overshoot; `SPY` resolves to `SPY.P`
  when only the suffixed symbol exists.

### M2: MT5 bridge Deployment
Files: `helm/tradingbots/templates/deployment-mt5-bridge.yaml` (new),
`helm/tradingbots/templates/service-mt5-bridge.yaml` (new), `docs/guides/mt5-bridge.md` (new)
- A single-replica Deployment (`strategy: Recreate`, like eventlogger) running the pinned
  `mt5linux` + Wine image, gated on `.Values.mt5Bridge.enabled`. It has a PVC for the terminal state so
  the login survives restarts and a ClusterIP Service on the RPyC port. It is **not exposed outside the cluster**
  (RPyC is unauthenticated remote code execution: restrict with a NetworkPolicy to the CronJob pods).
  Image, port and PVC size come from `.Values.mt5Bridge.*` using `default`, so this step doesn't edit `values.yaml`.
- The guide explains how the terminal logs in per account, how to check health, and the MetaApi fallback.
- Acceptance: `helm template` with `--set mt5Bridge.enabled=true` renders valid YAML (I run this).

### N1: Numerai submitter job
Files: `tradingbot/numerai_signals_submit.py` (new), `helm/tradingbots/templates/cronjob-numerai-signals.yaml` (new),
`tests/test_numerai_submit.py` (new), `pyproject.toml`
- Entry point: fetch the universe via `SignalsAPI.ticker_universe()`, prices through `DataService`,
  `build_signal` (N0), write a CSV in the Numerai format, `upload_predictions(model_id=...)` for
  `NUMERAI_MODEL_NAME` using `NUMERAI_PUBLIC_ID/SECRET_KEY`. Add `numerapi` to `pyproject.toml`.
- Idempotent per round (a second run the same round doesn't double-upload). Missing credentials exit 2.
  Nothing is printed from credentials.
- Tests (3, numerapi mocked): maps yfinance → Numerai tickers and drops unmapped ones; refuses to upload under
  100 tickers; second run in the same round skips.

### E3: eToro `is_tradeable()` (only if E2's coverage run finds gaps)
Files: `tradingbot/livetrade/etoro.py`, `tests/test_livetrade_etoro.py`. Skipped if there are no gaps.
- Tickers with no eToro instrument return False, so the weight stays in cash rather than aborting under
  `strictMapping`. Test: an unlisted ETF is dropped and a listed one kept. This waits on the ops
  step below and runs after E1 merges.

---

## Batch 2: wiring (3 steps, disjoint files)

### R1: registry + entry point + CronJob template for MT5
Files: `tradingbot/livetrade/registry.py`, `tradingbot/livetrade_mt5_mql5.py`,
`helm/tradingbots/templates/cronjob-livetrade-mt5-mql5.yaml`, `tests/test_livetrade_runner.py`
- One `BrokerSpec` (`mt5_mql5`) whose builder reads `MT5_MQL5_LOGIN/PASSWORD/SERVER` plus
  `MT5_BRIDGE_HOST/PORT`, connect with `connect_rpyc`, and log in. Defaults: `dry_run="false"`, blend weights.
- The CronJob follows the existing `_livetrade.tpl` helpers and pass credentials via secretKeyRef.
- Tests (2): missing `MT5_*` env → exit 2 with nothing sent; the spec builds an `MT5Broker` named `mt5_mql5`.

### D1: coverage run hooks for MT5 + discover_symbols
Files: `tradingbot/livetrade/discover_symbols.py`, `tests/test_discover_symbols_mt5.py`
- Let `discover_symbols --broker mt5_mql5` work (the aliases/registry already generalize; verify
  and fix whatever assumes the five original brokers).
- Test (1): `--broker mt5_mql5` resolves the registry entry and writes entries under that broker key.

### H1: values.yaml + docs (single owner of `values.yaml`, runs last in this batch)
Files: `helm/tradingbots/values.yaml`, `docs/guides/live-trading.md`, `.env.example`, `README.md`
- `liveTrade.etoro`: `enabled: true`, `demo: true`, `dryRun: "false"`, blend weights, comment pointing
  at the C2 block. `mt5Bridge` block. a `liveTrade.mt5Mql5` block with schedule
  `0 15 * * 1-5` and a comment on why they run in-session. A `numerai` block (daily, after the US close).
- Docs: one section per new venue, how to pick an MT5 broker (run `coverage` on a demo first), the manual MQL5
  signal registration steps (free signal from a demo, paid needs a real account), the Numerai "unproven, unstaked" note,
  and a table of all venues with their schedules.
- Acceptance: `helm template` renders `livetrade-etoro` with the blend and `ETORO_DEMO=true`; the MT5 job
  and the Numerai job render too (I run this).

---

## Ops (me, not agy)

- After Batch 0/1: run `coverage --broker etoro` against the eToro API (read-only search), then
  `discover_symbols --broker etoro`, and commit the `etoro` entries in `symbol_map.json`. Run E3 if there are gaps.
- Check `tradingbot-secrets` has `ETORO_*`, `NUMERAI_*` (key names only). Add anything missing through `ske`.
  When you supply the MT5 credentials, add them the same way, run `coverage --broker mt5_mql5`, and flip the MT5 job on
  only if the blend's ETFs resolve. If they don't, the fix is a different broker, not a code change.
- Push, confirm CI is green (the ruff format check blocks deploy), verify `helm list` shows the new revision.
- Smoke tests, one manual job each: eToro (positions match target, demo flag in `live_equity`);
  bridge health (MT5 logs in and holds the session across a pod restart); MT5 copier (needs MT5 secrets);
  Numerai (a submission appears on the model page).
- Done = each venue shows positions or a submission that matches its target. Anything I couldn't verify gets reported as unverified.

## Execution notes
- Wait for the running option-bots gembuild to merge: `agy/work` already exists and `values.yaml` is also edited there.
  The checkpoint (`git switch -c agy/work`) works as written once that branch is merged and deleted.
- The stale worktrees `C1/F1/P1/S1` under `.git/agy-wt` belong to that run. Don't touch them.
- Batch 0 has 4 parallel steps (the max). `pyproject.toml` is touched by M0 (Batch 0) and N1 (Batch 1),
  never in the same batch.
