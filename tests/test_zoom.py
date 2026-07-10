"""Verify Masalah 2: auto-scroll only when the view is stuck to the right edge.

Proves on the REAL window (offscreen):
  - _is_following logic: right edge -> True, panned/zoomed left -> False
  - after the user zooms out / pans left, advancing frames do NOT drag the view
    back to the latest price (view x-range preserved)
  - when the user returns to the right edge, auto-scroll resumes (view follows the
    newly-formed bars)
"""
import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyqtgraph as pg
from nocturna.replay import TickDataset
from nocturna.engine.simulator import Account
from nocturna.strategy_replay import StrategyReplay
from nocturna.apex_app import NocturnaApexWindow, default_indicator_registry

app = pg.mkQApp()
ds = TickDataset.load("market_data", symbol="XAUUSD")
acc = Account(balance=10_000, leverage=1000, contract_size=100, spread=0.18)
sr = StrategyReplay(ds, acc, lambda ctx: None, start="2026-07-01 08:00",
                    display_tf="M15", input_tz="WIB", display_tz="WIB",
                    ticks_per_frame=200, history_bars=400)
risk_cfg = dict(daily_target_pct=20, daily_stop_pct=3, equity_protector_pct=15,
                basket_target_pct=5, news_filter=False, max_layers=5)
win = NocturnaApexWindow(sr, "XAUUSD", "M15", "WIB", risk_cfg,
                         indicators=default_indicator_registry())
win.show()

PASS = True
def check(n, c):
    global PASS
    print(("  PASS " if c else "  FAIL ") + n)
    PASS = PASS and c

def step(n):
    for _ in range(n):
        if win._done:
            break
        win._tick()

vb = win.ax.vb
step(30)
n = len(sr.df)

print("\n[logic] _is_following")
check("right edge -> following True", win._is_following((n - 150, n - 1, 0, 1)) is True)
check("panned left -> following False", win._is_following((0, n - 120, 0, 1)) is False)

print("\n[A] zoom out / pan left is NOT dragged back")
vb.setXRange(max(0, n - 300), n - 120, padding=0)   # right edge 120 bars behind latest
x1_set = vb.viewRange()[0][1]
n_before = len(sr.df)
step(25)
n_after = len(sr.df)
x1_after = vb.viewRange()[0][1]
print(f"      n {n_before}->{n_after} | x1 set={x1_set:.1f} after={x1_after:.1f}")
check("new bars formed (a real drag opportunity existed)", n_after > n_before)
check("view x1 preserved (NOT dragged toward latest)", abs(x1_after - x1_set) < 5)

print("\n[B] returning to the right edge resumes auto-scroll")
n2 = len(sr.df)
vb.setXRange(n2 - 150, n2 - 1, padding=0)            # snap back to the latest bar
step(25)
n3 = len(sr.df)
x1_b = vb.viewRange()[0][1]
print(f"      n {n2}->{n3} | x1 now={x1_b:.1f} (latest~{n3 - 1})")
check("auto-scroll resumed (view followed new bars)", x1_b >= n3 - 1 - 15)

print("\n=== RESULT:", "ALL PASS" if PASS else "SOME FAILED", "===")
# os._exit avoids an intermittent Qt/finplot teardown segfault on this stack.
sys.stdout.flush()
os._exit(0 if PASS else 1)
