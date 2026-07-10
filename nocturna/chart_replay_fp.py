"""Moving candlestick replay using finplot (native Qt, no webview/threads).

Why finplot: it renders on the main Qt event loop and updates candles in place via
`plot.update_data()`, driven by `fplt.timer_callback()`. No background thread, no
multiprocessing — which is exactly what broke the webview backend.

Layers:
  - apply_frame(df, frame, display_tz): TESTABLE. Upserts the just-closed bar and
    the current forming bar into the working DataFrame (index = display-tz time).
    Upsert handles both "grow the current candle" and "start a new candle".
  - run_replay_fp(...): GUI. Sets history candles, then a Qt timer pulls frames from
    the (already tested) ReplayFeed and refreshes the plot.

Time: data is server (UTC+3); display_tz='WIB' shifts +4h for the axis.
Controls: SPACE = pause/resume, Up/Down = faster/slower. Auto-plays on launch.
"""
from __future__ import annotations

import pandas as pd

from .replay import TickDataset, ReplaySession
from .chart_replay import ReplayFeed   # reuse the tested feed

_TZ_SHIFT_H = {"SERVER": 0, "UTC": -3, "WIB": 4}


def _bar_to_row(bar: dict, display_tz: str):
    t = pd.Timestamp(bar["time"]) + pd.Timedelta(hours=_TZ_SHIFT_H[display_tz])
    return t, {"open": bar["open"], "close": bar["close"],
               "high": bar["high"], "low": bar["low"]}


def apply_frame(df: pd.DataFrame, frame: dict, display_tz: str = "WIB") -> pd.DataFrame:
    """Upsert closed + forming bars from a replay frame into df (index=time)."""
    for key in ("closed", "bar"):
        bar = frame.get(key)
        if not bar:
            continue
        t, row = _bar_to_row(bar, display_tz)
        df.loc[t, ["open", "close", "high", "low"]] = [row["open"], row["close"],
                                                       row["high"], row["low"]]
    return df


def history_df(hist: pd.DataFrame, display_tz: str = "WIB") -> pd.DataFrame:
    d = hist[["open", "close", "high", "low"]].copy()
    d.index = d.index + pd.Timedelta(hours=_TZ_SHIFT_H[display_tz])
    return d


def run_replay_fp(parquet_dir: str = "market_data", symbol: str = "XAUUSD",
                  start: str = "2026-07-01 08:00", display_tf: str = "M1",
                  input_tz: str = "WIB", display_tz: str = "WIB",
                  ticks_per_frame: int = 200, interval: float = 0.05,
                  history_bars: int = 400):
    import finplot as fplt

    ds = TickDataset.load(parquet_dir, symbol=symbol)
    lo, hi = ds.span("WIB")
    print(f"[replay] {lo} .. {hi} WIB | start={start} {input_tz} | tf={display_tf}")

    session = ReplaySession(ds, start=start, timeframes=[display_tf], input_tz=input_tz)
    hist = session.history[display_tf]
    if hist.empty:
        raise RuntimeError("No history before start — pick a later start datetime.")

    df = history_df(hist.tail(history_bars), display_tz)

    ax = fplt.create_plot(f"{symbol} REPLAY {display_tf} (WIB)  [SPACE pause | Up/Down speed]",
                          init_zoom_periods=150, maximize=False)
    candles = fplt.candlestick_ochl(df[["open", "close", "high", "low"]], ax=ax)

    state = {"paused": False, "tpf": ticks_per_frame}
    feed = ReplayFeed(session, display_tf, ticks_per_frame=state["tpf"])
    gen = feed.frames()

    def tick():
        if state["paused"]:
            return
        feed.ticks_per_frame = state["tpf"]
        try:
            frame = next(gen)
        except StopIteration:
            print("[replay] end of data")
            return
        apply_frame(df, frame, display_tz)
        candles.update_data(df[["open", "close", "high", "low"]])

    # keyboard controls via the pyqtgraph view
    def on_key(ev):
        from pyqtgraph.Qt import QtCore
        Key = getattr(QtCore.Qt, "Key", QtCore.Qt)  # PyQt6: Qt.Key.*  | PyQt5: Qt.*
        k = ev.key()
        if k == Key.Key_Space:
            state["paused"] = not state["paused"]
            print("[replay]", "PAUSED" if state["paused"] else "PLAY")
        elif k == Key.Key_Up:
            state["tpf"] = min(state["tpf"] * 2, 20000)
            print("[replay] speed:", state["tpf"], "ticks/frame")
        elif k == Key.Key_Down:
            state["tpf"] = max(state["tpf"] // 2, 10)
            print("[replay] speed:", state["tpf"], "ticks/frame")

    try:
        ax.vb.keyPressEvent = on_key  # attach to the view box
    except Exception as e:
        print("[replay] key controls unavailable:", e)

    fplt.timer_callback(tick, interval)   # Qt main-loop timer, no threads
    fplt.show()


if __name__ == "__main__":
    run_replay_fp()