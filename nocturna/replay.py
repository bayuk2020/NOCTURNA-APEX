"""Per-second replay engine — the core of Feature 1.

Concept (as specified by the user):
  Pick a datetime T. Show history UP TO T (candles built from ticks). Then press
  play: ticks after T are fed forward one at a time, and M1/M5/... candles form
  LIVE from those ticks — "rewind the gold tape and replay it".

Time handling (IMPORTANT — see analysis in project history):
  ticks.parquet timestamps are BROKER SERVER time (UTC+3), stored naive.
  The user thinks in WIB (UTC+7). So when the user asks for "1 July 10:00 WIB",
  we convert to server time before slicing the tick array.
      server_time = wib_time - 4h      (UTC+7 -> UTC+3)
  `input_tz` lets you switch this: 'WIB' (default), 'UTC', or 'SERVER'.

Design:
  - Ticks are the single source of truth. All candles are reconstructed, so we
    never depend on the broker's M1 (which came back empty for long ranges).
  - LiveCandleBuilder keeps a running (open,high,low,close,volume) per timeframe
    and closes/opens bars as tick timestamps cross bar boundaries. O(1) per tick.
  - Feeds integrate with the existing simulator/indicator/dashboard stack: each
    time a bar closes, you can run indicators + strategy on the updated frame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
import pandas as pd

# server(UTC+3) offset relative to other zones, in hours
_TZ_TO_SERVER_SHIFT = {"SERVER": 0, "UTC": 3, "WIB": 4}  # subtract this to get server time

# timeframe -> seconds
_TF_SECONDS = {
    "1S": 1, "5S": 5, "15S": 15, "30S": 30,
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


def to_server_time(ts, input_tz: str = "WIB") -> pd.Timestamp:
    """Convert a user-supplied naive datetime in `input_tz` to broker server time."""
    if input_tz not in _TZ_TO_SERVER_SHIFT:
        raise ValueError(f"input_tz must be one of {list(_TZ_TO_SERVER_SHIFT)}")
    ts = pd.Timestamp(ts)
    return ts - pd.Timedelta(hours=_TZ_TO_SERVER_SHIFT[input_tz])


def from_server_time(ts, output_tz: str = "WIB") -> pd.Timestamp:
    return pd.Timestamp(ts) + pd.Timedelta(hours=_TZ_TO_SERVER_SHIFT[output_tz])


@dataclass
class TickDataset:
    """Holds the full tick array (server time) + lazy per-timeframe caches."""
    ticks: pd.DataFrame                      # columns: time(server), bid, ask, volume
    _ohlc_cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls, parquet_dir: str | Path, symbol: str = "XAUUSD",
             price: str = "mid") -> "TickDataset":
        p = Path(parquet_dir) / symbol / "ticks.parquet"
        t = pd.read_parquet(p)
        t["time"] = pd.to_datetime(t["time"])
        t = t.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
        # precompute chosen price series once
        if price == "mid":
            t["price"] = (t["bid"] + t["ask"]) / 2.0
        else:
            t["price"] = t[price]
        return cls(ticks=t)

    def span(self, output_tz: str = "WIB"):
        lo, hi = self.ticks["time"].iloc[0], self.ticks["time"].iloc[-1]
        return from_server_time(lo, output_tz), from_server_time(hi, output_tz)

    def history_ohlc(self, until, timeframe: str, input_tz: str = "WIB") -> pd.DataFrame:
        """OHLC built from all ticks with time <= `until` (history before replay)."""
        cut = to_server_time(until, input_tz)
        sub = self.ticks[self.ticks["time"] <= cut]
        return _build_ohlc(sub, timeframe)

    def replay(self, start, input_tz: str = "WIB") -> Iterator[pd.Series]:
        """Yield ticks (server time) with time > start, one at a time."""
        cut = to_server_time(start, input_tz)
        sub = self.ticks[self.ticks["time"] > cut]
        for row in sub.itertuples(index=False):
            yield row  # row.time, row.bid, row.ask, row.volume, row.price


def _build_ohlc(ticks: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if ticks.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    sec = _TF_SECONDS[timeframe]
    rule = f"{sec}s"
    g = ticks.set_index("time")
    ohlc = g["price"].resample(rule, label="left", closed="left").ohlc().dropna(subset=["open"])
    ohlc["volume"] = g["price"].resample(rule, label="left", closed="left").count().reindex(ohlc.index).fillna(0)
    return ohlc


class LiveCandleBuilder:
    """Streams ticks into a live candle for ONE timeframe. O(1) per tick.

    Call `update(ts, price)` for each tick. Returns a *closed* bar (dict) at the
    moment a new period starts, else None. `current` holds the forming bar.
    """
    def __init__(self, timeframe: str):
        self.sec = _TF_SECONDS[timeframe]
        self.timeframe = timeframe
        self.bucket: Optional[pd.Timestamp] = None
        self.current: Optional[dict] = None

    def _floor(self, ts: pd.Timestamp) -> pd.Timestamp:
        epoch = int(ts.timestamp())
        return pd.Timestamp((epoch // self.sec) * self.sec, unit="s")

    def update(self, ts: pd.Timestamp, price: float) -> Optional[dict]:
        b = self._floor(ts)
        closed = None
        if self.bucket is None:
            self.bucket = b
            self.current = {"time": b, "open": price, "high": price,
                            "low": price, "close": price, "volume": 1}
        elif b == self.bucket:
            c = self.current
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
            c["volume"] += 1
        else:  # new period -> emit the finished bar, start a fresh one
            closed = dict(self.current)
            self.bucket = b
            self.current = {"time": b, "open": price, "high": price,
                            "low": price, "close": price, "volume": 1}
        return closed


class ReplaySession:
    """Ties dataset + multiple timeframe builders together for a replay run.

    Usage:
        ds = TickDataset.load("market_data")
        sess = ReplaySession(ds, start="2026-07-01 10:00", timeframes=["M1","M5"],
                             input_tz="WIB")
        hist = sess.history            # {tf: DataFrame} up to start
        for event in sess.step():      # each yielded event carries any bars that closed
            ...                          # feed event.closed[tf] into indicators/strategy
    """
    def __init__(self, dataset: TickDataset, start, timeframes=("M1", "M5", "M15"),
                 input_tz: str = "WIB", output_tz: str = "WIB"):
        self.ds = dataset
        self.start = start
        self.input_tz = input_tz
        self.output_tz = output_tz
        self.timeframes = list(timeframes)
        self.builders = {tf: LiveCandleBuilder(tf) for tf in self.timeframes}
        # history frames (server time index) up to and including `start`
        self.history = {tf: self.ds.history_ohlc(start, tf, input_tz) for tf in self.timeframes}
        # seed each builder's bucket from the last history bar so live bars continue cleanly
        for tf, df in self.history.items():
            if len(df):
                last_t = df.index[-1]
                self.builders[tf].bucket = self.builders[tf]._floor(last_t)
                self.builders[tf].current = {
                    "time": last_t, "open": float(df.iloc[-1]["open"]),
                    "high": float(df.iloc[-1]["high"]), "low": float(df.iloc[-1]["low"]),
                    "close": float(df.iloc[-1]["close"]), "volume": int(df.iloc[-1]["volume"]),
                }

    def step(self):
        """Generator yielding one dict per tick: {'time','bid','ask','price','closed': {tf: bar}}."""
        for row in self.ds.replay(self.start, self.input_tz):
            ts = pd.Timestamp(row.time)
            closed = {}
            for tf, b in self.builders.items():
                bar = b.update(ts, row.price)
                if bar is not None:
                    closed[tf] = bar
            yield {"time": ts, "bid": row.bid, "ask": row.ask,
                   "price": row.price, "closed": closed}