import pandas as pd
t = pd.read_parquet("market_data/XAUUSD/ticks.parquet")
sp = t["ask"] - t["bid"]
print("Spread (dari data tick asli broker-mu):")
print(f"  rata-rata : {sp.mean():.3f}")
print(f"  median    : {sp.median():.3f}")
print(f"  p25 / p75 : {sp.quantile(0.25):.3f} / {sp.quantile(0.75):.3f}")
print(f"  p95 (news): {sp.quantile(0.95):.3f}")
print(f"  min / max : {sp.min():.3f} / {sp.max():.3f}")