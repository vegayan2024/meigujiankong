import yfinance as yf
import pandas as pd
import numpy as np

AI_LEADERS = ["NVDA", "MSFT", "AVGO", "GOOGL", "TSM"]
BENCHMARKS = {"SOX_PROXY": "SMH", "TECH_BENCHMARK": "QQQ", "BROAD_MARKET": "SPY"}

def fetch_historical_data(tickers, period="6mo", interval="1d"):
    """
    拉取标的的历史日线数据 (OHLCV)
    """
    data = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [col[0] for col in df.columns]
                data[ticker] = df
        except Exception as e:
            print(f"Error fetching data for {ticker}: {e}")
    return data

def fetch_put_call_ratio(ticker="QQQ"):
    """
    获取近月期权的成交量 Put/Call Ratio 作为情绪偏度代理
    """
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return 1.0
        
        opt_chain = tk.option_chain(expirations[0])
        calls = opt_chain.calls
        puts = opt_chain.puts
        
        call_vol = calls["volume"].fillna(0).sum()
        put_vol = puts["volume"].fillna(0).sum()
        
        if call_vol > 0:
            return float(put_vol / call_vol)
        return 1.0
    except Exception as e:
        print(f"Error fetching options for {ticker}: {e}")
        return 1.0
