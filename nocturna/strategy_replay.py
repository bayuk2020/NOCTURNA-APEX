"""Run a strategy DURING the visual replay and collect entry/exit markers.

One tick stream drives everything (no duplicate iteration):
  - candle display frame (for the chart)
  - tick-level SL/TP via the simulator
  - strategy decisions on each closed strategy-timeframe bar
  - markers: buy/sell entries and exits, detected by diffing the account each frame
    so SL/TP exits (which happen mid-frame on ticks) are captured too.

Core (StrategyReplay) is GUI-free and testable. The finplot GUI is thin glue.

Times are server (UTC+3); markers/candles are shifted to display_tz for drawing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from .replay import TickDataset, ReplaySession
from .engine.simulator import Account, Side
from .engine.strategy_runner import BarContext

_TZ_SHIFT_H = {"SERVER": 0, "UTC": -3, "WIB": 4}


@dataclass
class StrategyReplay:
    dataset: TickDataset
    account: Account
    strategy: Callable[[BarContext], None]
    start: str
    display_tf: str = "M1"
    input_tz: str = "WIB"
    display_tz: str = "WIB"
    ticks_per_frame: int = 200
    lookback: int = 400
    history_bars: int = 400

    def __post_init__(self):
        self.session = ReplaySession(self.dataset, start=self.start,
                                     timeframes=[self.display_tf], input_tz=self.input_tz)
        hist = self.session.history[self.display_tf]
        shift = pd.Timedelta(hours=_TZ_SHIFT_H[self.display_tz])
        # display candle df (WIB index)
        d = hist.tail(self.history_bars)[["open", "close", "high", "low"]].copy()
        d.index = d.index + shift
        self.df = d
        # strategy history (server time), seeded for indicator warmup
        self._hist = deque(maxlen=self.lookback)
        for t, row in hist.tail(self.lookback).iterrows():
            self._hist.append({"time": t, "open": row["open"], "high": row["high"],
                               "low": row["low"], "close": row["close"],
                               "volume": row.get("volume", 0)})
        # marker stores as lists of (display_time, price) so same-timestamp
        # markers never collide/overwrite
        self.buys: list = []
        self.sells: list = []
        self.exits: list = []
        self._seen_tickets: set = set()   # tickets whose ENTRY marker is recorded
        self._hist_len = 0
        self._shift = shift
        # current server-time tick cursor — used by the GUI for manual orders and
        # risk actions so open/close use the live price + a server-time stamp
        # (markers shift +display_tz; passing a shifted time would double-shift).
        self.cur_time = hist.index[-1] if len(hist) else None
        self.cur_price = float(hist["close"].iloc[-1]) if len(hist) else None
        # when True the auto-strategy is skipped (risk halt) — indicators still warm
        self.trading_halted = False

    def _history_df(self) -> pd.DataFrame:
        return pd.DataFrame(list(self._hist)).set_index("time")

    def _record_entry(self, ticket, side, open_time, open_price):
        if ticket in self._seen_tickets:
            return
        self._seen_tickets.add(ticket)
        t = pd.Timestamp(open_time) + self._shift
        (self.buys if side is Side.BUY else self.sells).append((t, open_price))

    def _collect_markers(self):
        acc = self.account
        # entries from still-open positions
        for p in acc.positions:
            self._record_entry(p.ticket, p.side, p.open_time, p.open_price)
        # new exits — AND recover entries for trades that opened+closed inside one
        # frame (never seen as an open position)
        if len(acc.history) > self._hist_len:
            for tr in acc.history[self._hist_len:]:
                self._record_entry(tr.ticket, tr.side, tr.open_time, tr.open_price)
                t = pd.Timestamp(tr.close_time) + self._shift
                self.exits.append((t, tr.close_price))
            self._hist_len = len(acc.history)

    @staticmethod
    def _to_series(pairs):
        if not pairs:
            return None
        s = pd.Series({t: p for t, p in pairs})
        # if duplicate timestamps exist, keep them via a non-dedup construction
        if len(s) != len(pairs):
            idx = [t for t, _ in pairs]
            val = [p for _, p in pairs]
            s = pd.Series(val, index=idx)
        return s.sort_index()

    def frames(self):
        """Yield render frames: {'df','buys','sells','exits','stats','done'}."""
        b = self.session.builders[self.display_tf]
        acc = self.account
        n = 0
        last_px = None
        last_t = None
        for ev in self.session.step():
            last_t = ev["time"]
            px = ev["price"]
            last_px = px
            self.cur_time = last_t
            self.cur_price = px
            # tick-level SL/TP/trailing
            acc.update(px, px, px, last_t)
            # strategy on bar close
            closed = ev["closed"].get(self.display_tf)
            if closed is not None:
                self._hist.append(closed)
                # keep history warm always, but a risk halt blocks the strategy
                # from opening/closing anything (no death-loop after a stop-out)
                if len(self._hist) >= 2 and not self.trading_halted:
                    ctx = BarContext(time=closed["time"], price=px, bid=ev["bid"],
                                     ask=ev["ask"], bar=closed,
                                     history=self._history_df(), account=acc)
                    self.strategy(ctx)
            n += 1
            if n >= self.ticks_per_frame:
                n = 0
                # upsert forming bar into display df
                if b.current:
                    t = pd.Timestamp(b.current["time"]) + self._shift
                    self.df.loc[t, ["open", "close", "high", "low"]] = [
                        b.current["open"], b.current["close"],
                        b.current["high"], b.current["low"]]
                self._collect_markers()
                yield self._frame(px, last_t, done=False)
        self._collect_markers()
        yield self._frame(last_px, last_t, done=True)

    def _frame(self, px, t, done):
        acc = self.account
        eq = acc.equity(px) if px else acc.balance
        return {
            "df": self.df,
            "buys": self._to_series(self.buys),
            "sells": self._to_series(self.sells),
            "exits": self._to_series(self.exits),
            "stats": {"equity": round(eq, 2), "balance": round(acc.balance, 2),
                      "open": len(acc.positions), "closed": len(acc.history),
                      "floating": round(acc.floating_pnl(px), 2) if px else 0.0},
            "done": done,
        }


# ---------------- finplot GUI ----------------

def run_strategy_replay_fp(strategy, parquet_dir="market_data", symbol="XAUUSD",
                           start="2026-07-01 08:00", display_tf="M1",
                           input_tz="WIB", display_tz="WIB", ticks_per_frame=200,
                           interval=0.05, history_bars=400,
                           balance=10_000, leverage=1000, spread=0.18,
                           commission_per_lot=0.0):
    import finplot as fplt

    ds = TickDataset.load(parquet_dir, symbol=symbol)
    lo, hi = ds.span("WIB")
    print(f"[replay] {lo} .. {hi} WIB | start={start} {input_tz} | tf={display_tf}")

    acc = Account(balance=balance, leverage=leverage, contract_size=100,
                  commission_per_lot=commission_per_lot, spread=spread)
    sr = StrategyReplay(ds, acc, strategy, start=start, display_tf=display_tf,
                        input_tz=input_tz, display_tz=display_tz,
                        ticks_per_frame=ticks_per_frame, history_bars=history_bars)

    ax = fplt.create_plot(f"{symbol} STRATEGY REPLAY {display_tf} (WIB)  "
                          f"[SPACE pause | Up/Down speed]", init_zoom_periods=150)
    candles = fplt.candlestick_ochl(sr.df[["open", "close", "high", "low"]], ax=ax)

    handles = {"buy": None, "sell": None, "exit": None}
    state = {"paused": False, "tpf": ticks_per_frame}
    gen = sr.frames()

    def draw_markers(fr):
        idx = sr.df.index
        specs = [("buy", fr["buys"], "#26a69a", "^"),
                 ("sell", fr["sells"], "#ef5350", "v"),
                 ("exit", fr["exits"], "#b0b0b0", "x")]
        for name, series, color, style in specs:
            if series is None or len(series) == 0:
                continue
            # snap each marker time to the nearest candle bar, align to candle index
            aligned = pd.Series(index=idx, dtype="float64")
            for t, price in series.items():
                pos = idx.get_indexer([t], method="nearest")[0]
                if pos >= 0:
                    aligned.iloc[pos] = price
            if aligned.notna().sum() == 0:
                continue
            if handles[name] is None:
                handles[name] = fplt.plot(aligned, ax=ax, color=color,
                                          style=style, width=2, legend=name)
            else:
                handles[name].update_data(aligned)

    def tick():
        if state["paused"]:
            return
        sr.ticks_per_frame = state["tpf"]
        try:
            fr = next(gen)
        except StopIteration:
            return
        candles.update_data(sr.df[["open", "close", "high", "low"]])
        draw_markers(fr)
        s = fr["stats"]
        ax.setTitle(f"{symbol} {display_tf} | equity {s['equity']} | "
                    f"float {s['floating']} | trades {s['closed']} | open {s['open']}")

    def on_key(ev):
        from pyqtgraph.Qt import QtCore
        Key = getattr(QtCore.Qt, "Key", QtCore.Qt)
        k = ev.key()
        if k == Key.Key_Space:
            state["paused"] = not state["paused"]
            print("[replay]", "PAUSED" if state["paused"] else "PLAY")
        elif k == Key.Key_Up:
            state["tpf"] = min(state["tpf"] * 2, 20000)
            print("[replay] speed:", state["tpf"])
        elif k == Key.Key_Down:
            state["tpf"] = max(state["tpf"] // 2, 10)
            print("[replay] speed:", state["tpf"])

    try:
        ax.vb.keyPressEvent = on_key
    except Exception as e:
        print("[replay] key controls unavailable:", e)

    fplt.timer_callback(tick, interval)
    fplt.show()


# example strategy for direct launch
def _demo_strategy(ctx, fast=9, slow=21, sl=3.0, tp=6.0, lots=0.10):
    h = ctx.history
    if len(h) < slow + 2:
        return
    ef = h["close"].ewm(span=fast, adjust=False).mean()
    es = h["close"].ewm(span=slow, adjust=False).mean()
    up = ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]
    dn = ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]
    acc, px = ctx.account, ctx.price
    if acc.positions:
        p = acc.positions[0]
        if (p.side is Side.BUY and dn) or (p.side is Side.SELL and up):
            acc.close_all(px, ctx.time)
    if not acc.positions:
        if up:
            acc.open_market(Side.BUY, lots, px, ctx.time, sl=px - sl, tp=px + tp)
        elif dn:
            acc.open_market(Side.SELL, lots, px, ctx.time, sl=px + sl, tp=px - tp)


if __name__ == "__main__":
    run_strategy_replay_fp(_demo_strategy)