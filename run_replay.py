from nocturna.replay import TickDataset, ReplaySession

ds = TickDataset.load("market_data", symbol="XAUUSD")
print("Rentang data (WIB):", ds.span("WIB"))

# Pilih titik replay. Kamu ketik jam WIB (jam yang kamu lihat sehari-hari).
sess = ReplaySession(ds, start="2026-07-01 10:00",
                     timeframes=["M1", "M5", "M15"], input_tz="WIB")

print("History sampai 1 Juli 10:00 WIB:",
      "M1", len(sess.history["M1"]), "bar |",
      "M5", len(sess.history["M5"]), "bar")
print("Candle M5 terakhir sebelum replay:")
print(sess.history["M5"].tail(3))

# Putar maju 50.000 tick, lihat candle terbentuk live
print("\n--- REPLAY MULAI ---")
m1=m5=m15=ticks=0
for ev in sess.step():
    ticks += 1
    if "M1" in ev["closed"]:
        m1 += 1
        if m1 <= 3:  # tampilkan 3 candle M1 pertama yang terbentuk
            c = ev["closed"]["M1"]
            print(f"M1 close: {c['time']} O{c['open']:.2f} H{c['high']:.2f} L{c['low']:.2f} C{c['close']:.2f} vol{c['volume']}")
    m5 += "M5" in ev["closed"]
    m15 += "M15" in ev["closed"]
    if ticks >= 50000:
        break
print(f"\nSetelah {ticks} tick: M1 terbentuk {m1}, M5 {m5}, M15 {m15}")