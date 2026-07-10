"""NOCTURNA-APEX dashboard snapshot.

Computes the 4 panels you specified from the live Account + recent bars:
  1. Account Information
  2. Basket Information
  3. Market Condition (trend/ADX/ATR/volatility/spread/news)
  4. Risk Management (daily target/stop, equity protector, margin level)

This is the data layer for the dashboard. The GUI (PyQt/lightweight-charts panel)
renders these numbers; here we also provide a plain-text renderer for headless
testing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators.library import atr, _wilder_rma, true_range


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_ = _wilder_rma(tr, period)
    plus_di = 100 * _wilder_rma(pd.Series(plus_dm, index=df.index), period) / atr_
    minus_di = 100 * _wilder_rma(pd.Series(minus_dm, index=df.index), period) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder_rma(dx.fillna(0), period)


def market_condition(bars: pd.DataFrame, spread: float = 0.30,
                     news_filter: bool = False) -> dict:
    if len(bars) < 30:
        return {"trend": "n/a", "state": "n/a", "adx": None, "atr": None,
                "volatility": "n/a", "spread": spread, "news_filter": news_filter}
    a = adx(bars).iloc[-1]
    at = atr(bars, 14).iloc[-1]
    ema_fast = bars["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    ema_slow = bars["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    # ADX>25 => trending; slope of fast EMA decides direction
    if a >= 25:
        state = "Trending"
        trend = "Bullish" if ema_fast > ema_slow else "Bearish"
    else:
        state = "Ranging"
        trend = "Sideways"
    atr_pct = at / bars["close"].iloc[-1] * 100
    volatility = "High" if atr_pct > 0.15 else "Normal" if atr_pct > 0.06 else "Low"
    return {"trend": trend, "state": state, "adx": round(float(a), 1),
            "atr": round(float(at), 3), "volatility": volatility,
            "spread": spread, "news_filter": news_filter}


def nocturna_apex_snapshot(account, bars: pd.DataFrame, daily_start_balance: float,
                           daily_target_pct=20.0, daily_stop_pct=3.0,
                           equity_protector_pct=15.0, basket_target_pct=5.0,
                           news_filter=False) -> dict:
    mid = float(bars["close"].iloc[-1])
    equity = account.equity(mid)
    floating = account.floating_pnl(mid)
    daily_realized = account.balance - daily_start_balance
    daily_realized_pct = daily_realized / daily_start_balance * 100

    basket = account.basket(mid)
    basket_pnl_pct = None
    basket_hit = False
    if basket["layers"]:
        margin_used = account.used_margin(mid)
        basket_pnl_pct = (basket["pnl"] / daily_start_balance * 100)
        basket_hit = basket_pnl_pct >= basket_target_pct

    status = "Trading Active" if account.positions else "Trading Rest"

    return {
        "status": status,
        "account": {
            "balance": round(account.balance, 2),
            "equity": round(equity, 2),
            "floating_pnl": round(floating, 2),
            "daily_start_balance": round(daily_start_balance, 2),
            "daily_realized_pnl": round(daily_realized, 2),
            "daily_target_pct": daily_target_pct,
            "daily_stop_pct": daily_stop_pct,
            "equity_protector_pct": equity_protector_pct,
            "daily_status": "Profit" if daily_realized >= 0 else "Loss",
        },
        "basket": {**basket, "target_pct": basket_target_pct,
                   "pnl_pct": None if basket_pnl_pct is None else round(basket_pnl_pct, 2),
                   "hit": basket_hit},
        "market": market_condition(bars, spread=account.spread, news_filter=news_filter),
        "risk": {
            "daily_target": f"{round(daily_realized_pct, 2)}% / {daily_target_pct}%",
            "daily_stop": f"{round(abs(min(daily_realized_pct,0)), 2)}% / {daily_stop_pct}%",
            "equity_protector": f"{round((account.max_equity-equity)/account.max_equity*100,2)}% / {equity_protector_pct}%",
            "margin_level": (round(account.margin_level(mid), 1)
                             if account.positions else None),
        },
    }


def check_risk_triggers(snap: dict) -> list[str]:
    """Return list of triggered protective actions (the loop should act on these)."""
    actions = []
    acc = snap["account"]
    realized_pct = acc["daily_realized_pnl"] / acc["daily_start_balance"] * 100
    if realized_pct <= -acc["daily_stop_pct"]:
        actions.append("CLOSE_ALL_DAILY_STOP")
    if realized_pct >= acc["daily_target_pct"]:
        actions.append("STOP_TRADING_TARGET_HIT")
    # equity protector: drawdown from peak equity
    prot = float(snap["risk"]["equity_protector"].split("%")[0])
    if prot >= acc["equity_protector_pct"]:
        actions.append("CLOSE_ALL_EQUITY_PROTECTOR")
    return actions


def render_dashboard(snap: dict) -> str:
    a, b, m, r = snap["account"], snap["basket"], snap["market"], snap["risk"]
    L = []
    L.append(f"STATUS: {snap['status']}")
    L.append("-- 1. Account --")
    L.append(f"  Balance {a['balance']} | Equity {a['equity']} | Floating {a['floating_pnl']} "
             f"| Daily PnL {a['daily_realized_pnl']} ({a['daily_status']})")
    L.append("-- 2. Basket --")
    if b["layers"]:
        L.append(f"  {b['direction']} | layers {b['layers']}/5 | lot {b['total_lot']} | "
                 f"avg {b['avg_price']} | cur {b.get('current_price')} | PnL {b['pnl']} "
                 f"({b['pnl_pct']}% / {b['target_pct']}%{' HIT' if b['hit'] else ''})")
    else:
        L.append("  (flat)")
    L.append("-- 3. Market Condition --")
    L.append(f"  Trend {m['trend']} | State {m['state']} | Vol {m['volatility']} | "
             f"ADX(14) {m['adx']} | ATR(14) {m['atr']} | Spread {m['spread']} | News {m['news_filter']}")
    L.append("-- 4. Risk --")
    L.append(f"  Daily Target {r['daily_target']} | Stop {r['daily_stop']} | "
             f"Equity Protector {r['equity_protector']} | Margin Level {r['margin_level']}")
    return "\n".join(L)
