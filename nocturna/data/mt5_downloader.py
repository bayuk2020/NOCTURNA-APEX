"""Download XAUUSD ticks + OHLC from MetaTrader 5 -> Parquet.

REQUIREMENTS (verify on YOUR machine — cannot run in this sandbox):
  - Windows, with the MT5 terminal installed AND logged in to your broker.
  - pip install MetaTrader5 pandas pyarrow
  - The terminal must be running (or pass path=... to initialize()).

IMPORTANT CAVEATS (all [Certain] and broker-dependent):
  - MT5 has NO native second timeframe. We fetch RAW TICKS and rebuild 1S/M1 via
    data.store.ticks_to_ohlc. copy_rates_range covers M1..D1 directly.
  - Tick 'time' is broker SERVER time, not UTC. Normalize downstream if needed.
  - XAUUSD "volume" from copy_rates is TICK volume (volume_real is often 0).
  - Deep tick history (back to 2026-07-09) may be missing depending on how much
    the broker server retains. This script reports what it actually got.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:  # so the module imports on non-Windows for inspection
    mt5 = None

TF_MAP = None  # populated after mt5 import


def _tf_map():
    return {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }


def connect(login: int | None = None, password: str | None = None,
            server: str | None = None, path: str | None = None) -> None:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package not available (Windows only).")
    kwargs = {}
    if path:
        kwargs["path"] = path
    if login:
        kwargs.update(login=login, password=password, server=server)
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")


def download_rates(symbol: str, timeframe: str, date_from: datetime,
                   date_to: datetime) -> pd.DataFrame:
    rates = mt5.copy_rates_range(symbol, _tf_map()[timeframe], date_from, date_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"tick_volume": "volume"}).set_index("time")
    return df[["open", "high", "low", "close", "volume"]]


def download_ticks(symbol: str, date_from: datetime, date_to: datetime,
                   chunk_days: int = 1) -> pd.DataFrame:
    """Fetch ticks in day-chunks to bound memory; concatenate."""
    frames = []
    cur = date_from
    while cur < date_to:
        nxt = min(cur + timedelta(days=chunk_days), date_to)
        ticks = mt5.copy_ticks_range(symbol, cur, nxt, mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks):
            t = pd.DataFrame(ticks)
            t["time"] = pd.to_datetime(t["time_msc"], unit="ms")
            frames.append(t[["time", "bid", "ask", "volume"]])
        cur = nxt
    if not frames:
        return pd.DataFrame(columns=["time", "bid", "ask", "volume"])
    return pd.concat(frames, ignore_index=True)


def download_all(symbol: str = "XAUUSD",
                 date_from: datetime = datetime(2026, 7, 9),
                 date_to: datetime | None = None,
                 out_dir: str = "market_data",
                 timeframes=("M1", "M5", "M15", "M30", "H1", "H4", "D1"),
                 with_ticks: bool = True) -> dict:
    """Download everything and save to Parquet. Returns a report of row counts."""
    date_to = date_to or datetime.now()
    out = Path(out_dir) / symbol
    out.mkdir(parents=True, exist_ok=True)
    report = {}

    for tf in timeframes:
        df = download_rates(symbol, tf, date_from, date_to)
        df.to_parquet(out / f"{tf}.parquet")
        report[tf] = len(df)
        print(f"[{symbol} {tf}] {len(df)} bars -> {out / f'{tf}.parquet'}")

    if with_ticks:
        ticks = download_ticks(symbol, date_from, date_to)
        ticks.to_parquet(out / "ticks.parquet")
        report["ticks"] = len(ticks)
        print(f"[{symbol} ticks] {len(ticks)} ticks -> {out / 'ticks.parquet'}")

    return report


if __name__ == "__main__":
    connect()  # uses the already-running, logged-in terminal
    rep = download_all()
    mt5.shutdown()
    print("Done:", rep)
