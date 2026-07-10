# NOCTURNA-APEX — Project Handoff / Status

Baca file ini dulu untuk memahami di mana proyek berada. Semua modul di bawah
SUDAH JALAN dan TERUJI di data nyata (29 juta tick XAUUSD dari Exness, 1 Apr–9 Jul 2026).

## Lingkungan (penting)
- OS: Windows. Python 3.14 (`C:\Python314`).
- MetaTrader5 5.0.5735 TERPASANG & BISA KONEK (broker Exness, akun demo).
- pandas 3.0, numpy 2.5. GUI chart: **finplot + PyQt6** (BUKAN lightweight-charts —
  lightweight-charts v2.1 GAGAL di stack ini karena webview/multiprocessing +
  dtype datetime64[us]. Sudah dibuang. Jangan pakai lagi.)
- Data tersimpan di `market_data/XAUUSD/` sebagai Parquet: ticks.parquet (~29jt baris),
  M1..D1.parquet. CATATAN: M1.parquet dari broker = 0 baris (batas copy_rates); ini
  TIDAK MASALAH — semua candle direkonstruksi dari ticks.parquet.

## Fakta kalibrasi broker (dari data nyata)
- Simbol: `XAUUSD`. Contract size 100. Leverage akun = **1000**.
- Spread median nyata = **0.18** (dari ask-bid seluruh tick). Max 1.45 saat news.
- Commission: diasumsikan **0** (akun Standard Exness). Belum 100% dikonfirmasi via History.
- Waktu tick = SERVER broker = **UTC+3**. User berpikir dalam **WIB = UTC+7**.
  Konversi: server = WIB - 4 jam. Semua modul sudah handle ini (input_tz/display_tz).

## Arsitektur (data mengalir satu arah)
```
MT5 -> data/mt5_downloader.py -> Parquet
         data/store.py: ticks_to_ohlc / resample_ohlc (candle dari tick, wick terjaga)
         replay.py: TickDataset, ReplaySession, LiveCandleBuilder (replay per-detik)
         indicators/base.py + library.py: engine plugin (EMA,SMA,RSI,ATR,MACD,BB,VWAP,Supertrend,UTBot)
         engine/simulator.py: Account/Position (margin,commission,swap,SL,TP,trailing,partial,basket)
         engine/backtest.py + engine/strategy_runner.py: backtest + stats jujur (expectancy,PF,maxDD)
         strategy_replay.py: StrategyReplay (candle+strategi+marker dalam 1 aliran tick) + finplot GUI
         chart_replay_fp.py: replay chart bergerak (finplot, SPACE pause, Up/Down speed)
         dashboard.py: nocturna_apex_snapshot (4 panel) + check_risk_triggers + ADX
         apex_panel.py: DashboardPanel PyQt6 (view read-only 4 seksi, update_snapshot)
         apex_app.py: NocturnaApexWindow (finplot embed + panel + drive replay) [TAHAP A]
```

## Cara embed finplot ke PyQt6 (dipakai apex_app.py — penting untuk TAHAP B/C)
finplot 1.9.7: kalau `master` yang dikirim ke `create_plot_widget()` BUKAN
GraphicsLayoutWidget (mis. QMainWindow kita), finplot bikin `pg.PlotWidget` per
axis = `ax.ax_widget` → itu yang kita masukkan ke layout Qt. WAJIB set
`win.axs = [ax]` sendiri (finplot refresh()/autoscale membacanya). Jalankan event
loop sendiri: `win.show(); fplt.show(qt_exec=False); app.exec()`.

## Yang SUDAH jalan & teruji
- Ambil & simpan 29jt tick + semua timeframe.
- Rekonstruksi candle akurat dari tick (rasio M1/M5 = 4.99, terverifikasi).
- Replay per-detik dari tanggal/jam pilihan (WIB), candle terbentuk live.
- Backtest strategi otomatis + stats (SL/TP dievaluasi PER-TICK, bukan per-bar).
- Chart bergerak finplot + marker entry(▲▼)/exit(×) + judul equity real-time.
- dashboard.py MENGHITUNG 4 panel + risk triggers (tapi BELUM tampil visual).
- TAHAP A SELESAI & TERUJI (2026-07-10): apex_app.py + apex_panel.py. Jendela PyQt6 =
  chart finplot bergerak (kiri) + panel NOCTURNA-APEX read-only (kanan, 4 seksi +
  status header + tombol non-aktif). Update real-time tiap frame dari
  nocturna_apex_snapshot. Terverifikasi via `--smoke` (seed basket 5 layer, screenshot,
  auto-quit): status ACTIVE/REST, badge risk ✔/✘, floating PnL live, semua field benar.

## Yang BELUM ada (langkah berikutnya, urut prioritas)
Target akhir: UI seperti `designUI.png` (dashboard NOCTURNA-APEX di kanan chart).
Keputusan arsitektur: **PyQt6 membungkus chart finplot + panel dashboard** (bukan web,
supaya satu proses & mudah debug; finplot sudah berbasis PyQt6 jadi bisa disematkan).

- TAHAP B sub-langkah 1 SELESAI & TERUJI (2026-07-10):
  * apex_panel.py: seksi MANUAL TRADE (input Lot QDoubleSpinBox default 0.10 +
    tombol BUY/SELL/CLOSE PARTIAL/CLOSE ALL aktif). get_lot() expose nilai lot.
  * apex_app.py: tombol ter-connect ke account.open_market(BUY/SELL, lot, cur_price,
    cur_time) / close (partial, newest-first) / close_all. Entry pakai harga tick
    terkini (StrategyReplay.cur_price/cur_time — server time, jangan di-shift).
    _refresh_now() bikin aksi manual langsung tampil (chart+panel) walau timer belum tick.
  * RISK NYATA: _apply_risk_triggers() jalan tiap frame — check_risk_triggers() yang
    balikin CLOSE_ALL_DAILY_STOP / CLOSE_ALL_EQUITY_PROTECTOR MEMANGGIL close_all()
    beneran + halt (disable BUY/SELL). Terverifikasi via smoke: equity-protector breach
    -> 5 posisi ditutup otomatis, status -> REST, balance benar-benar turun.
  * Smoke: `run_apex(smoke=True, smoke_mode='manual'|'risk', shots_dir=...)`.
  * CLOSE BASKET terpisah blm ada (skrg CLOSE ALL = tutup semua; basket-specific nanti).
- TAHAP B sub-langkah 2 Bagian 1 SELESAI & TERUJI (2026-07-10) — tambal proteksi:
  * Halt sekarang JUGA gate strategi otomatis: StrategyReplay.trading_halted; frames()
    skip self.strategy(ctx) saat True (indikator tetap warm). _set_halted() set flag ini
    + disable BUY/SELL. Mencegah loop-maut buka-tutup setelah risk-stop.
  * Reset harian: window simpan daily_start_balance + _cur_day (tanggal server).
    _maybe_daily_reset(t) dipanggil tiap _tick; saat tanggal server MAJU (forward-only):
    daily_start_balance = balance skrg, acc.max_equity di-rebase ke equity skrg (kalau
    tidak, drawdown kemarin langsung re-halt), dan halt DILEPAS. Snapshot pakai
    self.daily_start_balance (bukan initial_balance lagi).
  * Terverifikasi deterministik (scratchpad/test_halt_protection.py, ALL PASS): strategi
    aktif -> forced protector breach -> close_all+halt -> 50 frame TIDAK buka posisi lagi
    -> new day reset (start_balance 10000->10027.25, halt lepas) -> strategi jalan lagi.
- TAHAP B sub-langkah 2 Bagian 2 SELESAI & TERUJI (2026-07-10) — Pause EA + SPACE:
  * window._paused (INDEPENDEN dari _halted). _tick() cek `if self._done or self._paused:
    return` di AWAL frame — QTimer terus jalan tapi no-op saat pause (tak ada stop/start
    timer di tengah frame, jadi tak ada race klik/keypress vs timer).
  * _toggle_pause() dipicu tombol PAUSE EA + QShortcut SPACE (WindowShortcut, jadi fire
    walau chart yang fokus, dan cuma 1 handler yang jalan). Tak pernah menyentuh _halted.
  * apex_panel.set_pause_state(): tombol toggle label PAUSE EA <-> RESUME EA (amber saat
    paused). btn_pause SELALU enabled (bisa ditekan walau halted, untuk inspeksi).
  * Terverifikasi: test_pause.py (ALL PASS) — freeze (cur_time & panel beku), resume mulus,
    label toggle, pause bisa saat halted & halt tak lepas, SPACE (QTest.keyClick) toggle.
    Plus smoke visual `smoke_mode='pause'`: cur_time 04:06:33 beku 1.2s lalu lanjut 04:14:15.
- TAHAP C: Martingale/grid/trailing/multiple-position + tabel history transaksi.
- TAHAP D: Equity curve, drawdown chart, pie win/loss (polesan).

## URUTAN PENGERJAAN YANG DISEPAKATI (fix — jangan diacak)
Prinsip: dahulukan yang dibutuhkan strategi untuk DIEKSEKUSI & DILIHAT; tunda yang
berat tapi tidak memblokir pengujian. Kerjakan SATU nomor per tugas, verifikasi,
baru lanjut (jangan tumpuk beberapa nomor sekaligus — sumber bug GUI bercampur).

  1. Pause EA + hotkey SPACE                    <- SELESAI (2026-07-10)
  2. Indikator TAMPIL di chart (EMA/UTBot overlay, RSI/MACD subwindow).  <- BERIKUTNYA
     Engine indikator sudah ada; ini murni menggambar ke chart finplot + update live.
  3. Pending order di UI (Buy/Sell Stop, Buy/Sell Limit) — simulator perlu matching
     pending vs bar high/low (match_pending sudah ada di engine/backtest.py).
  4. Martingale/grid otomatis + TABEL HISTORY transaksi (basket multi-layer + log).
  5. Manajemen indikator dari UI (add/edit/hapus/warna/multi-instance, ala TradingView)
     <- SENGAJA DITUNDA: paling kompleks (dialog+list+colorpicker), TIDAK memblokir
        pengujian strategi karena indikator sudah bisa diatur lewat kode. Kenyamanan,
        bukan syarat.
  6. Equity curve + drawdown chart + pie win/loss (polesan visual TAHAP D).
  --> lalu #7: SWEEP/OPTIMIZER strategi (walk-forward, peringkat by expectancy+maxDD).
      Inti tujuan user; dikerjakan setelah wadah UI (#1-6) lengkap.

Catatan urutan asli user adalah 1..7 linear; disepakati diubah jadi urutan di atas
(indikator-tampil & pending & martingale didahulukan; UI-indikator-canggih ditunda).

## Catatan strategi
- Strategi contoh EMA-cross (di strategy_replay._demo_strategy) EXIT TERLALU CEPAT
  karena keluar saat cross berlawanan. User ingin tahan lebih lama: hapus blok
  exit-on-cross, biarkan HANYA SL/TP (atau trailing) yang menutup.
- Tujuan akhir user: sweep banyak kombinasi parameter di 3 bulan, peringkat by
  EXPECTANCY + MAX DRAWDOWN (bukan winrate — winrate menyesatkan, sudah dibuktikan:
  strategi menang di 1 hari ternyata rugi di 8 hari = overfitting).

## Cara jalanin yang sudah ada
```
python -m nocturna.apex_app                 # TAHAP A: chart + dashboard NOCTURNA-APEX
python -m nocturna.apex_app --smoke          # self-test: seed basket, screenshot, quit
python -m nocturna.strategy_replay          # chart bergerak + marker strategi
python -m nocturna.chart_replay_fp          # chart bergerak saja
python -m nocturna.demo_run                 # smoke test core (data sintetis)
```

## Sisa bug kecil (tidak fatal)
- Warning "All-NaN slice" dari pyqtgraph ScatterPlotItem saat marker sedikit —
  candle & marker tetap tampil. Bisa dibungkam nanti.
- DPI warning Windows ("SetProcessDpiAwarenessContext failed") — abaikan.