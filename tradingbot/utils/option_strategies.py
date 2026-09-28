"""
The option strategies as pure decisions: decide_x(market, holdings, rules, ...) -> actions.

Each function is the body of one bot's makeOneIteration, moved here unchanged
in logic. It reads through the Market and Holdings interfaces
(utils/option_decide.py) and returns Open / Close / Hedge actions instead of
trading. The live bot (utils/option_strategy_bot.py), the replay over stored
chains (utils/option_replay.py) and, for index vol, the synthetic backtest
all execute the same function. See option_decide.py for why.

Every rule the functions apply is in utils/option_rules.py. The bots'
docstrings describe the strategies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import market_calendar, options
from . import option_math as om
from .option_decide import Action, Close, Hedge, Holdings, Market, Open
from .option_rules import (
    CrossVolRules,
    DispersionRules,
    EarningsCrushRules,
    IndexVolRules,
    MispricingScanRules,
    business_days,
    cross_vol_candidates,
    cross_vol_exit_reason,
    dispersion_signal,
    earnings_crush_entry_ok,
    earnings_crush_exit_due,
    index_vol_entry_ok,
    index_vol_exit_reason,
    index_vol_unwind_reason,
    reaction_session,
    scan_exit_reason,
    scan_side,
    vega_weighted_lots,
)
from .vol_indices import VIX, VIX3M, VVIX, term_ratio

logger = logging.getLogger(__name__)

DIVIDEND_REASON = "short call at risk of early assignment before the ex-dividend date"


def _fmt(x, spec: str) -> str:
    return format(x, spec) if x is not None else "n/a"


def _threatened(market: Market, book: options.OptionBook) -> bool:
    return bool(options.dividend_threatened_calls(book, market.next_dividend(book.underlying)))


# ------------------------------------------------------------------
# option_IndexVolBot
# ------------------------------------------------------------------


def decide_indexvol(market: Market, holdings: Holdings, rules: IndexVolRules, underlying: str = "SPY") -> list[Action]:
    """SPY iron condors while ATM IV beats a HAR forecast by min_gap (and the gates allow)."""
    if underlying in holdings.underlyings():
        book = holdings.book(underlying)
        reason = index_vol_exit_reason(book.credit, book.pnl, book.dte, rules)
        if reason is None and rules.unwind_term_ratio is not None:
            reason = index_vol_unwind_reason(term_ratio(market.vol_index(VIX), market.vol_index(VIX3M)), rules)
        logger.info(
            "Holding condor: credit %.2f, P&L %.2f, %s DTE, delta %.1f, theta %.2f/day, vega %.2f",
            book.credit,
            book.pnl,
            book.dte,
            book.greeks.delta,
            book.greeks.theta,
            book.greeks.vega,
        )
        if reason:
            logger.info("Closing: %s", reason)
            return [Close(underlying, reason=reason)]
        return []

    view = market.chain(underlying, rules.target_dte)
    if view is None or not view.live:
        logger.info("Chain not live; not opening a condor off-hours")
        return []
    close = market.closes(underlying)
    fair = om.har_rv_forecast(om.log_returns(close), max(business_days(view.today, view.expiry), 1))
    iv = options.atm_iv(view)
    vix, vix3m, vvix = market.vol_index(VIX), market.vol_index(VIX3M), market.vol_index(VVIX)
    term = term_ratio(vix, vix3m)
    event, bdays_to_event = market.next_macro_event()
    logger.info(
        "%s %s: ATM IV %s vs HAR fair %.1f%% (gap %s), VIX %s, VIX/VIX3M %s, VVIX %s, next event %s",
        underlying,
        view.expiry,
        _fmt(iv, ".1%"),
        fair * 100,
        f"{iv - fair:+.1%}" if iv else "n/a",
        _fmt(vix, ".1f"),
        _fmt(term, ".2f"),
        _fmt(vvix, ".0f"),
        f"{event[0]} {event[1]} ({bdays_to_event} sessions)" if event else "unknown",
    )
    if not index_vol_entry_ok(iv, fair, vix, rules, term_ratio=term, vvix=vvix, bdays_to_event=bdays_to_event):
        return []
    pick = options.select_iron_condor(
        underlying,
        rules.put_delta,
        rules.width_pct * view.spot,
        rules.target_dte,
        view=view,
        call_delta=rules.call_delta,
    )
    return [Open(pick, rules.max_risk_pct * holdings.equity(), reason=f"IV {iv:.1%} vs fair {fair:.1%}")]


# ------------------------------------------------------------------
# option_CrossVolBot
# ------------------------------------------------------------------


def decide_crossvol(market: Market, holdings: Holdings, rules: CrossVolRules, universe) -> list[Action]:
    """Condors on the names furthest above their Yang-Zhang HAR forecast, up to max_positions."""
    actions: list[Action] = []
    for u in sorted(holdings.underlyings()):
        book = holdings.book(u)
        if book.empty:
            continue
        reason = cross_vol_exit_reason(book.credit, book.pnl, book.dte, rules)
        if not reason and _threatened(market, book):
            reason = DIVIDEND_REASON
        logger.info("%s condor: credit %.2f, P&L %.2f, %s DTE", u, book.credit, book.pnl, book.dte)
        if reason:
            logger.info("Closing %s: %s", u, reason)
            actions.append(Close(u, reason=reason))
    closed = {a.underlying for a in actions}
    held = holdings.underlyings() - closed
    if len(held) >= rules.max_positions:
        logger.info("Holding %d condors (%s): no free slot", len(held), sorted(held))
        return actions
    vix = market.vol_index(VIX)
    if vix is None or vix >= rules.max_vix:
        logger.info("VIX %s >= %.0f (or unknown): no new short vol", _fmt(vix, ".1f"), rules.max_vix)
        return actions

    from . import mispricing_scan as ms

    names = ms.shortlist(universe, market.scan_scores(), rules.shortlist)
    ohlc = market.ohlc(names)
    scanned, views = [], {}
    for u in names:
        if u in held:
            continue
        nv, view = market.name_vol(u, rules.target_dte, ohlc.get(u))
        if view is None or not view.live:
            logger.info("%s: chain not live", u)
            continue
        scanned.append(nv)
        views[u] = view
        logger.info(
            "%s %s: IV %s fair %s gap %s, earnings clear %s",
            u,
            view.expiry,
            _fmt(nv.iv, ".1%"),
            _fmt(nv.fair, ".1%"),
            _fmt(nv.gap, "+.1%"),
            nv.earnings_clear,
        )
    if not views:
        logger.info("No live chain among the shortlist; not opening off-hours")
        return actions

    budget = rules.risk_per_name_pct * holdings.equity()
    for nv in cross_vol_candidates(scanned, held, rules):
        view = views[nv.underlying]
        pick = options.select_iron_condor(
            nv.underlying, rules.short_delta, rules.width_pct * view.spot, rules.target_dte, view=view
        )
        actions.append(Open(pick, budget, reason=f"gap {nv.gap * 100:+.1f} pts"))
    return actions


# ------------------------------------------------------------------
# option_EarningsCrushBot
# ------------------------------------------------------------------

ENTRY_WINDOW_MINUTES = 120  # runs this close to the session's close may enter; earlier runs only exit


def in_entry_window(now: datetime) -> bool:
    """True for a run in the last ENTRY_WINDOW_MINUTES before today's close (early closes included)."""
    left = market_calendar.minutes_to_close(now)
    return left is not None and 0 < left <= ENTRY_WINDOW_MINUTES


def decide_earningscrush(market: Market, holdings: Holdings, rules: EarningsCrushRules, universe) -> list[Action]:
    """Iron butterflies over reports whose two-expiry implied move is rich against history."""
    today = market.today
    actions: list[Action] = []
    # Entries happen only on the session right before the reaction, so by the
    # next run the report has happened: close everything opened before today.
    for u in sorted(holdings.underlyings()):
        opened = holdings.opened_on(u)
        if earnings_crush_exit_due(today, None if opened is None else opened + timedelta(days=1)):
            book = holdings.book(u)
            logger.info("Closing %s after the report: P&L %.2f on credit %.2f", u, book.pnl, book.credit)
            actions.append(Close(u, reason="report passed"))
    if not in_entry_window(market.now):
        return actions
    held = holdings.underlyings() - {a.underlying for a in actions}
    if len(held) >= rules.max_concurrent:
        logger.info("Holding %d earnings trades: no free slot", len(held))
        return actions

    reporting = []
    for u in universe:
        if u in held:
            continue
        event = market.next_earnings_event(u)
        if event is None:
            continue
        reaction = reaction_session(*event)
        if reaction is not None and business_days(today, reaction) == 1:
            reporting.append((u, event, reaction))
    if not reporting:
        logger.info("No name reacts to earnings tomorrow")
        return actions
    ohlc = market.ohlc([u for u, _, _ in reporting])
    budget = rules.risk_per_trade_pct * holdings.equity()
    opened = 0
    for u, (report, after_close), reaction in reporting:
        if len(held) + opened >= rules.max_concurrent:
            break
        try:
            action = _earnings_crush_entry(market, rules, u, report, after_close, reaction, ohlc.get(u), budget)
        except Exception as exc:
            logger.warning("%s: skipped (%s)", u, exc)
            action = None
        if action is not None:
            actions.append(action)
            opened += 1
    return actions


def _earnings_crush_entry(market, rules, u, report, after_close, reaction, ohlc, budget) -> Open | None:
    today = market.today
    front = market.chain(u, (reaction - today).days)
    if front is None or not front.live:
        logger.info("%s: chain not live", u)
        return None
    if (front.expiry - reaction).days > rules.front_max_days_after:
        logger.info("%s: first expiry %s too long after the report", u, front.expiry)
        return None
    back = market.chain(u, (front.expiry - today).days + rules.back_min_days_after_front)
    front_iv, back_iv = options.atm_iv(front), options.atm_iv(back) if back is not None else None
    implied = om.implied_earnings_move(front_iv, front.T, back_iv, back.T) if front_iv and back_iv else None
    hist, n = None, 0
    if ohlc is not None:
        events = [e for e in market.earnings_events(u) if e[0] < today]
        reactions = om.earnings_reaction_returns(ohlc["close"], [d for d, _ in events], dict(events))
        recent = reactions.tail(rules.hist_events)
        n, hist = len(recent), (om.earnings_jump(recent.tolist()) if len(recent) else None)
    ok, why = earnings_crush_entry_ok(today, reaction, implied, hist, n, rules)
    logger.info("%s reports %s (%s): %s", u, report, "after close" if after_close else "before open", why)
    if not ok:
        return None
    width = rules.wing_moves * implied * front.spot
    return Open(options.select_iron_butterfly(front, width), budget, reason=f"wings {width:.2f} away")


# ------------------------------------------------------------------
# option_MispricingScanBot
# ------------------------------------------------------------------


def decide_mispricingscan(
    market: Market, holdings: Holdings, rules: MispricingScanRules, universe, always=("SPY", "QQQ")
) -> list[Action]:
    """Both directions at |z| >= 2 of each name's own IV-vs-forecast gap, sized by a book-wide vega budget."""
    from . import mispricing_scan as ms

    vix = market.vol_index(VIX)
    term = term_ratio(vix, market.vol_index(VIX3M))
    actions = _scan_manage(market, holdings, rules, term)
    closed = {a.underlying for a in actions if isinstance(a, Close)}
    held = holdings.underlyings() - closed
    if len(held) >= rules.max_positions:
        logger.info("Holding %d positions: no free slot", len(held))
        return actions

    names = ms.shortlist(universe, market.scan_scores(), rules.shortlist, always=always)
    ohlc = market.ohlc(names)
    ranked = []
    for u in names:
        if u in held:
            continue
        nv, view = market.name_vol(u, rules.target_dte, ohlc.get(u), min_obs=rules.min_obs)
        side, why = scan_side(nv, rules, vix)
        logger.info(
            "%s: IV %s, fair %s, z %s -> %s (%s)",
            u,
            _fmt(nv.iv, ".1%"),
            _fmt(nv.fair, ".1%"),
            _fmt(nv.z, "+.2f"),
            side or "no trade",
            why,
        )
        if side and view is not None and view.live:
            if side == "rich" and term is not None and term > rules.unwind_term_ratio:
                logger.info("%s: rich, but VIX/VIX3M %.2f is inverted: no new short vol", u, term)
                continue
            ranked.append((abs(nv.z), u, side, view))
    if not ranked:
        return actions

    equity = holdings.equity()
    r = market.risk_free_rate()
    # Book risk after the closes above, grown by each position proposed here:
    # the executor opens them in this order, so each one is sized against the
    # book as it will stand, as when the bot re-read its book after every open.
    book_vega = book_crash = 0.0
    for u in held:
        book = holdings.book(u)
        book_vega += abs(book.greeks.vega)
        book_crash += om.worst_stress(options.stress_legs(book), book.spot, r)
    opened = 0
    for _, u, side, view in sorted(ranked, key=lambda x: -x[0]):
        if len(held) + opened >= rules.max_positions:
            break
        proposal = _scan_open(u, side, view, rules, equity, book_vega, book_crash, r)
        if proposal is None:
            continue
        open_action, vega, crash = proposal
        actions.append(open_action)
        if side == "cheap":
            actions.append(Hedge(u, 0.0, ww=(rules.hedge_cost_frac, rules.hedge_ww_risk_aversion)))
        book_vega += vega
        book_crash += crash
        opened += 1
    return actions


def _scan_pick(u: str, side: str, view: options.ChainView, rules: MispricingScanRules) -> options.StructurePick:
    if side == "cheap":
        return options.select_straddle(view)
    fit = options.svi_surface_fit(view)
    if fit.outliers:
        logger.info(
            "%s SVI outliers: %s", u, ", ".join(f"{o.right}{o.strike:g} {o.resid:+.1%}" for o in fit.outliers[:4])
        )
    return options.select_iron_condor(u, rules.short_delta, rules.width_pct * view.spot, rules.target_dte, view=view)


def _scan_open(u, side, view, rules, equity, book_vega, book_crash, r) -> tuple[Open, float, float] | None:
    """(the Open, its vega, its crash P&L), sized so the book stays in the vega and crash caps; None if nothing fits."""
    try:
        pick = _scan_pick(u, side, view, rules)
        m = options.pick_metrics(view, pick)
    except Exception as exc:
        logger.warning("%s: no %s structure (%s)", u, side, exc)
        return None
    budget = rules.vega_budget_pct * equity
    unit_vega = abs(m.vega)
    if unit_vega <= 0:
        return None
    units = max(int((budget / rules.max_positions) // unit_vega), 1)
    crash = 0.0
    while units > 0:
        crash = om.worst_stress(options.pick_stress_legs(view, pick, units), view.spot, r)
        if book_vega + units * unit_vega <= budget and -(book_crash + crash) <= rules.stress_cap_pct * equity:
            break
        units -= 1
    if units == 0:
        logger.info(
            "%s %s skipped: vega %.0f/pt per unit on a book at %.0f of %.0f, or the crash cap binds",
            u,
            side,
            unit_vega,
            book_vega,
            budget,
        )
        return None
    risk = units * (m.max_loss if side == "rich" else m.price) * 1.05
    logger.info("%s: %d x %s (%s), vega %+.0f/pt", u, units, side, pick.legs, units * m.vega)
    return Open(pick, risk, reason=f"{side} z"), units * unit_vega, crash


def _scan_manage(market: Market, holdings: Holdings, rules: MispricingScanRules, term) -> list[Action]:

    held = sorted(holdings.underlyings())
    if not held:
        return []
    actions: list[Action] = []
    ohlc = market.ohlc(held)
    for u in held:
        book = holdings.book(u)
        if book.empty:
            continue
        side = "rich" if book.entry_value < 0 else "cheap"
        opened = holdings.opened_on(u)
        held_days = business_days(opened, book.today) if opened else 0
        pnl, stake = book.pnl, abs(book.entry_value)
        if side == "cheap" and opened:
            pnl = holdings.structure_pnl(u, book)
        if side == "rich" and term is not None and term > rules.unwind_term_ratio:
            reason = f"VIX/VIX3M {term:.2f} inverted: unwinding short vol"
        elif side == "rich" and _threatened(market, book):
            reason = DIVIDEND_REASON
        else:
            nv, _ = market.name_vol(u, book.dte or rules.target_dte, ohlc.get(u), min_obs=rules.min_obs)
            reason = scan_exit_reason(side, nv.z, pnl, stake, book.dte, held_days, rules)
        logger.info("%s %s: P&L %.0f on %.0f, %s DTE, held %d days", u, side, pnl, stake, book.dte, held_days)
        if reason:
            logger.info("Closing %s: %s", u, reason)
            actions.append(Close(u, include_stock=True, reason=reason))
        elif side == "cheap":
            actions.append(Hedge(u, 0.0, ww=(rules.hedge_cost_frac, rules.hedge_ww_risk_aversion)))
    return actions


# ------------------------------------------------------------------
# option_DispersionBot
# ------------------------------------------------------------------


def decide_dispersion(
    market: Market, holdings: Holdings, rules: DispersionRules, universe, index: str = "SPY"
) -> list[Action]:
    """Short an index iron fly against long member straddles while implied correlation is rich."""
    history = market.implied_correlation_history(index)
    past, current = (history.iloc[:-1].tolist(), float(history.iloc[-1])) if len(history) else ([], None)
    signal, why = dispersion_signal(past, current, rules)
    logger.info("Dispersion: %s", why)

    held = holdings.underlyings()
    if held:
        books = {u: holdings.book(u) for u in held}
        pnl = sum(b.pnl for b in books.values())
        stake = sum(abs(b.entry_value) for b in books.values())
        dte = min((b.dte for b in books.values() if b.dte is not None), default=None)
        reason = None
        if signal == "exit":
            reason = "implied correlation back to normal"
        elif dte is not None and dte <= rules.exit_dte:
            reason = f"{dte} DTE"
        elif stake > 0 and pnl >= rules.take_profit * stake:
            reason = f"take profit {pnl:.0f} on {stake:.0f} at stake"
        logger.info("Holding dispersion on %d underlyings: P&L %.0f, stake %.0f, %s DTE", len(held), pnl, stake, dte)
        if not reason:
            return []
        logger.info("Closing the dispersion book: %s", reason)
        return [Close(u, reason=reason) for u in sorted(held)]
    if signal != "enter":
        return []
    return _dispersion_entry(market, holdings, rules, universe, index)


def _dispersion_entry(market, holdings, rules, universe, index) -> list[Action]:
    weights = market.market_caps(universe)
    names = sorted((u for u in weights if weights[u] > 0), key=lambda u: -weights[u])[: rules.n_names]
    if not names:
        logger.warning("No market caps in stock_fundamentals: cannot weight the members")
        return []
    index_view = market.chain(index, rules.target_dte)
    if index_view is None or not index_view.live:
        logger.info("Chain not live; not opening off-hours")
        return []
    iv = options.atm_iv(index_view) or 0.0
    width = rules.wing_sigmas * iv * index_view.T**0.5 * index_view.spot
    fly = options.select_iron_butterfly(index_view, width)
    fly_m = options.pick_metrics(index_view, fly)

    straddles, metrics = {}, {}
    for u in names:
        try:
            view = market.chain(u, rules.target_dte)
            if view is None:
                raise ValueError("no chain")
            pick = options.select_straddle(view)
            straddles[u], metrics[u] = pick, options.pick_metrics(view, pick)
        except Exception as exc:
            logger.warning("%s: no straddle (%s)", u, exc)

    budget = rules.max_risk_pct * holdings.equity()
    best = None
    for n_fly in range(1, 50):
        lots = vega_weighted_lots(n_fly * fly_m.vega, {u: m.vega for u, m in metrics.items()}, weights)
        cost = n_fly * fly_m.max_loss + sum(lots[u] * metrics[u].price for u in lots)
        if cost > budget:
            break
        best = (n_fly, lots, cost)
    if best is None or not best[1]:
        logger.info("Budget %.0f too small for one fly plus vega-matched straddles", budget)
        return []
    n_fly, lots, cost = best
    net_vega = n_fly * fly_m.vega + sum(lots[u] * metrics[u].vega for u in lots)
    logger.info(
        "Opening dispersion: %d %s flies (vega %.0f), straddles %s, cost %.0f, net vega %+.0f",
        n_fly,
        index,
        n_fly * fly_m.vega,
        lots,
        cost,
        net_vega,
    )
    actions: list[Action] = [Open(fly, n_fly * fly_m.max_loss * 1.05, reason="index fly")]
    actions += [Open(straddles[u], n * metrics[u].price * 1.05, reason="member straddle") for u, n in lots.items()]
    return actions
