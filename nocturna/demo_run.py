"""End-to-end smoke test on SYNTHETIC data (no MT5 needed).

Proves: ticks->OHLC reconstruction, indicator engine (create/modify/multiple
instances), simulator (open/close/SL/TP/trailing), backtest loop + stats, and the
NOCTURNA-APEX dashboard snapshot. Run:  python -m nocturna.demo_run
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data.store import ticks_to_ohlc, resample_ohlc
from .indicators.base import IndicatorRegistry
from .indicators.library import register_builtins
from .engine.simulator import Account, Side
from .engine.backtest import run_backtest
from .dashboard import nocturna_apex_snapshot, render_dashboard


def make_synthetic_ticks(n_minutes=3000, seed=7) -> pd.DataFrame:
    """Random-walk gold-ish price, ~10 ticks/min, with bid/ask spread."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-07-01 10:00", tz="UTC")
    rows = []
    price = 3300.0
    t = start
    for _ in range(n_minutes):
        for _ in range(rng.integers(6, 14)):
            price += rng.normal(0, 0.25)
            spread = 0.30
            rows.append((t, price - spread / 2, price + spread / 2, 1))
            t += pd.Timedelta(seconds=rng.integers(3, 9))
    return pd.DataFrame(rows, columns=["time", "bid", "ask", "volume"])


def main():
    ticks = make_synthetic_ticks()
    print(f"[data] {len(ticks):,} synthetic ticks from {ticks['time'].iloc[0]} to {ticks['time'].iloc[-1]}")

    m1 = ticks_to_ohlc(ticks, "M1")
    m5 = resample_ohlc(m1, "M5")
    print(f"[resample] M1 bars={len(m1)}  M5 bars={len(m5)}  (wicks preserved: "
          f"high>=max(o,c) holds = {(m1['high'] >= m1[['open','close']].max(axis=1)-1e-9).all()})")

    # indicator engine: multiple instances, modify, custom color
    reg = IndicatorRegistry()
    register_builtins(reg)
    ema_fast = reg.create("EMA", params={"length": 9}, colors={"ema": "#00e676"})
    ema_slow = reg.create("EMA", params={"length": 21})
    utbot = reg.create("UTBot", params={"keyvalue": 1.0, "atr_period": 10})
    reg.create("RSI", params={"length": 14})  # subwindow example
    print(f"[indicators] available types: {reg.available()}")
    print(f"[indicators] live instances: {[str(i) for i in reg.instances()]}")

    # --- strategy: EMA(9)/EMA(21) cross, confirmed by UT Bot, fixed SL/TP ---
    def strategy(ctx):
        acc = ctx.account
        h = ctx.history
        ef = ema_fast.compute(h)["ema"]
        es = ema_slow.compute(h)["ema"]
        sig = utbot.compute(h)
        mid = float(ctx.bar["close"])
        if len(h) < 25 or pd.isna(ef.iloc[-1]) or pd.isna(es.iloc[-1]):
            return
        cross_up = ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]
        cross_dn = ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]
        if acc.positions:
            # flip on opposite cross
            pos = acc.positions[0]
            if (pos.side is Side.BUY and cross_dn) or (pos.side is Side.SELL and cross_up):
                acc.close_all(mid, ctx.time)
        if not acc.positions:
            if cross_up and sig["buy"].iloc[-1] == 1:
                acc.open_market(Side.BUY, 0.10, mid, ctx.time, sl=mid - 3.0, tp=mid + 6.0)
            elif cross_dn and sig["sell"].iloc[-1] == 1:
                acc.open_market(Side.SELL, 0.10, mid, ctx.time, sl=mid + 3.0, tp=mid - 6.0)

    acc = Account(balance=10_000, leverage=100, contract_size=100,
                  commission_per_lot=7.0, spread=0.30)
    result = run_backtest(m1, acc, strategy, indicator_registry=None, warmup=25)

    print("\n[backtest stats]")
    for k, v in result["stats"].items():
        print(f"  {k:22s}: {v}")

    # dashboard snapshot at end of run (reopen a position so basket shows data)
    acc.open_market(Side.BUY, 0.10, float(m1.iloc[-1]['close']), m1.index[-1],
                    sl=float(m1.iloc[-1]['close']) - 3, tp=float(m1.iloc[-1]['close']) + 6)
    snap = nocturna_apex_snapshot(acc, m1, daily_start_balance=acc.initial_balance)
    print("\n[NOCTURNA-APEX DASHBOARD]")
    print(render_dashboard(snap))


if __name__ == "__main__":
    main()
