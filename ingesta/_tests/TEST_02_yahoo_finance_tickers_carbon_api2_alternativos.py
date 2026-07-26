import yfinance as yf

tickers = {
    "MTF=F": "Coal API2 futures",
    "XAD=F": "Coal API2 ARA",
    "KOL":   "VanEck Coal ETF",
    "COAL":  "Coal ETF",
}

for ticker, nombre in tickers.items():
    df = yf.download(ticker, start="2020-01-01", end="2020-06-30", progress=False)
    print(f"{ticker} — {nombre}: {len(df)} filas", end="")
    if len(df) > 0:
        print(f" | desde {df.index[0].date()}")
    else:
        print(" — sin datos")