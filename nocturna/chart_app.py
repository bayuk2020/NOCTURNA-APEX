"""TradingView-like chart window using lightweight-charts-python.

REQUIREMENTS (verify on your machine; needs a display):
  pip install lightweight-charts pandas

Gives you: candlestick, crosshair, zoom, pan, auto-scroll, price/time scale,
volume, indicator OVERLAYS (EMA/VWAP/Supertrend/UT Bot trailing) and indicator
SUBWINDOWS (RSI/MACD/ATR) — driven by the same IndicatorRegistry as the backtester,
so what you see is what you test.

Replay: `until` clips history to a chosen datetime; call `chart.update()` in a
loop feeding later bars/ticks to "replay the tape".
"""
from __future__ import annotations

import pandas as pd

from .indicators.base import IndicatorRegistry
from .indicators.library import register_builtins


def _ohlc_for_lwc(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.reset_index().rename(columns={bars.index.name or "index": "time"})
    return df[["time", "open", "high", "low", "close", "volume"]]


def show_chart(bars: pd.DataFrame, registry: IndicatorRegistry | None = None,
               title: str = "NOCTURNA-APEX | XAUUSD"):
    from lightweight_charts import Chart  # imported here so module loads without the dep

    chart = Chart(title=title, toolbox=True)
    chart.set(_ohlc_for_lwc(bars))
    chart.volume_config(up_color="#26a69a", down_color="#ef5350")
    chart.crosshair(mode="normal")
    chart.fit()

    if registry:
        for inst in registry.instances(only_enabled=True):
            out = inst.compute(bars)
            for key, series in out.items():
                if key in ("buy", "sell", "direction"):
                    continue  # signal markers handled separately
                color = inst.colors.get(key, "#2962FF")
                if inst.overlay:
                    line = chart.create_line(name=f"{inst.name}:{key}", color=color)
                    line.set(pd.DataFrame({"time": bars.index, key: series.values}).rename(columns={key: f"{inst.name}:{key}"}))
                else:
                    sub = chart.create_subchart(width=1.0, height=0.25, sync=True)
                    sub_line = sub.create_line(name=f"{inst.name}:{key}", color=color)
                    sub_line.set(pd.DataFrame({"time": bars.index, key: series.values}).rename(columns={key: f"{inst.name}:{key}"}))
    chart.show(block=True)
    return chart


if __name__ == "__main__":
    # demo: load parquet produced by mt5_downloader, or synthetic if absent
    import numpy as np
    from pathlib import Path
    from .data.store import load_parquet

    p = Path("market_data/XAUUSD/M5.parquet")
    if p.exists():
        bars = load_parquet(p)
    else:
        from .demo_run import make_synthetic_ticks
        from .data.store import ticks_to_ohlc
        bars = ticks_to_ohlc(make_synthetic_ticks(1000), "M5")

    reg = IndicatorRegistry()
    register_builtins(reg)
    reg.create("EMA", params={"length": 9}, colors={"ema": "#00e676"})
    reg.create("EMA", params={"length": 21})
    reg.create("RSI", params={"length": 14})
    show_chart(bars, reg)
