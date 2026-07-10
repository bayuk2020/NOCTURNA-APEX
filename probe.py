from datetime import datetime
import MetaTrader5 as mt5
from nocturna.data.mt5_downloader import connect, download_all

connect()  # pakai terminal MT5 yang sudah login & jalan
info = mt5.symbol_info_tick("XAUUSD")
print("server time contoh:", datetime.utcfromtimestamp(info.time), "UTC-epoch")
import time as _t; print("jam lokalmu sekarang:", datetime.now())
# GANTI "XAUUSD" bila broker-mu pakai suffix, mis. "XAUUSDm", "XAUUSD.r", "GOLD"
rep = download_all(
    symbol="XAUUSD",
    date_from=datetime(2026, 4, 1),
    date_to=datetime(2026, 7, 10),   # 1 hari saja
    out_dir="market_data",
)
mt5.shutdown()
print("REPORT:", rep)