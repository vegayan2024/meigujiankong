import yfinance as yf
import pandas as pd
import numpy as np
import requests
import json

AI_LEADERS = ["NVDA", "MSFT", "AVGO", "GOOGL", "TSM"]
BENCHMARKS = {"SOX_PROXY": "SMH", "TECH_BENCHMARK": "QQQ", "BROAD_MARKET": "SPY"}

def fetch_historical_data(tickers, period="1y", interval="1d"):
    """
    拉取标的的历史日线数据 (OHLCV)
    默认拉取1年，以便准确计算 200 日均线
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

def fetch_treasury_yields():
    """
    获取美债 2Y、10Y 收益率及利差 (10Y - 2Y)
    首选国内直连快速的东方财富数据中心 API，备用降级为 yfinance (^TNX)
    """
    # 方案 1: 东方财富 API (国内直连、高可靠、秒级响应)
    try:
        url = "https://datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD&sty=ALL&st=SOLAR_DATE&sr=-1&p=1&ps=5&token=894050c76af8597a853f5b408b759f5d"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            result = resp.json().get("result") or {}
            rows = result.get("data") or []
            if rows:
                latest = rows[0]
                us2y = float(latest.get("EMG00001306", 0))
                us10y = float(latest.get("EMG00001310", 0))
                date_str = str(latest.get("SOLAR_DATE", ""))[:10]
                if us2y > 0 and us10y > 0:
                    spread = round(us10y - us2y, 2)
                    return {
                        "us_2y": us2y,
                        "us_10y": us10y,
                        "spread": spread,
                        "date": date_str,
                        "source": "东方财富 (国内直连)"
                    }
    except Exception as e:
        print(f"EastMoney Treasury fetch failed: {e}")

    # 方案 2: yfinance 备用降级
    try:
        tnx = yf.Ticker("^TNX").history(period="5d")
        if not tnx.empty:
            us10y = float(tnx["Close"].dropna().iloc[-1])
            # 若缺失 2Y，假设正常利差平坦区间作为保守替代
            return {
                "us_2y": round(us10y - 0.2, 2),
                "us_10y": round(us10y, 2),
                "spread": 0.20,
                "date": str(tnx.index[-1].date()),
                "source": "yfinance (^TNX 备用)"
            }
    except Exception as e:
        print(f"yfinance Treasury fallback failed: {e}")

    # 兜底默认值
    return {
        "us_2y": 4.50,
        "us_10y": 4.40,
        "spread": -0.10,
        "date": "兜底参考",
        "source": "基线估算"
    }

def fetch_fear_and_greed():
    """
    抓取 CNN 官方 Fear & Greed 恐慌贪婪指数 (0-100)
    0-25: 极度恐惧, 25-45: 恐惧, 45-55: 中性, 55-75: 贪婪, 75-100: 极度贪婪
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.cnn.com/"
    }
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            fg = data.get("fear_and_greed", {})
            score = float(fg.get("score", 50))
            rating = fg.get("rating", "neutral")
            return {
                "score": round(score, 1),
                "rating": rating,
                "source": "CNN 官方实时"
            }
    except Exception as e:
        print(f"CNN Fear & Greed fetch failed: {e}")

    return {
        "score": 50.0,
        "rating": "neutral",
        "source": "中性兜底"
    }

