"""NOCTURNA-APEX GUI shell — TAHAP A.

A single PyQt6 window that hosts the moving finplot chart (left) and the
read-only NOCTURNA-APEX dashboard panel (right). One tick stream (StrategyReplay)
drives both: candles/markers on the chart and the 4 panels, refreshed live.

Embedding note (finplot 1.9.7): passing a *non* GraphicsLayoutWidget as `master`
to ``create_plot_widget`` makes finplot build a real ``pg.PlotWidget`` per axis,
exposed as ``ax.ax_widget`` — that widget is what we drop into our Qt layout. We
also set ``win.axs = [ax]`` because finplot's refresh/autoscale look it up there.

Run:
    python -m nocturna.apex_app                 # live replay GUI
    python -m nocturna.apex_app --smoke         # self-test: seed basket, shot, quit
"""
from __future__ import annotations

import os
import sys

import pandas as pd

import finplot as fplt
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QMainWindow, QScrollArea, QVBoxLayout, QWidget

from .replay import TickDataset
from .engine.simulator import Account, Side
from .apex_panel import DashboardPanel
from .dashboard import nocturna_apex_snapshot, check_risk_triggers
from .strategy_replay import StrategyReplay, _demo_strategy
from .indicators.base import IndicatorRegistry
from .indicators.library import register_builtins

# risk actions that must FORCE a real close_all (not just show a ✘ badge)
_FORCE_CLOSE_ACTIONS = ("CLOSE_ALL_DAILY_STOP", "CLOSE_ALL_EQUITY_PROTECTOR")

_TZ_SHIFT_H = {"SERVER": 0, "UTC": -3, "WIB": 4}

# indicator plot keys that are regime/signal flags (0/1 or ±1), NOT price lines —
# never drawn on an axis (e.g. Supertrend 'direction', UTBot 'buy'/'sell').
_SKIP_PLOT_KEYS = {"direction", "buy", "sell"}
# subwindow horizontal guide levels per indicator type
_GUIDE_LEVELS = {"RSI": (70, 50, 30)}


def default_indicator_registry() -> IndicatorRegistry:
    """Code-defined active indicators for work-order #2 (no add/edit UI yet, that
    is work-order #5): EMA(9) green + EMA(21) blue overlay, RSI(14) subwindow."""
    reg = IndicatorRegistry()
    register_builtins(reg)
    reg.create("EMA", params={"length": 9}, colors={"ema": "#26c281"})
    reg.create("EMA", params={"length": 21}, colors={"ema": "#3b82f6"})
    reg.create("RSI", params={"length": 14})
    return reg


def _legend_for(inst) -> str:
    for k in ("length", "period", "atr_period"):
        if k in inst.params:
            return f"{inst.name} {inst.params[k]}"
    return inst.name


class NocturnaApexWindow(QMainWindow):
    def __init__(self, sr: StrategyReplay, symbol: str, display_tf: str,
                 display_tz: str, risk_cfg: dict, interval: float = 0.05,
                 indicators: IndicatorRegistry | None = None):
        super().__init__()
        self.sr = sr
        self.symbol = symbol
        self.display_tf = display_tf
        self.risk_cfg = risk_cfg
        self.interval = interval
        self._done = False
        self._halted = False           # risk stop: blocks ENTRY only
        self._paused = False           # pause: freezes the WHOLE loop (independent)
        # per-day risk envelope: reset at each new broker (server-time) day so a
        # protector hit doesn't kill the whole remaining replay
        self.daily_start_balance = sr.account.initial_balance
        self._cur_day = sr.cur_time.normalize() if sr.cur_time is not None else None

        self.setWindowTitle(f"NOCTURNA-APEX v2 — {symbol} {display_tf} ({display_tz})")
        self.setStyleSheet("QMainWindow { background:#0a0e17; }")

        # ---- indicators (code-defined; work-order #2) ----
        self.indicators = indicators if indicators is not None else default_indicator_registry()
        insts = self.indicators.instances(only_enabled=True)
        self._overlay_insts = [i for i in insts if i.overlay]
        self._sub_insts = [i for i in insts if not i.overlay]

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- left: finplot price chart + one subwindow per non-overlay indicator ----
        # rows>1 gives finplot an X-linked PlotWidget per row; win.axs MUST list them
        # all or refresh()/autoscale errors (see HANDOFF embed notes).
        rows = 1 + len(self._sub_insts)
        axs = fplt.create_plot_widget(master=self, rows=rows, init_zoom_periods=150)
        axs = list(axs) if isinstance(axs, (list, tuple)) else [axs]
        self.axs = axs                             # finplot refresh() reads win.axs
        self.ax = axs[0]                           # price axis (candles + overlays)
        self._sub_axes = axs[1:]                   # one axis per subwindow indicator

        chart_col = QWidget()
        chart_v = QVBoxLayout(chart_col)
        chart_v.setContentsMargins(0, 0, 0, 0)
        chart_v.setSpacing(0)
        chart_v.addWidget(self.ax.ax_widget, stretch=4)
        for sax in self._sub_axes:
            chart_v.addWidget(sax.ax_widget, stretch=1)
        chart_col.setMinimumWidth(700)
        layout.addWidget(chart_col, stretch=1)

        # ---- right: dashboard panel (scrollable so it survives short screens) ----
        self.panel = DashboardPanel(config=risk_cfg,
                                    tz_shift_hours=_TZ_SHIFT_H[display_tz])
        scroll = QScrollArea()
        scroll.setWidget(self.panel)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(360)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:#0a0e17; border:none; }")
        layout.addWidget(scroll)

        # ---- plot series ----
        cols = ["open", "close", "high", "low"]
        self.candles = fplt.candlestick_ochl(sr.df[cols], ax=self.ax)
        self._marker_handles = {"buy": None, "sell": None, "exit": None}
        self._ind_handles = {}                     # (instance_id, plot_key) -> handle
        self._build_indicator_plots()

        # ---- wire manual trade buttons (TAHAP B sub-langkah 1) ----
        self.panel.btn_buy.clicked.connect(self._on_buy)
        self.panel.btn_sell.clicked.connect(self._on_sell)
        self.panel.btn_close_partial.clicked.connect(self._on_close_partial)
        self.panel.btn_close_all.clicked.connect(self._on_close_all)

        # ---- pause: button + SPACE hotkey (TAHAP B sub-langkah 2 Bagian 2) ----
        self.panel.btn_pause.clicked.connect(self._toggle_pause)
        # WindowShortcut fires before the focused child (chart viewbox) sees the
        # key, so SPACE works no matter what has focus — and only one handler runs.
        self._pause_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._pause_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._pause_shortcut.activated.connect(self._toggle_pause)

        self._gen = sr.frames()
        self._push_snapshot()                      # initial paint before first tick

    # ------------------------------------------------------------------ loop
    def start(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(self.interval * 1000))

    def _tick(self):
        # checked at the START of every frame; the QTimer keeps firing but does
        # nothing while paused — no stop/start mid-frame, so no click/timer race.
        if self._done or self._paused:
            return
        try:
            fr = next(self._gen)
        except StopIteration:
            self._done = True
            self._timer.stop()
            return
        self._maybe_daily_reset(self.sr.cur_time)
        self.candles.update_data(self.sr.df[["open", "close", "high", "low"]])
        self._update_indicators()
        self._draw_markers(fr)
        snap = self._push_snapshot()
        self._apply_risk_triggers(snap)

    def _apply_risk_triggers(self, snap):
        """Turn check_risk_triggers() into a REAL close_all — otherwise the ✘
        badge is fake protection. Only acts while a book is open; once fired the
        session is halted so it can't re-enter a stopped-out day."""
        actions = [a for a in check_risk_triggers(snap) if a in _FORCE_CLOSE_ACTIONS]
        if actions and self.sr.account.positions:
            n = len(self.sr.account.positions)
            self.sr.account.close_all(self.sr.cur_price, self.sr.cur_time)
            self._set_halted(True)
            print(f"[apex] RISK TRIGGER {actions} -> close_all ({n} pos) | trading halted")
            self._push_snapshot()

    def _draw_markers(self, fr):
        idx = self.sr.df.index
        specs = [("buy", fr["buys"], "#26a69a", "^"),
                 ("sell", fr["sells"], "#ef5350", "v"),
                 ("exit", fr["exits"], "#b0b0b0", "x")]
        for name, series, color, style in specs:
            if series is None or len(series) == 0:
                continue
            aligned = pd.Series(index=idx, dtype="float64")
            for t, price in series.items():
                pos = idx.get_indexer([t], method="nearest")[0]
                if pos >= 0:
                    aligned.iloc[pos] = price
            if aligned.notna().sum() == 0:
                continue
            if self._marker_handles[name] is None:
                self._marker_handles[name] = fplt.plot(
                    aligned, ax=self.ax, color=color, style=style, width=2, legend=name)
            else:
                self._marker_handles[name].update_data(aligned)

    # --------------------------------------------------------- indicators
    def _indicator_series(self):
        """Compute all enabled indicators on the CURRENT display bars. The bars
        df is already display-tz indexed, so the returned series share the candle
        index — lines align exactly and are never shifted twice (jebakan b)."""
        return self.indicators.compute_all(self.sr.df, only_enabled=True)

    def _build_indicator_plots(self):
        out = self._indicator_series()
        for inst in self._overlay_insts:
            self._plot_instance(inst, self.ax, out.get(inst.instance_id, {}))
        for inst, sax in zip(self._sub_insts, self._sub_axes):
            self._plot_instance(inst, sax, out.get(inst.instance_id, {}))
            for lvl in _GUIDE_LEVELS.get(inst.name, ()):
                sax.addItem(pg.InfiniteLine(
                    pos=lvl, angle=0,
                    pen=pg.mkPen("#39415a", style=Qt.PenStyle.DashLine)))

    def _plot_instance(self, inst, ax, comp):
        first = True
        for p in inst.plots:
            if p.key in _SKIP_PLOT_KEYS:
                continue
            series = comp.get(p.key)
            if series is None:
                continue
            s = series.dropna()                    # jebakan a: drop warmup NaN
            if len(s) == 0:
                continue
            color = inst.colors.get(p.key, p.color)
            h = fplt.plot(s, ax=ax, color=color, width=max(p.width, 2),
                          legend=_legend_for(inst) if first else None)
            self._ind_handles[(inst.instance_id, p.key)] = h
            first = False

    def _update_indicators(self):
        if not self._ind_handles:
            return
        out = self._indicator_series()
        for (iid, key), h in self._ind_handles.items():
            comp = out.get(iid)
            if not comp or key not in comp:
                continue
            s = comp[key].dropna()
            if len(s):
                h.update_data(s)

    def _push_snapshot(self):
        cfg = self.risk_cfg
        snap = nocturna_apex_snapshot(
            self.sr.account, self.sr.df,
            daily_start_balance=self.daily_start_balance,
            daily_target_pct=cfg["daily_target_pct"],
            daily_stop_pct=cfg["daily_stop_pct"],
            equity_protector_pct=cfg["equity_protector_pct"],
            basket_target_pct=cfg["basket_target_pct"],
            news_filter=cfg.get("news_filter", False),
        )
        self.panel.update_snapshot(snap)
        return snap

    # -------------------------------------------------------- manual controls
    def _on_buy(self):
        self._manual_open(Side.BUY)

    def _on_sell(self):
        self._manual_open(Side.SELL)

    def _manual_open(self, side: Side):
        if self._halted:
            print("[apex] trading halted by risk trigger — manual entry ignored")
            return
        px, t = self.sr.cur_price, self.sr.cur_time
        if px is None:
            return
        lot = self.panel.get_lot()
        self.sr.account.open_market(side, lot, px, t)
        print(f"[apex] manual {side.value.upper()} {lot:.2f} @ {px:.2f} "
              f"| open={len(self.sr.account.positions)}")
        self._refresh_now()

    def _on_close_all(self):
        if not self.sr.account.positions:
            return
        self.sr.account.close_all(self.sr.cur_price, self.sr.cur_time)
        print(f"[apex] manual CLOSE ALL | open={len(self.sr.account.positions)}")
        self._refresh_now()

    def _on_close_partial(self):
        """Close `lot` (input field) from the newest layers first."""
        px, t = self.sr.cur_price, self.sr.cur_time
        remaining = self.panel.get_lot()
        closed = 0.0
        for p in list(reversed(self.sr.account.positions)):
            if remaining <= 1e-9:
                break
            take = min(remaining, p.lots)
            self.sr.account.close(p.ticket, px, t, lots=take)
            remaining -= take
            closed += take
        print(f"[apex] manual CLOSE PARTIAL {closed:.2f} lot "
              f"| open={len(self.sr.account.positions)}")
        self._refresh_now()

    def _refresh_now(self):
        """Immediately reflect a manual action on chart + panel, even if the
        frame timer hasn't ticked (e.g. while paused)."""
        self.sr._collect_markers()
        fr = self.sr._frame(self.sr.cur_price, self.sr.cur_time, done=self._done)
        self.candles.update_data(self.sr.df[["open", "close", "high", "low"]])
        self._draw_markers(fr)
        self._push_snapshot()

    def _set_halted(self, halted: bool):
        self._halted = halted
        self.sr.trading_halted = halted        # also gate the auto-strategy loop
        self.panel.btn_buy.setEnabled(not halted)
        self.panel.btn_sell.setEnabled(not halted)

    def _toggle_pause(self):
        """Freeze/resume the whole replay loop. Independent of the risk halt:
        pausing never clears a halt and a halt never blocks pausing (so the user
        can pause to inspect even while stopped out). PAUSE EA stays enabled
        regardless of halt state."""
        self._paused = not self._paused
        self.panel.set_pause_state(self._paused)
        print("[apex]", "PAUSED" if self._paused else "RESUMED",
              f"(halted={self._halted})")

    def _maybe_daily_reset(self, t):
        """New broker day (server-time midnight) → reset the daily risk envelope
        and release any risk halt, so one protector hit doesn't stop the whole
        replay. Also re-bases the equity-protector peak to today's opening equity
        (otherwise yesterday's drawdown would instantly re-halt)."""
        if t is None:
            return
        day = t.normalize()
        if self._cur_day is None:
            self._cur_day = day
            return
        if day <= self._cur_day:        # forward-only: a new day is always later
            return
        self._cur_day = day
        acc = self.sr.account
        self.daily_start_balance = acc.balance
        acc.max_equity = acc.equity(self.sr.cur_price)   # re-base protector peak
        if self._halted:
            self._set_halted(False)
            print(f"[apex] NEW DAY {day.date()} -> daily reset | "
                  f"start_balance={self.daily_start_balance:.2f} | halt released")


def _seed_demo_basket(sr: StrategyReplay):
    """Open a 5-layer BUY basket so the panel is fully populated (smoke/demo).

    No SL/TP: the basket persists through the smoke window so the BASKET section
    and live floating PnL are exercised visually.
    """
    mid = float(sr.df["close"].iloc[-1])
    t0 = sr.df.index[-1]
    for lot, below in [(0.10, 6.0), (0.15, 4.5), (0.22, 3.0),
                       (0.33, 1.5), (0.50, 0.0)]:
        sr.account.open_market(Side.BUY, lot, mid - below, t0)


def run_apex(parquet_dir="market_data", symbol="XAUUSD",
             start="2026-07-01 08:00", display_tf="M15",
             strategy=None, input_tz="WIB", display_tz="WIB",
             ticks_per_frame=200, interval=0.05, history_bars=400,
             balance=10_000, leverage=1000, spread=0.18, commission_per_lot=0.0,
             daily_target_pct=20.0, daily_stop_pct=3.0, equity_protector_pct=15.0,
             basket_target_pct=5.0, news_filter=False, max_layers=5,
             indicators=None,
             smoke=False, smoke_mode="manual", smoke_seconds=8.0,
             screenshot_path=None, shots_dir=None):
    app = pg.mkQApp()

    ds = TickDataset.load(parquet_dir, symbol=symbol)
    lo, hi = ds.span("WIB")
    print(f"[apex] data {lo} .. {hi} WIB | start={start} {input_tz} | tf={display_tf}")

    acc = Account(balance=balance, leverage=leverage, contract_size=100,
                  commission_per_lot=commission_per_lot, spread=spread)
    strat = (lambda ctx: None) if smoke else (strategy or _demo_strategy)
    sr = StrategyReplay(ds, acc, strat, start=start, display_tf=display_tf,
                        input_tz=input_tz, display_tz=display_tz,
                        ticks_per_frame=ticks_per_frame, history_bars=history_bars)

    risk_cfg = {"daily_target_pct": daily_target_pct, "daily_stop_pct": daily_stop_pct,
                "equity_protector_pct": equity_protector_pct,
                "basket_target_pct": basket_target_pct, "news_filter": news_filter,
                "max_layers": max_layers}

    if smoke and smoke_mode == "risk":
        _seed_demo_basket(sr)     # a losing basket that must auto-close on breach

    win = NocturnaApexWindow(sr, symbol, display_tf, display_tz, risk_cfg, interval,
                             indicators=indicators)
    win.resize(1500, 950)
    win.show()
    fplt.show(qt_exec=False)     # finplot refresh() within our own event loop
    win._push_snapshot()
    win.start()

    if smoke:
        base = shots_dir or (os.path.dirname(screenshot_path) if screenshot_path else ".")

        def shot(name, note=""):
            p = os.path.join(base, name)
            ok = win.grab().save(p)
            print(f"[apex] shot -> {p} (saved={ok}) {note}")

        if smoke_mode == "manual":
            # scripted click sequence exercising the wired buttons end-to-end
            def s_buy1():
                win.panel.lot_input.setValue(0.30)
                win.panel.btn_buy.click()

            def s_buy2():
                win.panel.lot_input.setValue(0.10)
                win.panel.btn_buy.click()

            def s_shot_buy():
                shot("b1_01_manual_buy.png", "expect BUY basket 2 layers / 0.40 lot, ACTIVE")

            def s_partial():
                win.panel.lot_input.setValue(0.15)
                win.panel.btn_close_partial.click()

            def s_shot_partial():
                shot("b1_02_after_partial.png", "expect 1 layer / 0.25 lot (partial shrink)")

            def s_closeall():
                win.panel.btn_close_all.click()

            def s_done():
                shot("b1_03_after_closeall.png", "expect FLAT + TRADING REST")
                print(f"[apex] manual final: open={len(acc.positions)} "
                      f"closed={len(acc.history)}")
                app.quit()

            for ms, fn in [(1200, s_buy1), (1500, s_buy2), (1900, s_shot_buy),
                           (2300, s_partial), (2700, s_shot_partial),
                           (3100, s_closeall), (3500, s_done)]:
                QTimer.singleShot(ms, fn)
        elif smoke_mode == "risk":
            def r_done():
                shot("b1_04_risk_autoclose.png",
                     "expect FLAT + TRADING REST after equity-protector close_all")
                print(f"[apex] risk final: halted={win._halted} "
                      f"open={len(acc.positions)} closed={len(acc.history)}")
                app.quit()
            QTimer.singleShot(int(smoke_seconds * 1000), r_done)

        elif smoke_mode == "pause":
            mem = {}

            def p_run():
                print(f"[apex] running   cur_time={sr.cur_time}")
                shot("b2_01_running.png", "loop advancing (PAUSE EA)")

            def p_pause():
                win._toggle_pause()

            def p_shot_a():
                mem["t"] = sr.cur_time
                print(f"[apex] paused@A  cur_time={sr.cur_time}")
                shot("b2_02_paused.png", "expect RESUME EA (amber), chart frozen")

            def p_shot_b():
                same = sr.cur_time == mem["t"]
                print(f"[apex] paused@B  cur_time={sr.cur_time} | frozen={same}")
                shot("b2_03_still_paused.png", "1.2s later, still frozen (identical)")

            def p_resume():
                win._toggle_pause()

            def p_done():
                print(f"[apex] resumed   cur_time={sr.cur_time}")
                shot("b2_04_resumed.png", "expect PAUSE EA, chart advanced")
                app.quit()

            for ms, fn in [(1500, p_run), (1600, p_pause), (1700, p_shot_a),
                           (2900, p_shot_b), (3000, p_resume), (4200, p_done)]:
                QTimer.singleShot(ms, fn)

        elif smoke_mode == "indicators":
            def ind_tips():
                out = win._indicator_series()
                bits = []
                for inst in win._overlay_insts + win._sub_insts:
                    comp = out.get(inst.instance_id, {})
                    for p in inst.plots:
                        if p.key in _SKIP_PLOT_KEYS:
                            continue
                        s = comp.get(p.key)
                        if s is not None and s.dropna().size:
                            bits.append(f"{_legend_for(inst)}={s.dropna().iloc[-1]:.2f}")
                            break
                return " ".join(bits)

            def ind_a():
                print(f"[apex] indicators@A cur_time={sr.cur_time} | {ind_tips()}")
                shot("wo2_01_indicators_a.png", "EMA9/EMA21 overlay + RSI subwindow")

            def ind_b():
                print(f"[apex] indicators@B cur_time={sr.cur_time} | {ind_tips()}")
                shot("wo2_02_indicators_b.png", "replay advanced -> indicator lines moved")
                app.quit()

            QTimer.singleShot(1500, ind_a)
            QTimer.singleShot(int(smoke_seconds * 1000), ind_b)

    app.exec()


if __name__ == "__main__":
    kwargs = {}
    if "--smoke" in sys.argv:
        kwargs["smoke"] = True
    for mode in ("risk", "pause", "indicators", "manual"):
        if f"--{mode}" in sys.argv:
            kwargs.update(smoke=True, smoke_mode=mode)
            break
    run_apex(**kwargs)
