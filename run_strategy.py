from nocturna.replay import TickDataset
from nocturna.engine.simulator import Account
from nocturna.engine.strategy_runner import StrategyRunner

ds = TickDataset.load("market_data", symbol="XAUUSD")
print("Data siap. Rentang WIB:", ds.span("WIB"))

acc = Account(balance=10_000, leverage=1000, contract_size=100,
              commission_per_lot=0.0, spread=0.18)

# strategi contoh: EMA cross murni (tanpa gate UT Bot yang terlalu ketat)
from nocturna.engine.simulator import Side
def ema_cross(ctx, fast=9, slow=21, sl=3.0, tp=6.0, lots=0.10):
    h = ctx.history
    if len(h) < slow + 2: return
    ef = h["close"].ewm(span=fast, adjust=False).mean()
    es = h["close"].ewm(span=slow, adjust=False).mean()
    up = ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]
    dn = ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]
    acc, px = ctx.account, ctx.price
    if acc.positions:
        p = acc.positions[0]
        if (p.side is Side.BUY and dn) or (p.side is Side.SELL and up):
            acc.close_all(px, ctx.time)
    if not acc.positions:
        if up:   acc.open_market(Side.BUY,  lots, px, ctx.time, sl=px-sl, tp=px+tp)
        elif dn: acc.open_market(Side.SELL, lots, px, ctx.time, sl=px+sl, tp=px-tp)

# UJI 1 HARI DULU: 1 Juli 08:00 -> 2 Juli 08:00 WIB
runner = StrategyRunner(ds, acc, ema_cross,
                        start="2026-07-01 08:00", end="2026-07-09 20:00",
                        strategy_tf="M5", input_tz="WIB", lookback=300)
res = runner.run(progress_every=200_000)

print("\n=== HASIL ===")
for k, v in res["stats"].items():
    print(f"  {k:20s}: {v}")