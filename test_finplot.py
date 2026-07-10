if __name__ == '__main__':
    import pandas as pd
    import finplot as fplt
    from nocturna.replay import TickDataset, ReplaySession

    ds = TickDataset.load("market_data", symbol="XAUUSD")
    sess = ReplaySession(ds, start="2026-07-01 08:00", timeframes=["M1"], input_tz="WIB")
    df = sess.history["M1"].tail(300).copy()
    df.index = df.index + pd.Timedelta(hours=4)  # server -> WIB

    ax = fplt.create_plot("XAUUSD M1 — test", init_zoom_periods=100)
    fplt.candlestick_ochl(df[["open", "close", "high", "low"]], ax=ax)
    fplt.show()