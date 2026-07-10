"""MT5-like trading simulator for XAUUSD.

Models the mechanics that actually decide whether an account survives:
margin, commission, swap, floating vs closed P/L, SL/TP, trailing stop, break
even, partial close, multiple positions (so martingale/grid/hedging are just
strategy logic on top of this).

Scope control: this is a *simulator core*, not a broker. It fills at the price
you pass in (market model), plus an optional spread and slippage. Pending order
matching (Buy Stop/Limit etc.) lives in the backtest loop which knows the bar
high/low; here we expose helpers it calls.

XAUUSD conventions (configurable — brokers differ, verify yours):
  contract_size = 100 (1.0 lot = 100 oz)
  P/L per lot (buy) = (close - open) * contract_size * lots
  margin = lots * contract_size * price / leverage
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    BUY_STOP = "buy_stop"
    SELL_STOP = "sell_stop"
    BUY_LIMIT = "buy_limit"
    SELL_LIMIT = "sell_limit"


@dataclass
class Position:
    ticket: int
    side: Side
    lots: float
    open_price: float
    open_time: object
    sl: Optional[float] = None
    tp: Optional[float] = None
    trailing_pts: Optional[float] = None   # trailing distance in price units
    commission: float = 0.0
    swap: float = 0.0
    comment: str = ""
    _peak: Optional[float] = None          # best price seen, for trailing

    def signed(self) -> int:
        return 1 if self.side is Side.BUY else -1

    def floating_pnl(self, price: float, contract_size: float) -> float:
        return (price - self.open_price) * self.signed() * self.lots * contract_size + self.swap - self.commission


@dataclass
class ClosedTrade:
    ticket: int
    side: Side
    lots: float
    open_price: float
    close_price: float
    open_time: object
    close_time: object
    profit: float
    commission: float
    swap: float
    comment: str = ""


class Account:
    def __init__(self, balance: float = 10_000.0, leverage: int = 100,
                 contract_size: float = 100.0, commission_per_lot: float = 0.0,
                 swap_per_lot: float = 0.0, spread: float = 0.0):
        self.initial_balance = balance
        self.balance = balance
        self.leverage = leverage
        self.contract_size = contract_size
        self.commission_per_lot = commission_per_lot   # per lot, per side
        self.swap_per_lot = swap_per_lot                # per lot, per night (applied by loop)
        self.spread = spread                            # price units (e.g. 0.30 for gold)

        self.positions: list[Position] = []
        self.history: list[ClosedTrade] = []
        self._ticket = itertools.count(1)
        self.max_equity = balance
        self.max_drawdown = 0.0                          # as fraction of peak equity

    # ---------- pricing ----------
    def buy_price(self, mid: float) -> float:
        return mid + self.spread / 2

    def sell_price(self, mid: float) -> float:
        return mid - self.spread / 2

    # ---------- account metrics ----------
    def floating_pnl(self, mid: float) -> float:
        return sum(p.floating_pnl(self._exit_ref(p, mid), self.contract_size) for p in self.positions)

    def _exit_ref(self, p: Position, mid: float) -> float:
        # to close a BUY you hit the sell price; to close a SELL you hit the buy price
        return self.sell_price(mid) if p.side is Side.BUY else self.buy_price(mid)

    def equity(self, mid: float) -> float:
        return self.balance + self.floating_pnl(mid)

    def used_margin(self, mid: float) -> float:
        return sum(p.lots * self.contract_size * mid / self.leverage for p in self.positions)

    def free_margin(self, mid: float) -> float:
        return self.equity(mid) - self.used_margin(mid)

    def margin_level(self, mid: float) -> float:
        um = self.used_margin(mid)
        return (self.equity(mid) / um * 100.0) if um > 0 else float("inf")

    # ---------- orders ----------
    def open_market(self, side: Side, lots: float, mid: float, time,
                    sl=None, tp=None, trailing_pts=None, comment="") -> Position:
        price = self.buy_price(mid) if side is Side.BUY else self.sell_price(mid)
        comm = self.commission_per_lot * lots
        pos = Position(ticket=next(self._ticket), side=side, lots=lots,
                       open_price=price, open_time=time, sl=sl, tp=tp,
                       trailing_pts=trailing_pts, commission=comm, comment=comment,
                       _peak=price)
        self.positions.append(pos)
        return pos

    def close(self, ticket: int, mid: float, time, lots: Optional[float] = None) -> Optional[ClosedTrade]:
        """Close a position fully or partially (partial if lots < pos.lots)."""
        pos = next((p for p in self.positions if p.ticket == ticket), None)
        if pos is None:
            return None
        close_lots = pos.lots if lots is None else min(lots, pos.lots)
        exit_price = self._exit_ref(pos, mid)
        # proportional commission/swap for the closed slice
        frac = close_lots / pos.lots
        comm = pos.commission * frac
        swap = pos.swap * frac
        profit = (exit_price - pos.open_price) * pos.signed() * close_lots * self.contract_size + swap - comm
        self.balance += profit
        trade = ClosedTrade(pos.ticket, pos.side, close_lots, pos.open_price, exit_price,
                            pos.open_time, time, profit, comm, swap, pos.comment)
        self.history.append(trade)
        if close_lots >= pos.lots - 1e-9:
            self.positions.remove(pos)
        else:  # partial close: shrink remaining
            pos.lots -= close_lots
            pos.commission -= comm
            pos.swap -= swap
        return trade

    def close_all(self, mid: float, time) -> list[ClosedTrade]:
        return [self.close(p.ticket, mid, time) for p in list(self.positions)]

    def set_break_even(self, ticket: int, offset: float = 0.0) -> None:
        pos = next((p for p in self.positions if p.ticket == ticket), None)
        if pos:
            pos.sl = pos.open_price + pos.signed() * offset

    # ---------- per-tick/-bar maintenance ----------
    def update(self, high: float, low: float, mid_close: float, time) -> list[ClosedTrade]:
        """Check SL/TP (intrabar via high/low) and update trailing stops.

        Returns trades closed on this bar. Approximation: if both SL and TP are
        inside [low,high] we assume SL hit first (conservative).
        """
        closed = []
        for pos in list(self.positions):
            # trailing stop update
            if pos.trailing_pts:
                if pos.side is Side.BUY:
                    pos._peak = max(pos._peak or high, high)
                    new_sl = pos._peak - pos.trailing_pts
                    pos.sl = max(pos.sl, new_sl) if pos.sl else new_sl
                else:
                    pos._peak = min(pos._peak or low, low)
                    new_sl = pos._peak + pos.trailing_pts
                    pos.sl = min(pos.sl, new_sl) if pos.sl else new_sl

            hit_sl = pos.sl is not None and ((pos.side is Side.BUY and low <= pos.sl) or
                                             (pos.side is Side.SELL and high >= pos.sl))
            hit_tp = pos.tp is not None and ((pos.side is Side.BUY and high >= pos.tp) or
                                             (pos.side is Side.SELL and low <= pos.tp))
            fill = None
            if hit_sl:
                fill = pos.sl
            elif hit_tp:
                fill = pos.tp
            if fill is not None:
                # translate fill price back to a 'mid' so close() reapplies spread correctly
                mid_equiv = fill + (self.spread / 2 if pos.side is Side.BUY else -self.spread / 2)
                closed.append(self.close(pos.ticket, mid_equiv, time))

        eq = self.equity(mid_close)
        self.max_equity = max(self.max_equity, eq)
        if self.max_equity > 0:
            self.max_drawdown = max(self.max_drawdown, (self.max_equity - eq) / self.max_equity)
        return [c for c in closed if c]

    # ---------- basket view (for the NOCTURNA-APEX dashboard) ----------
    def basket(self, mid: float) -> dict:
        if not self.positions:
            return {"direction": None, "layers": 0, "total_lot": 0.0,
                    "avg_price": None, "pnl": 0.0}
        total_lot = sum(p.lots for p in self.positions)
        avg = sum(p.open_price * p.lots for p in self.positions) / total_lot
        # dominant direction by lot
        buy_lot = sum(p.lots for p in self.positions if p.side is Side.BUY)
        sell_lot = total_lot - buy_lot
        direction = Side.BUY if buy_lot >= sell_lot else Side.SELL
        return {
            "direction": direction.value,
            "layers": len(self.positions),
            "total_lot": round(total_lot, 2),
            "avg_price": round(avg, 3),
            "current_price": round(mid, 3),
            "pnl": round(self.floating_pnl(mid), 2),
            "open_time": min(p.open_time for p in self.positions),
        }
