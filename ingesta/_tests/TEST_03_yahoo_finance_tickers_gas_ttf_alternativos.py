import yfinance as yf

tickers = {
    "TTF=F": "Gas TTF futures",
    "TTFE.DE": "TTF Gas EEX",
    "NG=F": "Natural Gas futures USD",
}

for ticker, nombre in tickers.items():
    df = yf.download(ticker, start="2020-01-01", end="2020-12-31")
    print(f"\n=== {ticker} — {nombre} ===")
    print(f"Filas: {len(df)}")
    if len(df) > 0:
        print(f"Primer dato: {df.index[0].date()}")
        print(df.head(3))