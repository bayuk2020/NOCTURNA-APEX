"""Moving candlestick replay window (Feature 1, path A / option 2).

Two layers, deliberately separated so the risky GUI part is thin:
  - ReplayFeed (TESTABLE, no GUI): turns the tick stream into RENDER FRAMES. Each
    frame carries the current forming candle of the display timeframe plus any bar
    that just closed. It batches ticks so we don't push millions of updates to the
    webview — we push at most one update per frame.
  - run_chart_replay (GUI glue, needs lightweight-charts + a screen): sets history,
    starts the feed on a background thread, and pushes frames to the chart. Handles
    play/pause + speed via the topbar.

Time: data is broker server time (UTC+3). `display_tz='WIB'` shifts +4h so the
chart's time axis shows the clock you actually use.

KNOWN: this GUI layer is UNTESTED in the build sandbox (no display). If a topbar
callback or update call misbehaves, that's the first place to look.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional

import pandas as pd

from .replay import TickDataset, ReplaySession, from_server_time

_TZ_SHIFT_H = {"SERVER": 0, "UTC": -3, "WIB": 4}  # add to server time to get display tz


# ---------------- testable helpers ----------------

def shift_index(df: pd.DataFrame, display_tz: str) -> pd.DataFrame:
    out = df.copy()
    out.index = out.index + pd.Timedelta(hours=_TZ_SHIFT_H[display_tz])
    return out


def format_for_lwc(df: pd.DataFrame, display_tz: str = "WIB") -> pd.DataFrame:
    """Shape an OHLC(V) frame for lightweight-charts chart.set()."""
    d = shift_index(df, display_tz).reset_index()
    d = d.rename(columns={d.columns[0]: "time"})
    d["time"] = d["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return d[["time", "open", "high", "low", "close"]]


def forming_series(bar: dict, display_tz: str = "WIB") -> pd.Series:
    """One-row series for chart.update() from a (forming or closed) bar dict."""
    t = pd.Timestamp(bar["time"]) + pd.Timedelta(hours=_TZ_SHIFT_H[display_tz])
    return pd.Series({"time": t.strftime("%Y-%m-%d %H:%M:%S"),
                      "open": bar["open"], "high": bar["high"],
                      "low": bar["low"], "close": bar["close"]})


@dataclass
class ReplayFeed:
    """Batches the tick stream into render frames for ONE display timeframe.

    frame = {'bar': <current forming bar dict>, 'closed': <bar dict or None>,
             'time': server ts of last tick, 'done': bool}
    `ticks_per_frame` controls speed: more ticks per frame = faster replay.
    """
    session: ReplaySession
    display_tf: str
    ticks_per_frame: int = 200

    def frames(self) -> Iterator[dict]:
        b = self.session.builders[self.display_tf]
        n = 0
        last_closed = None
        last_ts = None
        for ev in self.session.step():
            last_ts = ev["time"]
            if self.display_tf in ev["closed"]:
                last_closed = ev["closed"][self.display_tf]
            n += 1
            if n >= self.ticks_per_frame:
                n = 0
                yield {"bar": dict(b.current) if b.current else None,
                       "closed": last_closed, "time": last_ts, "done": False}
                last_closed = None
        # final frame
        yield {"bar": dict(b.current) if b.current else None,
               "closed": last_closed, "time": last_ts, "done": True}


# ---------------- GUI glue (needs lightweight-charts + a display) ----------------

def run_chart_replay(parquet_dir: str = "market_data", symbol: str = "XAUUSD",
                     start: str = "2026-07-01 08:00", display_tf: str = "M1",
                     input_tz: str = "WIB", display_tz: str = "WIB",
                     ticks_per_frame: int = 300, fps: int = 30):
    import time
    from threading import Thread, Event
    from lightweight_charts import Chart

    ds = TickDataset.load(parquet_dir, symbol=symbol)
    lo, hi = ds.span("WIB")
    print(f"[replay] data {lo} .. {hi} (WIB). Start = {start} {input_tz}, tf={display_tf}")

    session = ReplaySession(ds, start=start, timeframes=[display_tf], input_tz=input_tz)
    hist = session.history[display_tf]
    if hist.empty:
        raise RuntimeError("No history before start datetime — pick a later start.")

    chart = Chart(toolbox=True, title=f"{symbol} REPLAY | {display_tf}")
    chart.set(format_for_lwc(hist, display_tz))
    chart.fit()

    state = {"paused": False, "speed": ticks_per_frame}

    def on_play_pause(chart):
        state["paused"] = not state["paused"]
        chart.topbar["pp"].set("Play" if state["paused"] else "Pause")

    def on_speed(chart):
        mapping = {"0.5x": 60, "1x": 150, "5x": 600, "20x": 2000, "max": 20000}
        state["speed"] = mapping.get(chart.topbar["speed"].value, 300)

    chart.topbar.button("pp", "Pause", func=on_play_pause)
    chart.topbar.switcher("speed", ("0.5x", "1x", "5x", "20x", "max"),
                          default="1x", func=on_speed)
    state["speed"] = 150  # matches default '1x'

    stop = Event()

    def feed():
        time.sleep(1.0)  # let the webview finish loading before first update
        feed = ReplayFeed(session, display_tf, ticks_per_frame=state["speed"])
        gen = feed.frames()
        frame_interval = 1.0 / max(fps, 1)
        while not stop.is_set():
            if state["paused"]:
                time.sleep(0.05)
                continue
            feed.ticks_per_frame = state["speed"]  # live speed changes
            try:
                fr = next(gen)
            except StopIteration:
                print("[replay] reached end of data")
                break
            if fr["bar"]:
                try:
                    chart.update(forming_series(fr["bar"], display_tz))
                except Exception as e:
                    print("[replay] update error:", e)
            time.sleep(frame_interval)

    Thread(target=feed, daemon=True).start()
    chart.show(block=True)
    stop.set()


if __name__ == "__main__":
    run_chart_replay()