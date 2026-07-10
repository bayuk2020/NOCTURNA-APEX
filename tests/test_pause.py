"""Deterministic test for TAHAP B sub-langkah 2 Bagian 2: Pause EA + SPACE.

Proves on the REAL window (offscreen):
  A. pause freezes the whole loop (cur_time + panel do NOT advance)
  B. resume continues smoothly (loop advances again from where it stopped)
  C. PAUSE EA button toggles state + label (Pause <-> Resume)
  D. pause is INDEPENDENT of halt: pressable while halted, neither blocks the
     other (halt stays set across pause/resume; pause stays set across halt)
  E. SPACE hotkey toggles pause (real key event via QTest)
"""
import os
import sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from nocturna.replay import TickDataset
from nocturna.engine.simulator import Account, Side
from nocturna.strategy_replay import StrategyReplay
from nocturna.apex_app import NocturnaApexWindow


def reenter_when_flat(ctx, lots=0.30):
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
win.show()
win.activateWindow()
QApplication.processEvents()

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


def eq_text():
    return win.panel.sec_account._rows["equity"].text()


print("\n[warmup] advance a few frames")
step(10)
print(f"      cur_time={sr.cur_time}  label='{win.panel.btn_pause.text()}'")

print("\n[A] pause freezes candle + panel")
win._toggle_pause()
t_frozen = sr.cur_time
eq_frozen = eq_text()
check("_paused True after toggle", win._paused is True)
check("label -> 'RESUME EA'", win.panel.btn_pause.text() == "RESUME EA")
step(30)  # timer would fire 30x; all must be no-ops
check("cur_time did NOT advance while paused", sr.cur_time == t_frozen)
check("panel equity label unchanged while paused", eq_text() == eq_frozen)

print("\n[B] resume continues smoothly")
win._toggle_pause()
check("_paused False after toggle", win._paused is False)
check("label -> 'PAUSE EA'", win.panel.btn_pause.text() == "PAUSE EA")
step(5)
check("cur_time advanced after resume", sr.cur_time != t_frozen)

print("\n[C] PAUSE EA button click toggles")
win.panel.btn_pause.click()
check("click -> paused", win._paused is True and win.panel.btn_pause.text() == "RESUME EA")
win.panel.btn_pause.click()
check("click -> resumed", win._paused is False and win.panel.btn_pause.text() == "PAUSE EA")

print("\n[D] pause INDEPENDENT of halt")
win._set_halted(True)
check("PAUSE EA still enabled while halted", win.panel.btn_pause.isEnabled() is True)
check("BUY disabled by halt (unchanged behaviour)", win.panel.btn_buy.isEnabled() is False)
win.panel.btn_pause.click()                      # pause WHILE halted
check("can pause while halted", win._paused is True)
check("halt NOT cleared by pausing", win._halted is True and sr.trading_halted is True)
t_h = sr.cur_time
step(20)
check("loop frozen while halted+paused", sr.cur_time == t_h)
win.panel.btn_pause.click()                      # resume WHILE halted
check("can resume while halted", win._paused is False)
check("halt STILL set after resume", win._halted is True and sr.trading_halted is True)
step(5)
check("loop advances again after resume (still halted)", sr.cur_time != t_h)
win._set_halted(False)                            # tidy up for [E]

print("\n[E] SPACE hotkey toggles pause")
check("SPACE shortcut object exists", hasattr(win, "_pause_shortcut"))
before = win._paused
QTest.keyClick(win, Qt.Key.Key_Space)
QApplication.processEvents()
check("SPACE flipped pause once", win._paused == (not before))
QTest.keyClick(win, Qt.Key.Key_Space)
QApplication.processEvents()
check("SPACE flipped pause back", win._paused == before)

print("\n=== RESULT:", "ALL PASS" if PASS else "SOME FAILED", "===")
raise SystemExit(0 if PASS else 1)
