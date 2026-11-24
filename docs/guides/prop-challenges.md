# Prop-Firm Challenge Simulator & Forward Paper Sims

This guide explains the proprietary trading firm ("prop firm") challenge simulator, the historical study methodology, and the daily forward paper simulation tracking.

---

## 1. What It Is and Why

### The Proposition
Proprietary trading firms offer evaluation challenges where a trader pays an upfront fee to trade a simulated account with substantial notional capital (e.g., $50,000 to $100,000). If the trader reaches a defined profit target (e.g., 6% to 10%) without violating daily loss limits or maximum drawdown limits, they graduate to a "funded" account and receive a profit split (typically 80% to 90%) on subsequent simulated gains.

On paper, this sounds like an asymmetric bet: risk a few hundred dollars in fees to harvest thousands in profit splits.

### The Problem: Absorbing Barriers
In practice, prop firm risk limits act as severe absorbing barriers:
- Maximum drawdowns of 4% to 10% leave very little cushion when trading with leverage.
- Daily loss limits (often 5%) can be triggered by short-term market volatility or single-day shocks.
- Trailing drawdown rules ratchets the liquidation threshold upwards as equity rises, locking in losses at the peak.

To evaluate whether candidate bot return streams yield positive expected value (EV) after fees, the framework implements a pure numpy/pandas simulation engine in `tradingbot/utils/prop_challenge.py`.

### The Verdict: Not Proven
Can an automated trading bot pay for itself by buying prop-firm challenges?

**The verdict is not proven.** As detailed in the [Prop-Firm Challenge Simulator Study (2026-11)](../backtests/prop-challenge-2026-11.md):
- In backtested simulations, the edge of candidate bots over a synthetic zero-alpha control or a simple QQQ buy-and-hold twin is narrow.
- Daily-close simulations understate intraday drawdown swings; stress-testing losing days by a modest 25% flips headline expected values negative.
- The return streams tested have negative out-of-sample live alpha since sleeve deployment in September 2026, meaning live bots would have breached challenges rapidly.

---

## 2. How a Challenge Is Modelled

The simulator models challenge rules, account state progression, and payout economics using dataclasses in `tradingbot/utils/prop_challenge.py`.

### Phases and Evaluation Rules (`PhaseRules`)
Each challenge consists of one or more sequential evaluation phases (e.g., Phase 1 and Phase 2), followed by a funded account stage. Each phase is configured by a `PhaseRules` instance:

```python
from tradingbot.utils.prop_challenge import PhaseRules

rules = PhaseRules(
    profit_target=0.10,
    max_loss=0.10,
    trailing="static",
    daily_loss=0.05,
    max_days=None,
    min_days=4,
    consistency=None,
)
```

1. **Profit Target (`profit_target`):** Required fractional gain relative to starting balance (e.g., `0.10` for +10%). In the funded stage, this is set to `0.0` (ignored).
2. **Daily Loss (`daily_loss`):** Maximum loss allowed in a single trading day, measured from prior day's close in starting-balance units. If `daily_pnl <= -daily_loss`, the account breaches immediately.
3. **Maximum Drawdown (`max_loss` and `trailing`):**
   - **`static`:** Fixed breach limit relative to initial equity (`limit = 1.0 - max_loss`).
   - **`eod_trailing`:** Limit trails peak end-of-day equity, locking at starting balance (`limit = min(peak_eod - max_loss, 1.0)`).
4. **Minimum Days (`min_days`):** Minimum trading days required before a profit-target pass is recognized.
5. **Consistency Rule (`consistency`):** Fraction specifying that the single best day's profit cannot exceed `consistency * total_profit` at the time the target is reached (e.g., `0.55` means best day must be $\le$ 55% of total profits). If violated when reaching target, trading continues until total profit dilutes the best day.
6. **Breach Priority:** Breach checks are evaluated before pass checks on the same day (conservative ordering).

### Payouts and Fee Economics (`FirmPreset`)
Challenge terms, recurring costs, and payout rules are captured in `FirmPreset`:

```python
from tradingbot.utils.prop_challenge import FirmPreset, PhaseRules

preset = FirmPreset(
    name="sample_preset",
    phases=(PhaseRules(profit_target=0.10, max_loss=0.10, trailing="static", daily_loss=0.05),),
    funded=PhaseRules(profit_target=0.0, max_loss=0.10, trailing="static", daily_loss=0.05),
    fee=540.0,
    account_size=100_000.0,
    profit_split=0.8,
    payout_every_days=21,
    fee_refund_on_first_payout=True,
    financing_markup=0.02,
    note="Sample preset note.",
    monthly_fee=0.0,
    activation_fee=0.0,
    max_payout_request=None,
)
```

- **Upfront Fee (`fee`):** Paid per attempt.
- **Monthly Fee (`monthly_fee`):** Charged per started 21-day block during the evaluation phase: $\lceil \text{days} / 21 \rceil \times \text{monthly\_fee}$.
- **Activation Fee (`activation_fee`):** One-time fee charged upon passing evaluation (e.g., Topstep Express funded account).
- **Fee Refund (`fee_refund_on_first_payout`):** If true, the upfront fee is refunded upon receiving the first funded payout.
- **Payout Frequency (`payout_every_days`):** Number of trading days survived in funded status between profit withdrawal requests.
- **Payout Splits & Caps (`profit_split`, `max_payout_request`):** Every `payout_every_days`, if equity exceeds starting balance, profit is calculated. Payout is gross profit (capped at `max_payout_request` if set) multiplied by `profit_split`. Equity resets by the gross withdrawal, leaving excess profits in the account.
- **Financing Markup (`financing_markup`):** Annual financing drag above the risk-free rate on notional capital (simulating CFD swap rates or financing markups). Scaled returns follow:
  $$\tilde{r} = L \cdot r - L \cdot r_f - \frac{L \cdot \text{financing\_markup}}{252}$$

### Rule-to-Field Mapping

| Class | Field | Rule Modelled |
|---|---|---|
| `PhaseRules` | `profit_target` | Required gain on starting balance to pass phase (e.g. 0.10 = +10%) |
| `PhaseRules` | `max_loss` | Maximum permitted cumulative loss fraction before breach |
| `PhaseRules` | `trailing` | Drawdown boundary type: `"static"` or `"eod_trailing"` |
| `PhaseRules` | `daily_loss` | Maximum loss in one day relative to prior close (`None` if unconstrained) |
| `PhaseRules` | `max_days` | Time limit in trading days (`None` = unlimited) |
| `PhaseRules` | `min_days` | Minimum trading days required before passing |
| `PhaseRules` | `consistency` | Best-day profit cap as fraction of total profit (e.g. 0.55) |
| `FirmPreset` | `phases` | Tuple of sequential evaluation phases |
| `FirmPreset` | `funded` | Risk rules applied once funded stage is reached |
| `FirmPreset` | `fee` | Upfront evaluation challenge cost in USD |
| `FirmPreset` | `account_size` | Account notional value in USD (e.g. $50,000 or $100,000) |
| `FirmPreset` | `profit_split` | Trader's fractional share of funded profits (e.g. 0.8 or 0.9) |
| `FirmPreset` | `payout_every_days` | Trading days between payout calculation dates |
| `FirmPreset` | `fee_refund_on_first_payout` | Refund initial challenge fee on first successful payout |
| `FirmPreset` | `financing_markup` | Annualized financing/swap cost on notional capital |
| `FirmPreset` | `monthly_fee` | Monthly recurring subscription charged during evaluation |
| `FirmPreset` | `activation_fee` | One-time funded account activation fee |
| `FirmPreset` | `max_payout_request` | USD cap per payout withdrawal |
| `FirmPreset` | `note` | Provenance, source date, unmodelled rules, and verification warnings |

---

## 3. The Three Presets

The framework defines three standard presets in `PRESETS` (`tradingbot/utils/prop_challenge.py`). Each preset includes an explicit note detailing what the model does **NOT** capture.

### 1. `ftmo_2step_100k`
- **Structure:** 2-step challenge ($100,000 notional).
- **Rules:** Phase 1 target 10%, Phase 2 target 5%. Max loss 10% (static), daily loss 5%, min 4 trading days, unlimited time. Fee $540, 80% split, 21-day payouts, fee refunded on first payout, 2% annual CFD financing markup.
- **Unmodelled Rules (from code note):**
  > "FTMO 2-step 100k, matched against ftmo.com/en/2-step-challenge on 2026-11-24: targets 10%/5%, 5% daily, 10% max loss, 4 min days, unlimited time, fee refunded at first reward. Split is 80% (up to 90% per FTMO). Fee shown is the list price in EUR treated as USD. **NOT modelled:** the 14-day minimum before the first reward and the CFD instrument limits. **VERIFY before buying.**"

### 2. `topstep_50k`
- **Structure:** 1-step Combine ($50,000 notional).
- **Rules:** Target 6% ($3,000). Max loss 4% ($2,000 end-of-day trailing locking at starting balance), no daily loss rule, min 2 trading days, best day under 55% of target. Upfront fee $0, monthly evaluation fee $49/month, activation fee $149 on Express account, 90% split, 5-day payouts, $2,000 cap per payout request, 0% financing markup (futures).
- **Unmodelled Rules (from code note):**
  > "Topstep 50k Combine, read from help.topstep.com on 2026-11-24: target $3,000, end-of-day trailing $2,000 loss limit locking at the start balance, best day under 55% of target, 2 min days, $49/month, $149 activation on the Express account, 90% split, $2,000 cap per payout request. **NOT modelled:** the optional daily loss limit, the loss limit dropping to $0 after the first payout, the 40% consistency objective and 5-winning-day payout gate on the funded account, the $125 minimum payout, and the undocumented VPS policy. **VERIFY before buying.**"

### 3. `the5ers_hs_100k`
- **Structure:** 2-step High Stakes ($100,000 notional).
- **Rules:** Phase 1 target 10%, Phase 2 target 5%. Max loss 10% (static), daily loss 5%, min 3 trading days, unlimited time. Fee $545, 80% split, 21-day payouts, $4,000 cap per payout request, fee refunded on first payout, 2% annual CFD financing markup.
- **Unmodelled Rules (from code note):**
  > "The5ers High Stakes 100k, from search snippets of the5ers.com FAQs on 2026-11-24 (pages could not be fetched): two steps, unlimited time, 10% max loss, 5% daily, payout cap $4,000. Sources conflict on the step-1 target (8% or 10%; 10% used) and the fee ($545 from one promo). Minimum-days rule is three profitable days of 0.5%, approximated as 3 trading days. **VERIFY before buying.**"

---

## 4. Running the Historical Study

The study script `scripts/onetime_prop_challenge_study.py` evaluates candidate return streams across a leverage grid ($1\times$ to $6\times$) using circular block bootstrap and historical rolling paths.

### Execution Commands
Running the study takes approximately **10 minutes**:

```bash
# Print summary tables to stdout only (does not overwrite docs)
python scripts/onetime_prop_challenge_study.py

# Stress test: losses are 25% worse intraday (printed to terminal)
python scripts/onetime_prop_challenge_study.py --intraday-haircut 1.25

# Sensitivity test: keep only 50% of backtested alpha vs QQQ
python scripts/onetime_prop_challenge_study.py --alpha-keep 0.5

# Recompute and overwrite the backtest documentation page
python scripts/onetime_prop_challenge_study.py --write-doc
```

### Command Flags
- `--write-doc`: Writes markdown tables and output directly to `docs/backtests/prop-challenge-2026-11.md`. **Without this flag, the file is not modified.**
- `--intraday-haircut <float>` (default: `1.0`): Multiplier applied to negative daily returns prior to checking breach thresholds. Addresses the issue where daily closing prices understate intraday drawdown spikes.
- `--alpha-keep <float>` (default: `1.0`): Fraction of backtested alpha retained versus QQQ (e.g., `0.5` cuts alpha by half).

### Methodology & Walk-Forward Split
- **Dataset:** Daily returns from 2019-01-02 to 2026-11-23.
- **Split Date:** `SPLIT = "2023-07-01"`.
- **H1 Selection Window:** 2019-01-02 to 2023-07-01. The optimal leverage for each candidate is selected purely on H1 by maximizing expected value per attempt ($EV/\text{att}$).
- **H2 Out-of-Sample Window:** 2023-07-01 to 2026-11-23. The chosen leverage is locked and evaluated out-of-sample on H2.
- **Path Generation:**
  - Circular block bootstrap: $N = 5{,}000$ paths, block length $b = 21$ trading days, horizon $H = 252$ days.
  - Historical rolling paths: horizon $H = 252$ days, step = 5 days.

---

## 5. Forward Paper Sims

To avoid relying solely on backtests, forward paper simulations run daily in production.

### What Runs and When
- **Entrypoint:** `tradingbot/propsimlive.py`, using logic in `tradingbot/utils/prop_forward.py`.
- **Schedule:** `40 22 * * 1-5` (22:40 UTC Monday through Friday), scheduled in `helm/tradingbots/values.yaml` (`propsimlive`). This runs shortly after the 22:00 UTC `portfolioworth` job.
- **Execution Mode:** Paper tracking only. **Nothing trades.**
- **Stale Data Guard:** If any source return series has a gap exceeding `MAX_GAP_DAYS` (4 days), the script raises `LookupError` and exits with code 1, preventing stale data from masquerading as frozen progress.

### The QQQ Twin as Control
For every candidate simulation, a paired **QQQ twin** runs at the exact same leverage and firm preset:
- Pass by bot + Pass by QQQ twin $\rightarrow$ Market volatility passed the challenge, not bot edge.
- Pass by bot + Breach by QQQ twin $\rightarrow$ Plausible evidence of alpha.

The production configuration in `SIMS` tracks the live C2 blend (TARegimeMultiAsset 0.39, TSMOM 0.24, GoldenButterflyMomBot 0.37) and QQQ twins at the H1-selected optimal leverages:
- `ftmo_2step_100k`: $4.0\times$
- `the5ers_hs_100k`: $4.0\times$
- `topstep_50k`: $2.0\times$

### Database Schema (`prop_challenge_sim`)
Snapshots are recorded in the PostgreSQL table `prop_challenge_sim` (model `PropChallengeSim` in `tradingbot/utils/db.py`):

| Column | Type | Description |
|---|---|---|
| `as_of` | `Date` | Snapshot date (composite primary key with `sim_name`) |
| `sim_name` | `String` | Simulation identifier (e.g. `blend_ftmo_2step_100k_4x`) |
| `preset` | `String` | Firm preset (`ftmo_2step_100k`, `topstep_50k`, `the5ers_hs_100k`) |
| `source` | `String` | Return stream source (`blend` or `qqq`) |
| `leverage` | `Float` | Applied leverage multiplier |
| `start_date` | `Date` | Fixed simulation start date (`2026-11-24` or `PROPSIM_START`) |
| `stage` | `String` | Current stage (`"phase 1"`, `"phase 2"`, or `"funded"`) |
| `status` | `String` | Account status (`"running"`, `"breached"`, or `"funded"`) |
| `trading_days` | `Integer` | Total trading days evaluated since inception |
| `stage_day` | `Integer` | Trading days elapsed inside the current stage |
| `equity` | `Float` | Current normalized equity ($1.0 =$ stage start balance) |
| `limit_equity` | `Float` | Breach threshold equity level for today |
| `target_equity` | `Float` | Target equity level required to pass current stage |
| `cum_return` | `Float` | Compounded levered return since start (ignoring withdrawals) |
| `payouts_usd` | `Float` | Cumulative payout USD received while in funded status |

### Inspecting State via SQL
Use this sample query from `docs/backtests/prop-challenge-2026-11.md` to view the latest status:

```sql
select as_of, sim_name, stage, status, trading_days, equity, limit_equity, target_equity, cum_return
from prop_challenge_sim where as_of = (select max(as_of) from prop_challenge_sim) order by sim_name;
```

### Reading State & Breaches
- **`stage` / `status`:** A healthy evaluation shows `stage: "phase 1"` with `status: "running"`. Once passed, it transitions to `stage: "phase 2"`, and finally `stage: "funded"` with `status: "funded"`.
- **`equity`, `limit_equity`, `target_equity`:** If equity starts at $1.000$, a 10% target displays `target_equity: 1.100`. A static 10% max loss displays `limit_equity: 0.900`.
- **What a Breach Looks Like:** If `equity <= limit_equity` (or daily drop $\le -\text{daily\_loss}$), the simulation immediately transitions to `status: "breached"`. The stage, equity, and limit remain frozen. A state change generates a log warning:
  ```text
  WARNING: blend_ftmo_2step_100k_4x: phase 1/running -> phase 1/breached
  ```

---

## 6. How to Read the Results Honestly

When evaluating prop challenge metrics, keep four critical factors in mind:

### 1. The Zero-Alpha Control
A bot can show positive EV simply because prop challenge rules provide asymmetric payoff: upside is captured via payouts, while downside is capped at the challenge fee. To check if positive EV comes from true skill or from gambling variance, compare against the **zero-alpha control** (`blend minus alpha`):
- In H2 testing, the C2 blend achieved $+1{,}080$ EV on FTMO, but the zero-alpha control also produced $+172$ EV at the same leverage.
- On Topstep, QQQ achieved $+759$ EV while the blend achieved $+598$ EV. The bot possessed no edge over buying QQQ.

### 2. The Intraday Haircut
Evaluating daily closes ignores intraday pathing:
- If an account falls 6% intraday before rebounding to close down 3%, a daily-close simulation marks the day as safe.
- In reality, the prop firm's automated liquidation engine breaches the account the instant the 5% daily limit is crossed intraday.
- In the study, applying a modest 25% intraday stress (`--intraday-haircut 1.25`) drops FTMO EV from $+1{,}080$ to $-\$360$, and The5ers EV from $+752$ to $-\$412$.

### 3. Unproven Live Alpha
Backtests assume past Sharpe ratios persist:
- Between sleeve deployment on 2026-09-21 and 2026-11-24, live sleeve returns were negative: TARegimeMultiAsset $-4.5\%$, TSMOM $-9.8\%$, GoldenButterfly $-1.9\%$ (blend $-4.9\%$ vs QQQ $-12.2\%$).
- Replaying these live returns through challenge rules breaches all blend accounts within 33 trading days.

### 4. Rules Drift & Omissions
Prop firm terms change frequently. Presets do not model contract limits, news-trading restrictions, minimum winning day rules for payouts, or swap fees on open CFD positions over weekends.

For detailed numbers and tables, see [docs/backtests/prop-challenge-2026-11.md](../backtests/prop-challenge-2026-11.md).

---

## 7. Extending

### Adding a Preset
To define a new prop firm or challenge tier, add an entry to `PRESETS` in `tradingbot/utils/prop_challenge.py`:

```python
from tradingbot.utils.prop_challenge import FirmPreset, PhaseRules

PRESETS["myfirm_100k"] = FirmPreset(
    name="myfirm_100k",
    phases=(
        PhaseRules(
            profit_target=0.08,
            max_loss=0.08,
            trailing="static",
            daily_loss=0.04,
            max_days=None,
            min_days=5,
            consistency=None,
        ),
    ),
    funded=PhaseRules(
        profit_target=0.0,
        max_loss=0.08,
        trailing="static",
        daily_loss=0.04,
        max_days=None,
        min_days=0,
        consistency=None,
    ),
    fee=499.0,
    account_size=100_000.0,
    profit_split=0.80,
    payout_every_days=14,
    fee_refund_on_first_payout=True,
    financing_markup=0.02,
    note="MyFirm 100k 1-step verified on YYYY-MM-DD. NOT modelled: news restrictions. VERIFY before buying.",
    monthly_fee=0.0,
    activation_fee=0.0,
    max_payout_request=None,
)
```

### Adding a Forward Sim (`SIMS`)
To add a forward sim specification, update `tradingbot/utils/prop_forward.py`:

1. Define or update leverage in `_LEVERAGE`:
   ```python
   _LEVERAGE = {
       "ftmo_2step_100k": 4.0,
       "the5ers_hs_100k": 4.0,
       "topstep_50k": 2.0,
       "myfirm_100k": 3.0,
   }
   ```
2. If adding a new candidate sleeve basket, add it to `SOURCES`:
   ```python
   SOURCES["custom_bot"] = {"TSMOMTrendBot": 1.0}
   ```
   The `SIMS` tuple automatically constructs the corresponding `SimSpec` instances and their QQQ twins.

### Changing the Start Date
The forward simulator starts evaluating from `DEFAULT_START = date(2026, 11, 24)`. To reset or change the evaluation baseline:
- **Locally:** Set the `PROPSIM_START` environment variable:
  ```bash
  export PROPSIM_START="2027-01-01"
  python -m tradingbot.propsimlive
  ```
- **In Kubernetes:** Add or update the `PROPSIM_START` environment variable in `helm/tradingbots/values.yaml` under the `propsimlive` CronJob container environment.
