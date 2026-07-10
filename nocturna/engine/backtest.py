"""Bar-driven backtest / replay engine.

Feeds bars one at a time to a strategy callback. Everything the strategy needs is
on the `Context`: current bar, full history up to now, the account/simulator, and
precomputed indicator instances. This is the same loop that powers the "replay
from a chosen datetime" feature — you just choose where the feed starts.

Metrics reported go beyond winrate (winrate alone is misleading): expectancy,
profit factor, max drawdown, and average win/loss. Those decide account survival.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .simulator import Account, OrderType, Side


@dataclass
class Context:
    i: int                       # index of current bar
    time: object
    bar: pd.Series               # current OHLCV row
    history: pd.DataFrame        # bars[0..i] inclusive (no lookahead)
    account: Account
    indicators: dict             # instance_id -> {key: Series aligned to history}
    equity_curve: list


# strategy signature: fn(ctx: Context) -> None  (calls ctx.account.open_market/close/...)
Strategy = Callable[[Context], None]


def run_backtest(bars: pd.DataFrame, account: Account, strategy: Strategy,
                 indicator_registry=None, warmup: int = 50,
                 swap_hour_utc: int = 22) -> dict:
    """Run `strategy` over `bars`. Returns account + stats + equity curve.

    - Indicators are computed *incrementally per bar on history-so-far* to prevent
      lookahead. (Slower but correct; optimize later with rolling state.)
    - Swap is charged once per bar that crosses `swap_hour_utc`.
    """
    bars = bars.copy()
    if not isinstance(bars.index, pd.DatetimeIndex):
        bars.index = pd.to_datetime(bars.index, utc=True)

    equity_curve = []
    prev_day = None

    for i in range(len(bars)):
        row = bars.iloc[i]
        t = bars.index[i]
        mid = float(row["close"])

        # apply swap once per day boundary at swap hour (simplified)
        if prev_day is not None and t.date() != prev_day and t.hour >= swap_hour_utc:
            for p in account.positions:
                p.swap += account.swap_per_lot * p.lots
        prev_day = t.date()

        # SL/TP/trailing maintenance against this bar's range
        account.update(float(row["high"]), float(row["low"]), mid, t)

        if i >= warmup:
            history = bars.iloc[: i + 1]
            inds = {}
            if indicator_registry is not None:
                for inst in indicator_registry.instances(only_enabled=True):
                    inds[inst.instance_id] = inst.compute(history)
            ctx = Context(i=i, time=t, bar=row, history=history,
                          account=account, indicators=inds, equity_curve=equity_curve)
            strategy(ctx)

        equity_curve.append((t, account.equity(mid)))

    # liquidate remaining at last close
    if len(bars):
        last_t = bars.index[-1]
        account.close_all(float(bars.iloc[-1]["close"]), last_t)

    return {
        "account": account,
        "equity_curve": pd.Series(dict(equity_curve)),
        "stats": performance_stats(account),
    }


def performance_stats(account: Account) -> dict:
    trades = account.history
    if not trades:
        return {"trades": 0, "note": "no closed trades"}
    profits = np.array([t.profit for t in trades])
    wins = profits[profits > 0]
    losses = profits[profits < 0]
    gross_profit = wins.sum()
    gross_loss = -losses.sum()
    net = profits.sum()
    winrate = len(wins) / len(profits)
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    expectancy = profits.mean()  # avg $ per trade — the number that matters
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    return {
        "trades": len(profits),
        "winrate": round(winrate, 4),
        "net_profit": round(net, 2),
        "return_pct": round(net / account.initial_balance * 100, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "profit_factor": round(profit_factor, 3) if np.isfinite(profit_factor) else np.inf,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown_pct": round(account.max_drawdown * 100, 2),
        "final_balance": round(account.balance, 2),
    }


# ---------------- pending order matching helper ----------------
def match_pending(order_type: OrderType, price: float, bar_high: float, bar_low: float) -> bool:
    """Would a pending order at `price` trigger within this bar's range?"""
    if order_type in (OrderType.BUY_STOP, OrderType.SELL_LIMIT):
        return bar_high >= price if order_type is OrderType.BUY_STOP else bar_high >= price
    if order_type in (OrderType.SELL_STOP, OrderType.BUY_LIMIT):
        return bar_low <= price
    return False
