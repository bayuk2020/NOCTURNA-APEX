"""Storage and resampling layer.

Responsibilities:
- Persist/load tick and OHLC data as Parquet (columnar, fast, compressed).
- Reconstruct OHLC bars from raw ticks (MT5 has no native sub-minute timeframe).
- Resample an OHLC frame to a higher timeframe.

Design notes / assumptions:
- Ticks frame expected columns: ['time' (tz-aware or naive UTC), 'bid', 'ask', 'volume'].
  We build bars from MID price = (bid+ask)/2 by default (configurable) so wicks
  reflect quote extremes, not just trade prints. This matches how many replay
  tools reconstruct candles.
- Volume for XAUUSD CFD is tick-volume, not real volume. We sum tick 'volume'
  (or count ticks if absent). Do not treat it as real traded volume.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

# MT5 timeframe -> pandas offset alias. Sub-minute ("1S", "5S") only makes sense
# from ticks; the minute+ ones can also resample an existing lower-TF frame.
TIMEFRAMES: dict[str, str] = {
    "1S": "1s", "5S": "5s", "15S": "15s", "30S": "30s",
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1D",
}

PriceSource = Literal["mid", "bid", "ask"]


def ticks_to_ohlc(ticks: pd.DataFrame, timeframe: str,
                  price: PriceSource = "mid") -> pd.DataFrame:
    """Reconstruct OHLC(V) bars from raw ticks.

    Returns a frame indexed by bar-open time with columns
    ['open','high','low','close','volume','ticks'].
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {timeframe!r}. Valid: {list(TIMEFRAMES)}")
    if ticks.empty:
        return _empty_ohlc()

    df = ticks.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()

    if price == "mid":
        px = (df["bid"] + df["ask"]) / 2.0
    else:
        px = df[price]

    rule = TIMEFRAMES[timeframe]
    grouped = px.resample(rule, label="left", closed="left")
    ohlc = grouped.ohlc()
    ohlc = ohlc.dropna(subset=["open"])  # drop empty periods (market closed)

    if "volume" in df.columns and df["volume"].notna().any():
        vol = df["volume"].resample(rule, label="left", closed="left").sum()
    else:
        vol = px.resample(rule, label="left", closed="left").count()
    ohlc["volume"] = vol.reindex(ohlc.index).fillna(0.0)
    ohlc["ticks"] = px.resample(rule, label="left", closed="left").count().reindex(ohlc.index).fillna(0).astype(int)
    return ohlc


def resample_ohlc(bars: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate a lower-timeframe OHLC frame into a higher timeframe."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {timeframe!r}")
    if bars.empty:
        return _empty_ohlc()
    df = bars.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    rule = TIMEFRAMES[timeframe]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    out = df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
    return out


def _empty_ohlc() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="time")
    return pd.DataFrame(
        {c: pd.Series(dtype="float64") for c in ["open", "high", "low", "close", "volume"]},
        index=idx,
    )


# ---------- Parquet IO ----------

def save_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def load_parquet(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def slice_until(bars: pd.DataFrame, until) -> pd.DataFrame:
    """Return only bars whose open time is <= `until`.

    Used by the replay engine: 'show me history up to the chosen datetime, and
    nothing after it.'
    """
    until = pd.to_datetime(until, utc=True)
    return bars.loc[bars.index <= until]
