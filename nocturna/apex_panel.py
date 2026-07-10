"""NOCTURNA-APEX read-only dashboard panel (TAHAP A).

A pure *view*: it renders the dict produced by
``dashboard.nocturna_apex_snapshot(account, bars_df, ...)`` into the 4 sections
shown in designUI.png — Account / Basket / Market Condition / Risk Management —
plus a status header. No business logic lives here; every number comes straight
from the snapshot so "what you see is what you test".

TAHAP A is read-only. The action buttons at the bottom are present for visual
parity with the design but are disabled (wired up in TAHAP B).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

# ---- palette (dark, matches designUI.png) ----
BG          = "#0a0e17"
CARD        = "#0d1220"
BORDER      = "#1b2438"
TITLE_GREEN = "#3fd07a"
HEADER      = "#6f7fa6"
LABEL       = "#8b98b8"
VALUE       = "#e8edf7"
GREEN       = "#2ecc71"
RED         = "#ff5b5b"
BLUE        = "#3b82f6"
AMBER       = "#f5a623"
GREY        = "#9aa7c2"


def _money(v: float, signed: bool = False) -> str:
    if v is None:
        return "—"
    sign = ""
    if signed:
        sign = "+" if v >= 0 else "-"
        v = abs(v)
    return f"{sign}${v:,.2f}"


def _span(text, color) -> str:
    return f'<span style="color:{color}">{text}</span>'


def _pnl_color(v) -> str:
    if v is None or abs(v) < 1e-9:
        return VALUE
    return GREEN if v > 0 else RED


class _Section(QFrame):
    """A titled card with label/value rows. Values are set via HTML so a single
    label can hold multiple colors (e.g. '+$504.32 (5.04%)  ✔')."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(6)

        head = QLabel(title.upper())
        head.setObjectName("sectionHeader")
        outer.addWidget(head)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(5)
        self._grid.setColumnStretch(0, 1)
        outer.addLayout(self._grid)

        self._rows: dict[str, QLabel] = {}

    def add_row(self, key: str, label_text: str) -> None:
        r = self._grid.rowCount()
        lab = QLabel(label_text)
        lab.setObjectName("rowLabel")
        val = QLabel("—")
        val.setObjectName("rowValue")
        val.setTextFormat(Qt.TextFormat.RichText)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._grid.addWidget(lab, r, 0)
        self._grid.addWidget(val, r, 1)
        self._rows[key] = val

    def set_label(self, key: str, text: str) -> None:
        # allow updating a row's left-hand caption (e.g. dynamic % in the label)
        idx = list(self._rows).index(key)
        item = self._grid.itemAtPosition(idx, 0)
        if item and item.widget():
            item.widget().setText(text)

    def set(self, key: str, html: str) -> None:
        self._rows[key].setText(html)


class DashboardPanel(QWidget):
    """The full NOCTURNA-APEX side panel. Call :meth:`update_snapshot` each frame."""

    def __init__(self, config: dict | None = None, tz_shift_hours: int = 0):
        super().__init__()
        self.setObjectName("apexPanel")
        self.tz_shift_hours = tz_shift_hours
        cfg = config or {}
        self.cfg = {
            "daily_target_pct": cfg.get("daily_target_pct", 20.0),
            "daily_stop_pct": cfg.get("daily_stop_pct", 3.0),
            "equity_protector_pct": cfg.get("equity_protector_pct", 15.0),
            "basket_target_pct": cfg.get("basket_target_pct", 5.0),
            "max_layers": cfg.get("max_layers", 5),
        }

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setFixedWidth(340)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ---- title ----
        title = QLabel()
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setText(f'{_span("NOCTURNA-APEX v2", TITLE_GREEN)} '
                      f'{_span("DASHBOARD", VALUE)}')
        title.setObjectName("panelTitle")
        root.addWidget(title)

        # ---- status ----
        status_card = QFrame()
        status_card.setObjectName("card")
        sc = QVBoxLayout(status_card)
        sc.setContentsMargins(12, 8, 12, 10)
        sc.setSpacing(2)
        sh = QLabel("STATUS")
        sh.setObjectName("sectionHeader")
        sc.addWidget(sh)
        self.status_label = QLabel("—")
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setObjectName("statusValue")
        sc.addWidget(self.status_label)
        root.addWidget(status_card)

        # ---- sections ----
        self.sec_account = _Section("Account Information")
        for key, lab in [
            ("balance", "Balance"),
            ("equity", "Equity"),
            ("floating", "Floating PnL"),
            ("daily_start", "Daily Start Balance"),
            ("daily_realized", "Daily Realized PnL"),
            ("daily_target", f"Daily Target ({self.cfg['daily_target_pct']:.0f}%)"),
            ("daily_stop", f"Daily Realized Stop ({self.cfg['daily_stop_pct']:.0f}%)"),
            ("equity_protector", f"Equity Protector ({self.cfg['equity_protector_pct']:.0f}%)"),
            ("status_daily", "Status Daily"),
        ]:
            self.sec_account.add_row(key, lab)
        root.addWidget(self.sec_account)

        self.sec_basket = _Section("Basket Information")
        for key, lab in [
            ("direction", "Direction"),
            ("open_time", "Open Time"),
            ("layers", "Total Layers"),
            ("total_lot", "Total Lot"),
            ("avg_price", "Average Price"),
            ("current_price", "Current Price"),
            ("basket_pnl", "Basket PnL"),
            ("basket_target", "Basket Target"),
        ]:
            self.sec_basket.add_row(key, lab)
        root.addWidget(self.sec_basket)

        self.sec_market = _Section("Market Condition")
        for key, lab in [
            ("trend", "Trend"),
            ("state", "Market State"),
            ("volatility", "Volatility"),
            ("adx", "ADX (14)"),
            ("atr", "ATR (14)"),
            ("spread", "Spread"),
            ("news", "News Filter"),
        ]:
            self.sec_market.add_row(key, lab)
        root.addWidget(self.sec_market)

        self.sec_risk = _Section("Risk Management")
        for key, lab in [
            ("daily_target", "Daily Target"),
            ("daily_stop", "Daily Realized Stop"),
            ("equity_protector", "Equity Protector"),
            ("margin_level", "Margin Level"),
        ]:
            self.sec_risk.add_row(key, lab)
        root.addWidget(self.sec_risk)

        # ---- manual trade controls (TAHAP B sub-langkah 1) ----
        trade_card = QFrame()
        trade_card.setObjectName("card")
        tc = QVBoxLayout(trade_card)
        tc.setContentsMargins(12, 10, 12, 12)
        tc.setSpacing(6)
        th = QLabel("MANUAL TRADE")
        th.setObjectName("sectionHeader")
        tc.addWidget(th)

        lot_row = QHBoxLayout()
        lot_lab = QLabel("Lot")
        lot_lab.setObjectName("rowLabel")
        self.lot_input = QDoubleSpinBox()
        self.lot_input.setObjectName("lotInput")
        self.lot_input.setDecimals(2)
        self.lot_input.setSingleStep(0.01)
        self.lot_input.setRange(0.01, 100.0)
        self.lot_input.setValue(0.10)
        lot_row.addWidget(lot_lab)
        lot_row.addStretch(1)
        lot_row.addWidget(self.lot_input)
        tc.addLayout(lot_row)

        bs_row = QHBoxLayout()
        self.btn_buy = QPushButton("BUY")
        self.btn_buy.setObjectName("btnBuy")
        self.btn_sell = QPushButton("SELL")
        self.btn_sell.setObjectName("btnSell")
        bs_row.addWidget(self.btn_buy)
        bs_row.addWidget(self.btn_sell)
        tc.addLayout(bs_row)

        cl_row = QHBoxLayout()
        self.btn_close_partial = QPushButton("CLOSE PARTIAL")
        self.btn_close_partial.setObjectName("btnGrey")
        self.btn_close_all = QPushButton("CLOSE ALL")
        self.btn_close_all.setObjectName("btnRed")
        cl_row.addWidget(self.btn_close_partial)
        cl_row.addWidget(self.btn_close_all)
        tc.addLayout(cl_row)
        root.addWidget(trade_card)

        # ---- EA controls ----
        ea_row = QHBoxLayout()
        self.btn_pause = QPushButton("PAUSE EA")
        self.btn_pause.setObjectName("btnPause")
        self.btn_pause.setToolTip("Pause/resume replay (hotkey: SPACE)")
        self.btn_settings = QPushButton("SETTINGS")
        self.btn_settings.setObjectName("btnGrey")
        ea_row.addWidget(self.btn_pause)
        ea_row.addWidget(self.btn_settings)
        root.addLayout(ea_row)

        # entry/close + PAUSE EA live now (sub-langkah 1 & 2). SETTINGS -> later.
        for b in (self.btn_buy, self.btn_sell, self.btn_close_partial,
                  self.btn_close_all, self.btn_pause):
            b.setEnabled(True)
        self.btn_settings.setEnabled(False)
        self.btn_settings.setToolTip("Belum diaktifkan")

        footer = QLabel("NOCTURNA-APEX v2 – Precision. Discipline. Execution.")
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)

        root.addStretch(1)
        self.setStyleSheet(self._qss())

    # ---------------------------------------------------------------- update
    def update_snapshot(self, snap: dict) -> None:
        """Render one snapshot dict. Safe to call every replay frame."""
        a = snap["account"]
        b = snap["basket"]
        m = snap["market"]
        r = snap["risk"]

        # ---- status ----
        active = snap["status"].lower().startswith("trading active")
        if active:
            self.status_label.setText(_span("● TRADING ACTIVE", GREEN))
        else:
            self.status_label.setText(_span("● TRADING REST", RED))

        # ---- account ----
        ds = a["daily_start_balance"]
        self.sec_account.set("balance", _money(a["balance"]))
        self.sec_account.set("equity", _money(a["equity"]))
        self.sec_account.set("floating",
                             _span(_money(a["floating_pnl"], signed=True),
                                   _pnl_color(a["floating_pnl"])))
        self.sec_account.set("daily_start", _money(ds))
        dr = a["daily_realized_pnl"]
        dr_pct = (dr / ds * 100) if ds else 0.0
        self.sec_account.set("daily_realized",
                             _span(f"{_money(dr, signed=True)} ({dr_pct:+.2f}%)",
                                   _pnl_color(dr)))
        self.sec_account.set("daily_target",
                             _money(ds * a["daily_target_pct"] / 100))
        self.sec_account.set("daily_stop",
                             _money(ds * a["daily_stop_pct"] / 100))
        self.sec_account.set("equity_protector",
                             _money(ds * a["equity_protector_pct"] / 100))
        is_profit = a["daily_status"].lower() == "profit"
        self.sec_account.set("status_daily",
                             _span("PROFIT" if is_profit else "LOSS",
                                   GREEN if is_profit else RED))

        # ---- basket ----
        has_pos = bool(b["layers"])
        if has_pos:
            direction = (b["direction"] or "").upper()
            self.sec_basket.set("direction",
                                _span(direction, BLUE if direction == "BUY" else RED))
            self.sec_basket.set("open_time", self._fmt_time(b.get("open_time")))
            self.sec_basket.set("layers", f"{b['layers']} / {self.cfg['max_layers']}")
            self.sec_basket.set("total_lot", f"{b['total_lot']:.2f}")
            self.sec_basket.set("avg_price", f"{b['avg_price']:.2f}"
                                if b["avg_price"] is not None else "—")
            self.sec_basket.set("current_price", f"{b.get('current_price'):.2f}"
                                if b.get("current_price") is not None else "—")
            pnl = b["pnl"]
            pnl_pct = b.get("pnl_pct")
            pct_txt = f" ({pnl_pct:+.2f}%)" if pnl_pct is not None else ""
            self.sec_basket.set("basket_pnl",
                                _span(f"{_money(pnl, signed=True)}{pct_txt}",
                                      _pnl_color(pnl)))
            tgt = f"{b['target_pct']:.2f}%"
            if b.get("hit"):
                tgt += " " + _span("(Hit)", GREEN)
            self.sec_basket.set("basket_target", tgt)
        else:
            self.sec_basket.set("direction", _span("FLAT", GREY))
            for key in ("open_time", "avg_price", "current_price"):
                self.sec_basket.set(key, "—")
            self.sec_basket.set("layers", f"0 / {self.cfg['max_layers']}")
            self.sec_basket.set("total_lot", "0.00")
            self.sec_basket.set("basket_pnl", _money(0.0, signed=True))
            self.sec_basket.set("basket_target", f"{b['target_pct']:.2f}%")

        # ---- market ----
        trend = str(m["trend"]).upper()
        trend_col = (GREEN if trend == "BULLISH" else
                     RED if trend == "BEARISH" else GREY)
        self.sec_market.set("trend", _span(trend, trend_col))
        self.sec_market.set("state", _span(str(m["state"]).upper(), VALUE))
        vol = str(m["volatility"]).upper()
        vol_col = AMBER if vol == "HIGH" else VALUE if vol == "NORMAL" else GREY
        self.sec_market.set("volatility", _span(vol, vol_col))
        self.sec_market.set("adx", "—" if m["adx"] is None else f"{m['adx']:.2f}")
        self.sec_market.set("atr", "—" if m["atr"] is None else f"{m['atr']:.2f}")
        self.sec_market.set("spread", f"{round(m['spread'] * 100)} points")
        news_on = bool(m["news_filter"])
        self.sec_market.set("news",
                            _span("BLOCKED", RED) if news_on else _span("CLEAR", GREEN))

        # ---- risk (value + ✔/✘ badge) ----
        realized_pct = dr_pct
        drawdown_pct = float(r["equity_protector"].split("%")[0])
        self.sec_risk.set("daily_target",
                          self._risk_row(r["daily_target"], ok=True))
        self.sec_risk.set("daily_stop",
                          self._risk_row(r["daily_stop"],
                                         ok=realized_pct > -a["daily_stop_pct"]))
        self.sec_risk.set("equity_protector",
                          self._risk_row(r["equity_protector"],
                                         ok=drawdown_pct < a["equity_protector_pct"]))
        ml = r["margin_level"]
        ml_txt = "—" if ml is None else f"{ml:.0f}%"
        self.sec_risk.set("margin_level",
                          self._risk_row(ml_txt, ok=(ml is None or ml > 100)))

    def get_lot(self) -> float:
        """Lot size from the input field (for manual BUY/SELL/partial-close)."""
        return round(float(self.lot_input.value()), 2)

    def set_pause_state(self, paused: bool) -> None:
        """Reflect replay loop state on the PAUSE EA button (label + colour)."""
        self.btn_pause.setText("RESUME EA" if paused else "PAUSE EA")
        self.btn_pause.setProperty("paused", "true" if paused else "false")
        self.btn_pause.style().unpolish(self.btn_pause)
        self.btn_pause.style().polish(self.btn_pause)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _risk_row(text: str, ok: bool) -> str:
        badge = _span("✔", GREEN) if ok else _span("✘", RED)
        return f'{_span(text, VALUE)}  {badge}'

    def _fmt_time(self, ts) -> str:
        if ts is None:
            return "—"
        try:
            import pandas as pd
            t = pd.Timestamp(ts) + pd.Timedelta(hours=self.tz_shift_hours)
            return t.strftime("%d %b %H:%M")
        except Exception:
            return str(ts)

    # ---------------------------------------------------------------- style
    def _qss(self) -> str:
        return f"""
        QWidget#apexPanel {{ background:{BG}; }}
        QLabel {{ color:{VALUE}; font-size:12px; }}
        QLabel#panelTitle {{ font-size:15px; font-weight:700; padding:2px 2px 4px; }}
        QFrame#card {{ background:{CARD}; border:1px solid {BORDER};
                       border-radius:8px; }}
        QLabel#sectionHeader {{ color:{HEADER}; font-size:10px; font-weight:700;
                                letter-spacing:1px; }}
        QLabel#rowLabel {{ color:{LABEL}; font-size:12px; }}
        QLabel#rowValue {{ color:{VALUE}; font-size:12px; font-weight:600; }}
        QLabel#statusValue {{ font-size:15px; font-weight:800; padding:2px; }}
        QLabel#footer {{ color:{HEADER}; font-size:10px; padding-top:4px; }}
        QPushButton {{ color:#ffffff; font-size:11px; font-weight:700;
                       border:none; border-radius:6px; padding:9px 4px; }}
        QPushButton:disabled {{ background:#182136; color:#59657f; }}
        QPushButton#btnBuy   {{ background:#2563eb; }}
        QPushButton#btnBuy:hover  {{ background:#3b82f6; }}
        QPushButton#btnSell  {{ background:#dc2626; }}
        QPushButton#btnSell:hover {{ background:#ef4444; }}
        QPushButton#btnBlue  {{ background:#2563eb; }}
        QPushButton#btnRed   {{ background:#dc2626; }}
        QPushButton#btnRed:hover  {{ background:#ef4444; }}
        QPushButton#btnGrey  {{ background:#25304a; }}
        QPushButton#btnGrey:hover {{ background:#2f3c5c; }}
        QPushButton#btnPause {{ background:#25304a; }}
        QPushButton#btnPause:hover {{ background:#2f3c5c; }}
        QPushButton#btnPause[paused="true"] {{ background:{AMBER}; color:#0a0e17; }}
        QDoubleSpinBox#lotInput {{ background:{BG}; color:{VALUE};
                       border:1px solid {BORDER}; border-radius:4px;
                       padding:3px 6px; min-width:78px; font-weight:600; }}
        QDoubleSpinBox#lotInput::up-button, QDoubleSpinBox#lotInput::down-button {{
                       width:14px; background:{CARD}; border:none; }}
        """
