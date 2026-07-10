"""Deterministic integration test for TAHAP B sub-langkah 2 Bagian 1.

Proves (on the REAL window + StrategyReplay + Account, offscreen):
  1. auto-strategy is active (opens a position) as a baseline
  2. a risk protector breach closes the book AND halts
  3. while halted the auto-strategy does NOT reopen (no death-loop)
  4. a new broker day releases the halt + rebases daily_start_balance
  5. trading resumes on the new day (strategy reopens)

The protector breach is forced deterministically by lifting the peak equity
(same code path a real price-driven drawdown triggers — proven live in sub-1).
"""
import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pyqtgraph as pg

from nocturna.replay import TickDataset
from nocturna.engine.simulator import Account, Side
from nocturna.strategy_replay import StrategyReplay
from nocturna.apex_app import NocturnaApexWindow


def reenter_when_flat(ctx, lots=0.50):
    # aggressive: whenever flat, open — so WITHOUT a halt gate it would loop
    if not ctx.account.positions:
        ctx.account.open_market(Side.BUY, lots, ctx.price, ctx.time)


app = pg.mkQApp()
ds = TickDataset.load("market_data", symbol="XAUUSD")
acc = Account(balance=10_000, leverage=1000, contract_size=100, spread=0.18)
sr = StrategyReplay(ds, acc, reenter_when_flat, start="2026-07-01 08:00",
                    display_tf="M1", input_tz="WIB", display_tz="WIB",
                    ticks_per_frame=300, history_bars=400)
risk_cfg = dict(daily_target_pct=20.0, daily_stop_pct=3.0, equity_protector_pct=15.0,
                basket_target_pct=5.0, news_filter=False, max_layers=5)
win = NocturnaApexWindow(sr, "XAUUSD", "M1", "WIB", risk_cfg, interval=0.05)

PASS = True


def check(name, cond):
    global PASS
    print(("  PASS " if cond else "  FAIL ") + name)
    PASS = PASS and cond


def step(n):
    for _ in range(n):
        if win._done:
            break
        win._tick()


print("\n[1] baseline: auto-strategy should open a position")
for _ in range(80):
    step(1)
    if acc.positions or acc.history:
        break
check("strategy active (opened/closed something)", bool(acc.positions or acc.history))
print(f"      open={len(acc.positions)} closed={len(acc.history)} bal={acc.balance:.2f}")

print("\n[2] force equity-protector breach (peak +33% over equity => 25% DD)")
acc.max_equity = acc.equity(sr.cur_price) / 0.75
step(1)
check("halt engaged (_halted & sr.trading_halted)", win._halted and sr.trading_halted)
check("book flat after real close_all", len(acc.positions) == 0)
H = len(acc.history)
print(f"      closed={H} bal={acc.balance:.2f} halted={win._halted}")

print("\n[3] while halted the strategy must NOT reopen (no death-loop)")
step(50)
check("positions stay 0 while halted", len(acc.positions) == 0)
check("no new closed trades while halted", len(acc.history) == H)
print(f"      after 50 halted frames: open={len(acc.positions)} closed={len(acc.history)}")

print("\n[4] new broker day -> release halt + reset daily start balance")
prev_start = win.daily_start_balance
next_day = pd.Timestamp(sr.cur_time).normalize() + pd.Timedelta(days=1, hours=1)
win._maybe_daily_reset(next_day)
check("halt released", (not win._halted) and (not sr.trading_halted))
check("daily_start_balance == current balance", abs(win.daily_start_balance - acc.balance) < 1e-6)
check("daily_start_balance changed from before", win.daily_start_balance != prev_start)
print(f"      start_balance {prev_start:.2f} -> {win.daily_start_balance:.2f} (bal {acc.balance:.2f})")

print("\n[5] trading resumes on the new day")
for _ in range(80):
    step(1)
    if acc.positions:
        break
check("strategy reopened after new day", bool(acc.positions))
print(f"      open={len(acc.positions)} closed={len(acc.history)}")

print("\n=== RESULT:", "ALL PASS" if PASS else "SOME FAILED", "===")
# os._exit avoids an intermittent Qt/finplot teardown segfault on this stack.
sys.stdout.flush()
os._exit(0 if PASS else 1)
