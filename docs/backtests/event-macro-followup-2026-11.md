# Macro-event sleeve follow-up (2026-11-06)

Most promising candidate from the event studies: scheduled-macro drift in non-equity ETFs. Scripts:
`onetime_fomc_drift_followup.py`, `onetime_macro_sleeve_followup.py`. Prices: daily closes, 2007-03 to 2026-11.

## FOMC -> UUP (long dollar, enter T0 close, exit T+2 close)

- 162 events, mean +0.17% per event (t 2.75; abnormal vs any 3-day UUP window +0.15%, t 2.49).
- Both halves positive (H1 t 1.74, H2 t 2.15); ex-2008/09 still t 2.33.
- Neighbouring windows agree: [0,+3] t 2.88, [0,+1] t 1.82; only [-1,+2] (adds the pre-day) is flat.
- **Placebo:** 5,000 random sets of 162 three-day UUP windows reach t >= 2.75 in 0.8% of draws.
- Weak years exist (2012, 2016, 2019, 2026-so-far negative); 14 of 20 years positive.

## Pooled sleeve, alpha vs QQQ (beta ~0 in every case)

Legs: FOMC UUP [0,+2], CPI TLT [0,+1], pre-FOMC TLT [-1,0], pre-FOMC GLD [-1,0]. About 25-30 held days a year.

| Cost per side | UUP+CPI-TLT alpha / t | All four alpha / t | All four H1 t / H2 t |
|---|---|---|---|
| 0 bp | +3.0% / 3.17 | +5.7% / 3.63 | 2.05 / 3.11 |
| 1 bp | +2.5% / 2.72 | +5.0% / 3.16 | 1.73 / 2.78 |
| 2 bp | +2.1% / 2.28 | +4.2% / 2.69 | 1.41 / 2.44 |
| 3 bp | +1.7% / 1.83 | +3.5% / 2.22 | 1.08 / 2.09 |
| 5 bp (framework default) | +0.9% / 0.92 | +2.0% / 1.27 | 0.42 / 1.40 |

UUP alone at 5 bp: +0.6%/yr, t 1.11.

## Verdict: `unproven`, best lead so far, not shippable as an edge

- The four legs were picked after seeing 40 variants, so t 3.6 is overstated. The placebo covers one leg only.
  Honest read: a real-looking but small effect, about +2-4%/yr alpha at beta 0, if costs are 2-3 bp.
- It does not survive the framework's default 5 bp/side. TLT and GLD trade at about 1 bp, UUP wider; true cost is unmeasured.
- It cannot be levered here, so the ceiling is a few percent a year on a sleeve idle 90% of the year.
- Next test would be a genuine out-of-sample: paper-trade the four legs forward (about 33 FOMC+CPI events a year, so a
  year gives only about 130 event-days; t >= 2 will take years). Not built.
