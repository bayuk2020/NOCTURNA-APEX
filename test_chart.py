if __name__ == '__main__':
    from nocturna.replay import TickDataset, ReplaySession
    from nocturna.chart_replay import format_for_lwc
    from lightweight_charts import Chart

    ds = TickDataset.load("market_data", symbol="XAUUSD")
    sess = ReplaySession(ds, start="2026-07-01 08:00", timeframes=["M1"], input_tz="WIB")
    hist = format_for_lwc(sess.history["M1"].tail(200), "WIB")
    print(hist["time"].dtype, "| contoh:", hist["time"].iloc[0])  # harus object/string

    chart = Chart()
    chart.set(hist)
    chart.show(block=True)