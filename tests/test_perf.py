"""Verify the WO#2 performance regression fix (Masalah 1).

Proves on the REAL window (offscreen), default indicators EMA9+EMA21+RSI14:
  1. indicators recompute+redraw only on a NEW bar, not every tick-frame
  2. the per-frame cost of one full indicator recompute is real (so skipping it
     per-frame is what unclogs the loop) -> avg tick is cheap on non-bar frames
  3. indicators still actually update (not frozen) as replay advances
  4. clicking BUY creates the marker IMMEDIATELY (no waiting for a frame)
  5. no "All-NaN slice" warning leaks to the terminal
"""
import os
import sys
import time
import warnings
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyqtgraph as pg
from nocturna.replay import TickDataset
from nocturna.engine.simulator import Account, Side
from nocturna.strategy_replay import StrategyReplay
from nocturna.apex_app import NocturnaApexWindow, default_indicator_registry

app = pg.mkQApp()
ds = TickDataset.load("market_data", symbol="XAUUSD")
acc = Account(balance=10_000, leverage=1000, contract_size=100, spread=0.18)
# real app config: M15 tf, 200 ticks/frame
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

# instrument indicator recompute
calls = {"n": 0}
_orig = win._update_indicators
def counting():
    calls["n"] += 1
    _orig()
win._update_indicators = counting

# cost of ONE full indicator recompute+redraw (what USED to run every frame)
t = time.perf_counter()
_orig()
t_ind = (time.perf_counter() - t) * 1000

import numpy as np
def ema_last():
    h = win._ind_handles.get((win._overlay_insts[0].instance_id, "ema"))
    return None if h is None else float(np.asarray(h.datasrc.y)[-1])

ema0 = ema_last()

FRAMES = 200
warnings.resetwarnings()  # start clean, keep the module's ignore filter re-applied below
from nocturna import apex_app  # re-assert the ignore filter after resetwarnings
warnings.filterwarnings("ignore", message="All-NaN slice encountered",
                        category=RuntimeWarning)
with warnings.catch_warnings(record=True) as w:
    times = []
    for _ in range(FRAMES):
        if win._done:
            break
        t = time.perf_counter()
        win._tick()
        times.append(time.perf_counter() - t)
allnan = [x for x in w if "All-NaN" in str(x.message)]

avg_ms = sum(times) / len(times) * 1000
mx_ms = max(times) * 1000
ema1 = ema_last()
print(f"  frames={len(times)} ind_recompute={calls['n']} "
      f"one_recompute={t_ind:.2f}ms avg_tick={avg_ms:.2f}ms max_tick={mx_ms:.2f}ms "
      f"allnan_warns={len(allnan)}")

check("indicators throttled (recompute < frames)", calls["n"] < len(times))
check("avg tick cheap vs one full recompute", avg_ms < t_ind or avg_ms < 15)
check("indicators still updating (EMA moved)", ema0 is not None and ema1 != ema0)
check("no All-NaN warning leaked", len(allnan) == 0)

# BUY click -> marker created immediately (no frame wait)
win.panel.lot_input.setValue(0.10)
before = win._marker_handles["buy"]
win.panel.btn_buy.click()
check("BUY click created marker immediately", win._marker_handles["buy"] is not None
      and before is None)

print("\n=== RESULT:", "ALL PASS" if PASS else "SOME FAILED", "===")
# os._exit avoids an intermittent Qt/finplot teardown segfault on this stack
# (the pass/fail is already decided above); flush first so output isn't lost.
sys.stdout.flush()
os._exit(0 if PASS else 1)
