import pandas as pd
from pathlib import Path

p = Path("market_data/XAUUSD")
t = pd.read_parquet(p / "ticks.parquet")
t["time"] = pd.to_datetime(t["time"])

print("Total tick     :", f"{len(t):,}")
print("Rentang waktu  :", t["time"].min(), "->", t["time"].max())
print("Hari unik      :", t["time"].dt.date.nunique())
print("Duplikat waktu :", t.duplicated(subset=['time']).sum())
print("Bid nol/NaN    :", (t['bid'] <= 0).sum(), t['bid'].isna().sum())
print("\nContoh 3 baris awal:")
print(t.head(3).to_string())

# rekonstruksi M1 dari tick, bandingkan jumlahnya dengan M5 broker
import sys; sys.path.insert(0, ".")
from nocturna.data.store import ticks_to_ohlc
m1 = ticks_to_ohlc(t.rename(columns={}), "M1")
print("\nM1 hasil rekonstruksi dari tick:", f"{len(m1):,} bar")
m5 = pd.read_parquet(p / "M5.parquet")
print("M5 broker                      :", f"{len(m5):,} bar")
print("Rasio M1/M5 (harusnya ~5)      :", round(len(m1)/max(len(m5),1), 2))