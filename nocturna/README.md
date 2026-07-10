# NOCTURNA-APEX — XAUUSD backtesting & replay engine (Python + MT5)

A Python foundation for replaying and backtesting XAUUSD, with a plugin indicator
engine (no Pine Script) and an MT5-like trading simulator. This is **Phase 1 core**,
not the finished TradingView clone — see status + roadmap below.

## What actually runs today (tested on synthetic data in-repo)

| Component | File | Status |
|---|---|---|
| Ticks → OHLC (M1..D1) + resample, wicks preserved | `data/store.py` | ✅ Tested |
| Indicator engine (plugin, create/delete/modify/enable/multi-instance) | `indicators/base.py` | ✅ Tested |
| Indicators: SMA EMA RSI ATR MACD Bollinger VWAP Supertrend UT Bot | `indicators/library.py` | ✅ Tested |
| MT5-like simulator (margin/commission/swap/SL/TP/trailing/partial/basket) | `engine/simulator.py` | ✅ Tested |
| Backtest/replay loop + stats (expectancy, PF, max DD — not just winrate) | `engine/backtest.py` | ✅ Tested |
| NOCTURNA-APEX dashboard (4 panels + ADX/ATR + risk triggers) | `dashboard.py` | ✅ Tested |
| MT5 downloader (ticks + all TFs → Parquet) | `data/mt5_downloader.py` | ⚠️ Correct, **Windows/MT5 only — not runnable in sandbox** |
| TradingView-like chart window | `chart_app.py` | ⚠️ Correct, **needs `lightweight-charts` + display — untested here** |

Run the proof:
```bash
python -m nocturna.demo_run      # synthetic ticks → bars → indicators → backtest → dashboard
```

## Real data (Windows + MT5 running & logged in)
```bash
pip install MetaTrader5 pandas pyarrow lightweight-charts
python -m nocturna.data.mt5_downloader     # 2026-04-01 → now, XAUUSD → market_data/
python -m nocturna.chart_app               # chart the M5 parquet
```

## Architecture (data flows one way, no lookahead)
```
MT5 terminal ──copy_ticks_range/copy_rates_range──► mt5_downloader ──► Parquet
                                                                        │
                          store.ticks_to_ohlc / resample_ohlc ◄─────────┘
                                                                        │
          ┌───────────────────────────────────────────────────────────┤
          ▼                                ▼                            ▼
  IndicatorRegistry (plugins)      backtest.run_backtest         chart_app (lightweight-charts)
   EMA/RSI/UTBot/... instances  ──► Context ──► Strategy(ctx)  ──► Account (simulator)
                                                                        │
                                                     dashboard.nocturna_apex_snapshot
```
Same registry feeds both the chart and the backtester → **what you see is what you test.**

## Writing a strategy (this replaces Pine Script)
A strategy is a Python function `fn(ctx)`. `ctx.history` is all bars up to now
(no future data), `ctx.account` is the simulator. Open/close via `ctx.account`.
See `demo_run.py` for an EMA-cross + UT Bot example.

## Adding an indicator plugin
Drop a `.py` in a folder, subclass `Indicator`, then
`registry.load_plugins("my_plugins/")`. That's the whole plugin system.

## Roadmap (deliberately phased — do not build all at once)
- **P1 (done):** data layer, indicator engine, simulator, backtest loop, dashboard data.
- **P2:** replay UI (datetime picker + play/pause/speed) wired to `chart_app`;
  pending orders (Buy/Sell Stop/Limit) matched in the loop; wire `check_risk_triggers`
  into the loop for auto daily-stop / equity-protector / target-hit.
- **P3:** GUI shell (PyQt/PySide) hosting chart + NOCTURNA-APEX panel + Buy/Sell/
  Close/Pause buttons; indicator manager dialog (add/edit/delete/colors).
- **P4:** strategy optimizer with **walk-forward / out-of-sample split** (guards
  against the overfitting trap); parameter sweeps ranked by expectancy + max DD.
- **P5 (Feature 2 — real money):** live MT5 feed + auto-execution. Gate behind
  out-of-sample validation. **Winrate is NOT the selection metric.**

## Known limits / honesty
- Volume is **tick volume**, not real traded volume (XAUUSD CFD).
- Intrabar SL/TP fill assumes SL-before-TP when both are in a bar's range (conservative).
- Indicators recompute on full history each bar in the loop (correct, not yet optimized).
- MT5 tick 'time' is broker server time, not UTC — normalize before cross-source joins.
- Deep tick history to 2026-04-01 depends on your broker's retention.
```
