"""Automated strategy runner over the per-second replay engine (Feature 1, path B).

Why this exists: run a Python strategy (UT Bot, EMA cross, anything you write)
across historical XAUUSD, entering/exiting automatically, and get HONEST stats
(expectancy, profit factor, max drawdown — not just winrate).

Key correctness choice [tick-level execution]:
  SL/TP are checked on EVERY TICK, not on bar close. With 29M ticks this removes
  the classic backtest lie where intrabar SL/TP hits are missed and winrate looks
  better than reality. Strategy DECISIONS happen on bar close (you act on closed
  candles); order MANAGEMENT (SL/TP/trailing) happens on ticks.

Flow per tick:
  1. account.update(price, price, price, t)  -> tick-level SL/TP/trailing
  2. if this tick closed a strategy-timeframe bar:
        build recent history (seeded with pre-replay history for warmup)
        call strategy(ctx); ctx.account lets it open/close at ctx.price

Cost: pure-Python loop over ticks. Full 3 months ≈ minutes. Use `end=` to run a
short window while developing a strategy, then widen for the final measurement.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import pandas as pd

from .simulator import Account, Side
from .backtest import performance_stats
from ..replay import TickDataset, ReplaySession, to_server_time


@dataclass
class BarContext:
    time: pd.Timestamp          # close time of the bar that just finished (server time)
    price: float                # current tick price (fill reference for market orders)
    bid: float
    ask: float
    bar: dict                   # the bar that just closed
    history: pd.DataFrame       # recent closed bars incl. warmup (index=server time)
    account: Account


Strategy = Callable[[BarContext], None]


class StrategyRunner:
    def __init__(self, dataset: TickDataset, account: Account, strategy: Strategy,
                 start, end=None, strategy_tf: str = "M5",
                 aux_timeframes: Sequence[str] = (), input_tz: str = "WIB",
                 lookback: int = 500):
        self.ds = dataset
        self.account = account
        self.strategy = strategy
        self.start = start
        self.end_server = to_server_time(end, input_tz) if end else None
        self.strategy_tf = strategy_tf
        self.input_tz = input_tz
        self.lookback = lookback
        tfs = [strategy_tf] + [t for t in aux_timeframes if t != strategy_tf]
        self.session = ReplaySession(dataset, start=start, timeframes=tfs, input_tz=input_tz)

        # seed history with pre-replay bars (warmup) so indicators are valid on bar 1
        seed = self.session.history[strategy_tf]
        self._hist = deque(maxlen=lookback)
        for t, row in seed.tail(lookback).iterrows():
            self._hist.append({"time": t, "open": row["open"], "high": row["high"],
                               "low": row["low"], "close": row["close"],
                               "volume": row.get("volume", 0)})
        self.ticks_processed = 0
        self.bars_processed = 0

    def _history_df(self) -> pd.DataFrame:
        df = pd.DataFrame(list(self._hist))
        return df.set_index("time")

    def run(self, progress_every: int = 0) -> dict:
        acc = self.account
        for ev in self.session.step():
            t = ev["time"]
            if self.end_server is not None and t > self.end_server:
                break
            self.ticks_processed += 1
            px = ev["price"]

            # 1) tick-level order management (SL/TP/trailing) — high=low=close=tick px
            acc.update(px, px, px, t)

            # 2) strategy decision on bar close
            closed = ev["closed"].get(self.strategy_tf)
            if closed is not None:
                self._hist.append(closed)
                self.bars_processed += 1
                if len(self._hist) >= 2:
                    ctx = BarContext(time=closed["time"], price=px,
                                     bid=ev["bid"], ask=ev["ask"], bar=closed,
                                     history=self._history_df(), account=acc)
                    self.strategy(ctx)

            if progress_every and self.ticks_processed % progress_every == 0:
                eq = acc.equity(px)
                print(f"  ...{self.ticks_processed:>10,} ticks | {t} | "
                      f"bars {self.bars_processed} | equity {eq:.2f} | "
                      f"open {len(acc.positions)} | closed {len(acc.history)}")

        # liquidate at final tick price
        if self.ticks_processed:
            acc.close_all(px, t)
        stats = performance_stats(acc)
        stats["ticks_processed"] = self.ticks_processed
        stats["bars_processed"] = self.bars_processed
        return {"account": acc, "stats": stats}


# ---------------- example strategies (write your own the same way) ----------------

def ema_cross_utbot(ctx: BarContext, *, fast=9, slow=21, sl=3.0, tp=6.0, lots=0.10):
    """EMA(fast)/EMA(slow) cross confirmed by UT Bot direction. Fixed SL/TP in $."""
    from ..indicators.library import EMA, UTBot
    h = ctx.history
    if len(h) < max(slow, 25):
        return
    ef = h["close"].ewm(span=fast, adjust=False).mean()
    es = h["close"].ewm(span=slow, adjust=False).mean()
    ut = UTBot(params={"keyvalue": 1.0, "atr_period": 10}).compute(h)
    up = ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]
    dn = ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]
    acc, px = ctx.account, ctx.price
    if acc.positions:
        p = acc.positions[0]
        if (p.side is Side.BUY and dn) or (p.side is Side.SELL and up):
            acc.close_all(px, ctx.time)
    if not acc.positions:
        if up and ut["buy"].iloc[-1] == 1:
            acc.open_market(Side.BUY, lots, px, ctx.time, sl=px - sl, tp=px + tp)
        elif dn and ut["sell"].iloc[-1] == 1:
            acc.open_market(Side.SELL, lots, px, ctx.time, sl=px + sl, tp=px - tp)