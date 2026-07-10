"""Built-in indicators. Pure numpy/pandas. Each is a plugin-ready Indicator.

Verified against standard definitions. Where a definition varies by author
(e.g. RSI uses Wilder's smoothing; UT Bot uses ATR trailing stop), the choice is
noted in the docstring so results are reproducible and comparable to TradingView.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Indicator, Plot


def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA) — used by RSI/ATR, matches TradingView ta.rma."""
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder_rma(true_range(df), period)


class SMA(Indicator):
    name = "SMA"
    overlay = True
    default_params = {"length": 20, "source": "close"}
    plots = [Plot("sma", "#f5a623")]

    def compute(self, df):
        s = df[self.params["source"]].rolling(self.params["length"]).mean()
        return {"sma": s}


class EMA(Indicator):
    name = "EMA"
    overlay = True
    default_params = {"length": 20, "source": "close"}
    plots = [Plot("ema", "#2962FF")]

    def compute(self, df):
        length = self.params["length"]
        s = df[self.params["source"]].ewm(span=length, adjust=False, min_periods=length).mean()
        return {"ema": s}


class RSI(Indicator):
    name = "RSI"
    overlay = False
    default_params = {"length": 14, "source": "close"}
    plots = [Plot("rsi", "#7e57c2")]

    def compute(self, df):
        length = self.params["length"]
        delta = df[self.params["source"]].diff()
        gain = _wilder_rma(delta.clip(lower=0), length)
        loss = _wilder_rma(-delta.clip(upper=0), length)
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return {"rsi": rsi.fillna(100)}


class ATR(Indicator):
    name = "ATR"
    overlay = False
    default_params = {"length": 14}
    plots = [Plot("atr", "#ef5350")]

    def compute(self, df):
        return {"atr": atr(df, self.params["length"])}


class MACD(Indicator):
    name = "MACD"
    overlay = False
    default_params = {"fast": 12, "slow": 26, "signal": 9, "source": "close"}
    plots = [Plot("macd", "#2962FF"), Plot("signal", "#ff6d00"),
             Plot("hist", "#26a69a", kind="histogram")]

    def compute(self, df):
        src = df[self.params["source"]]
        fast = src.ewm(span=self.params["fast"], adjust=False).mean()
        slow = src.ewm(span=self.params["slow"], adjust=False).mean()
        macd = fast - slow
        signal = macd.ewm(span=self.params["signal"], adjust=False).mean()
        return {"macd": macd, "signal": signal, "hist": macd - signal}


class BollingerBands(Indicator):
    name = "BollingerBands"
    overlay = True
    default_params = {"length": 20, "mult": 2.0, "source": "close"}
    plots = [Plot("basis", "#ff6d00"), Plot("upper", "#787b86"), Plot("lower", "#787b86")]

    def compute(self, df):
        src = df[self.params["source"]]
        basis = src.rolling(self.params["length"]).mean()
        dev = self.params["mult"] * src.rolling(self.params["length"]).std(ddof=0)
        return {"basis": basis, "upper": basis + dev, "lower": basis - dev}


class VWAP(Indicator):
    name = "VWAP"
    overlay = True
    default_params = {"anchor": "D"}  # reset per day
    plots = [Plot("vwap", "#00bcd4")]

    def compute(self, df):
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vol = df.get("volume", pd.Series(1.0, index=df.index)).replace(0, 1.0)
        grp = df.index.to_period(self.params["anchor"])
        cum_pv = (tp * vol).groupby(grp).cumsum()
        cum_v = vol.groupby(grp).cumsum()
        return {"vwap": cum_pv / cum_v}


class Supertrend(Indicator):
    name = "Supertrend"
    overlay = True
    default_params = {"period": 10, "mult": 3.0}
    plots = [Plot("supertrend", "#26a69a"), Plot("direction", "#787b86")]

    def compute(self, df):
        period, mult = self.params["period"], self.params["mult"]
        a = atr(df, period)
        hl2 = (df["high"] + df["low"]) / 2
        upper = hl2 + mult * a
        lower = hl2 - mult * a
        close = df["close"].to_numpy()
        n = len(df)
        final_upper = upper.to_numpy().copy()
        final_lower = lower.to_numpy().copy()
        st = np.full(n, np.nan)
        direction = np.ones(n)  # 1 uptrend, -1 downtrend
        for i in range(1, n):
            final_upper[i] = (min(final_upper[i], final_upper[i - 1])
                              if close[i - 1] <= final_upper[i - 1] else final_upper[i])
            final_lower[i] = (max(final_lower[i], final_lower[i - 1])
                              if close[i - 1] >= final_lower[i - 1] else final_lower[i])
            if close[i] > final_upper[i - 1]:
                direction[i] = 1
            elif close[i] < final_lower[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
            st[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
        idx = df.index
        return {"supertrend": pd.Series(st, index=idx),
                "direction": pd.Series(direction, index=idx)}


class UTBot(Indicator):
    """UT Bot Alerts (ATR trailing-stop). Signal keys: 'buy'/'sell' are 1 on flip.

    keyvalue = sensitivity (a in original), atr_period default 10 (original uses
    ATR-based nLoss). This matches the widely used TradingView 'UT Bot Alerts'.
    """
    name = "UTBot"
    overlay = True
    default_params = {"keyvalue": 1.0, "atr_period": 10}
    plots = [Plot("trailing", "#facc15"), Plot("buy", "#26a69a"), Plot("sell", "#ef5350")]

    def compute(self, df):
        a = atr(df, self.params["atr_period"])
        nloss = self.params["keyvalue"] * a
        close = df["close"]
        n = len(df)
        stop = np.zeros(n)
        c = close.to_numpy()
        nl = nloss.to_numpy()
        for i in range(1, n):
            prev = stop[i - 1]
            if c[i] > prev and c[i - 1] > prev:
                stop[i] = max(prev, c[i] - nl[i])
            elif c[i] < prev and c[i - 1] < prev:
                stop[i] = min(prev, c[i] + nl[i])
            elif c[i] > prev:
                stop[i] = c[i] - nl[i]
            else:
                stop[i] = c[i] + nl[i]
        stop_s = pd.Series(stop, index=df.index)
        above = close > stop_s
        cross_up = above & ~above.shift(1, fill_value=False)
        cross_dn = ~above & above.shift(1, fill_value=False)
        return {"trailing": stop_s,
                "buy": cross_up.astype(int),
                "sell": cross_dn.astype(int)}


BUILTINS = [SMA, EMA, RSI, ATR, MACD, BollingerBands, VWAP, Supertrend, UTBot]


def register_builtins(registry) -> None:
    for cls in BUILTINS:
        registry.register(cls)
