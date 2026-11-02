# -*- coding: utf-8 -*-
"""
数据接入层 (Data Loader)
========================
原则（与全局数据纪律一致）：
  1. 第一顺位优先使用专业金融数据接口（TuShare Pro / 同花顺 iFinD）。
  2. 非第一顺位数据源默认【不启用】，须用户在 source_policy.json 中把对应域的
     authorized_fallback 设为 true 后才会使用；启用后命中的数据一律标注为 secondary。
  3. 取不到的数据返回 ok=False 并保留每一步失败原因，严禁用默认值/估算值冒充。
  4. 每个返回结果都带 source / tier / as_of / attempts 字段，供前端、日志与推送如实展示。
"""

import os
import sys
import json
import warnings
from pathlib import Path
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
POLICY_FILE = BASE_DIR / "source_policy.json"
CACHE_DIR = BASE_DIR / ".cache"
IFIND_SKILL_DIR = os.environ.get(
    "IFIND_SKILL_DIR",
    r"C:\Users\vega_\.gemini\config\plugins\claude-custom-skills\skills\ifind-finance-data",
)
NY_TZ = ZoneInfo("America/New_York")

AI_LEADERS = ["NVDA", "MSFT", "AVGO", "GOOGL", "TSM"]
BENCHMARKS = {"SOX_PROXY": "SMH", "TECH_BENCHMARK": "QQQ", "BROAD_MARKET": "SPY"}

SOURCE_LABELS = {
    "tushare_us_tycr": "TuShare Pro us_tycr (美债收益率曲线)",
    "ifind_edb": "同花顺 iFinD EDB",
    "eastmoney": "东方财富数据中心",
    "fred_csv": "FRED 公开CSV",
    "tushare_us_daily": "TuShare Pro us_daily (美股日线)",
    "yfinance": "Yahoo Finance (yfinance)",
    "yfinance_options": "Yahoo Finance 期权链 (yfinance)",
    "cnn_dataviz": "CNN Fear & Greed 官方端点",
}

# 每个数据域的取数顺位。chain[0] 为第一顺位；其余仅在 authorized_fallback=true 时启用。
DEFAULT_POLICY = {
    "treasury": {"chain": ["tushare_us_tycr", "ifind_edb", "eastmoney", "fred_csv"], "authorized_fallback": False},
    "equity_daily": {"chain": ["tushare_us_daily", "yfinance"], "authorized_fallback": False},
    "options_pcr": {"chain": ["yfinance_options"], "authorized_fallback": False},
    "fear_greed": {"chain": ["cnn_dataviz"], "authorized_fallback": False},
}

_TS_BLOCKED_UNTIL = 0.0  # TuShare us_daily 频率限制冷却截止时间戳


# =============================================================================
# 基础工具
# =============================================================================

def _load_env():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()


def load_policy():
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    if POLICY_FILE.exists():
        try:
            user = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
            for domain, cfg in user.items():
                if domain in policy and isinstance(cfg, dict):
                    policy[domain].update(cfg)
        except Exception as e:
            print(f"[data_loader] 读取 source_policy.json 失败，使用默认严格策略: {e}")
    return policy


def _safe_err(e):
    text = str(e).replace("\n", " ")
    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        text = text.replace(token, "[已隐藏]")
    return text[:240]


def _run_chain(domain, fetchers):
    """按 source_policy 顺位取数。返回带完整溯源信息的结果字典。"""
    cfg = load_policy()[domain]
    chain = cfg.get("chain", [])
    allow = bool(cfg.get("authorized_fallback", False))
    res = {"domain": domain, "ok": False, "value": None, "source": None, "tier": None,
           "as_of": None, "attempts": []}
    for i, name in enumerate(chain):
        if i > 0 and not allow:
            res["attempts"].append({
                "source": name, "status": "skipped",
                "error": "非第一顺位数据源，未获用户授权（source_policy.json 中 authorized_fallback=false）",
            })
            continue
        fn = fetchers.get(name)
        if fn is None:
            res["attempts"].append({"source": name, "status": "failed", "error": "未实现的数据源"})
            continue
        try:
            value, as_of = fn()
            res.update(ok=True, value=value, source=name, tier="primary" if i == 0 else "secondary", as_of=as_of)
            res["attempts"].append({"source": name, "status": "ok"})
            return res
        except Exception as e:
            res["attempts"].append({"source": name, "status": "failed", "error": _safe_err(e)})
    return res


def _ts_pro():
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("未配置 TUSHARE_TOKEN（请写入项目目录 .env）")
    import tushare as ts
    return ts.pro_api(token)


def _ifind_call(server_type, tool_name, params):
    if not Path(IFIND_SKILL_DIR, "call.py").exists():
        raise RuntimeError(f"未找到 iFinD 调用脚本: {IFIND_SKILL_DIR}")
    if IFIND_SKILL_DIR not in sys.path:
        sys.path.insert(0, IFIND_SKILL_DIR)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from call import call  # noqa: E402  (iFinD skill 自带脚本)
        r = call(server_type, tool_name, params)
    if not r.get("ok"):
        raise RuntimeError(f"iFinD 调用失败: {r.get('error')}")
    text = r["data"]["result"]["content"][0]["text"]
    payload = json.loads(text)
    if payload.get("code") != 1:
        raise RuntimeError(f"iFinD 返回错误: {payload.get('msg')}")
    return payload.get("data") or {}


# =============================================================================
# 一、美债 2Y / 10Y 收益率（含利差历史序列，供因子5路径感知）
# =============================================================================

def _treasury_pack(dates, y2, y10, label):
    df = pd.DataFrame({"date": list(dates), "y2": list(y2), "y10": list(y10)}).dropna()
    df = df.sort_values("date")
    if df.empty:
        raise ValueError(f"{label} 无有效 2Y/10Y 数据")
    spreads = (df["y10"] - df["y2"]).round(3).tolist()
    last = df.iloc[-1]
    return ({
        "us_2y": float(last["y2"]),
        "us_10y": float(last["y10"]),
        "spread": round(float(last["y10"] - last["y2"]), 3),
        "spread_history": spreads,
    }, str(last["date"]))


def _treasury_tushare():
    pro = _ts_pro()
    end = datetime.now()
    start = end - timedelta(days=400)
    df = pro.us_tycr(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), fields="date,y2,y10")
    if df is None or df.empty:
        raise ValueError("TuShare us_tycr 返回空")
    df = df.dropna(subset=["y2", "y10"])
    return _treasury_pack(df["date"].astype(str), df["y2"].astype(float), df["y10"].astype(float), "TuShare")


def _treasury_ifind():
    end = date.today()
    start = end - timedelta(days=400)
    data = _ifind_call("edb", "get_edb_data", {
        "query": f"美国2年期国债收益率和美国10年期国债收益率 {start:%Y-%m-%d}至{end:%Y-%m-%d} 日度"
    })
    answer = str(data.get("answer", "")).replace("\\n", "\n")
    lines = [ln for ln in answer.split("\n") if ln.strip().startswith("|")]
    if len(lines) < 3:
        raise ValueError("iFinD EDB 未返回表格数据")
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    i2 = next(i for i, h in enumerate(header) if "2年" in h and "10年" not in h)
    i10 = next(i for i, h in enumerate(header) if "10年" in h)
    rows = []
    for ln in lines[2:]:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        try:
            rows.append((cells[0].replace("-", ""), float(cells[i2]), float(cells[i10])))
        except (ValueError, IndexError):
            continue
    if not rows:
        raise ValueError("iFinD EDB 表格中无完整的 2Y/10Y 同日数据")
    d, y2, y10 = zip(*rows)
    return _treasury_pack(d, y2, y10, "iFinD")


def _treasury_eastmoney():
    url = ("https://datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD&sty=ALL"
           "&st=SOLAR_DATE&sr=-1&p=1&ps=300&token=894050c76af8597a853f5b408b759f5d")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
    resp.raise_for_status()
    rows = ((resp.json().get("result") or {}).get("data")) or []
    recs = []
    for r in rows:
        try:
            recs.append((str(r["SOLAR_DATE"])[:10].replace("-", ""), float(r["EMG00001306"]), float(r["EMG00001310"])))
        except (TypeError, ValueError, KeyError):
            continue
    if not recs:
        raise ValueError("东方财富未返回有效美债数据")
    d, y2, y10 = zip(*recs)
    return _treasury_pack(d, y2, y10, "东方财富")


def _treasury_fred():
    frames = {}
    for sid in ("DGS2", "DGS10"):
        resp = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=10)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        df.columns = ["date", sid]
        df[sid] = pd.to_numeric(df[sid], errors="coerce")
        frames[sid] = df.dropna().tail(400)
    m = frames["DGS2"].merge(frames["DGS10"], on="date")
    return _treasury_pack(m["date"].str.replace("-", ""), m["DGS2"], m["DGS10"], "FRED")


def fetch_treasury_yields():
    return _run_chain("treasury", {
        "tushare_us_tycr": _treasury_tushare,
        "ifind_edb": _treasury_ifind,
        "eastmoney": _treasury_eastmoney,
        "fred_csv": _treasury_fred,
    })


# =============================================================================
# 二、美股 / ETF 日线
# =============================================================================

def _standardize_ohlcv(df):
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["Close"])
    df["Volume"] = df["Volume"].astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _project_intraday_volume(df):
    """美股盘中最后一根日K未收盘：按已开盘分钟数把成交量折算为全天预估量。"""
    now = datetime.now(NY_TZ)
    if df.empty or now.weekday() >= 5 or df.index[-1].date() != now.date():
        return df, None
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if not (open_t <= now < close_t):
        return df, None
    elapsed = max((now - open_t).total_seconds() / 60.0, 15.0)
    k = min(390.0 / elapsed, 10.0)
    df = df.copy()
    df.iloc[-1, df.columns.get_loc("Volume")] *= k
    return df, {"factor": round(k, 2), "elapsed_min": int(elapsed)}


def _equity_tushare(ticker):
    global _TS_BLOCKED_UNTIL
    cache = CACHE_DIR / f"tushare_us_daily_{ticker}.csv"
    if cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime).date() == date.today():
        df = pd.read_csv(cache, parse_dates=["Date"], index_col="Date")
        return {"df": df, "projected": None, "cached": True}, str(df.index[-1].date())
    if datetime.now().timestamp() < _TS_BLOCKED_UNTIL:
        raise RuntimeError("TuShare us_daily 已触发频率限制，冷却期内跳过")
    pro = _ts_pro()
    end = datetime.now()
    start = end - timedelta(days=430)
    try:
        raw = pro.us_daily(ts_code=ticker, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
                           fields="trade_date,open,high,low,close,vol")
    except Exception as e:
        if any(k in str(e) for k in ("频率", "每小时", "每分钟", "次/")):
            _TS_BLOCKED_UNTIL = datetime.now().timestamp() + 3600
            raise RuntimeError(f"TuShare us_daily 账号频率/权限受限: {_safe_err(e)}")
        raise
    if raw is None or raw.empty:
        raise ValueError(f"TuShare us_daily {ticker} 返回空")
    raw = raw.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "vol": "Volume"})
    raw.index = pd.to_datetime(raw.pop("trade_date"))
    df = _standardize_ohlcv(raw)
    CACHE_DIR.mkdir(exist_ok=True)
    df.to_csv(cache, index_label="Date")
    return {"df": df, "projected": None, "cached": False}, str(df.index[-1].date())


def _equity_yf(ticker):
    import yfinance as yf
    df = None
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period="2y", auto_adjust=False)
        if df is None or df.empty:
            df = tk.history(period="1y")
    except Exception:
        df = None

    if df is None or df.empty:
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=False)
        except Exception:
            pass

    if df is None or df.empty:
        raise ValueError(f"yfinance {ticker} 返回空")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = _standardize_ohlcv(df)
    df, projected = _project_intraday_volume(df)
    return {"df": df, "projected": projected, "cached": False}, str(df.index[-1].date())


def fetch_equity_daily(tickers):
    """逐标的按顺位取日线。返回 {"data": {ticker: df}, "meta": {ticker: 溯源信息}}。"""
    data, meta = {}, {}
    for tk in tickers:
        r = _run_chain("equity_daily", {
            "tushare_us_daily": lambda tk=tk: _equity_tushare(tk),
            "yfinance": lambda tk=tk: _equity_yf(tk),
        })
        info = {k: v for k, v in r.items() if k != "value"}
        if r["ok"]:
            data[tk] = r["value"]["df"]
            info["projected"] = r["value"]["projected"]
            info["cached"] = r["value"]["cached"]
            info["rows"] = len(r["value"]["df"])
        meta[tk] = info
    return {"data": data, "meta": meta}


def fetch_historical_data(tickers=None, period="1y"):
    """向后兼容接口：获取历史日线并返回 {ticker: DataFrame}"""
    if tickers is None:
        tickers = AI_LEADERS + list(BENCHMARKS.values())
    res = fetch_equity_daily(tickers)
    return res["data"]


# =============================================================================
# 三、QQQ 期权 Put/Call Ratio
# =============================================================================

def _pcr_yf_options(ticker="QQQ"):
    import yfinance as yf
    tk = yf.Ticker(ticker)
    expirations = tk.options
    if not expirations:
        raise ValueError("QQQ 期权到期日列表为空")
    today = datetime.now(NY_TZ).date()
    chosen, dte = None, None
    for e in expirations:
        d = (date.fromisoformat(e) - today).days
        if 7 <= d <= 25:
            chosen, dte = e, d
            break
    if chosen is None:
        raise ValueError("无距今 7~25 天的到期合约")
    hist = tk.history(period="5d")
    if hist.empty:
        raise ValueError("无法获取 QQQ 现价用于行权价过滤")
    spot = float(hist["Close"].dropna().iloc[-1])
    chain = tk.option_chain(chosen)
    lo, hi = 0.90 * spot, 1.10 * spot
    calls = chain.calls[(chain.calls["strike"] >= lo) & (chain.calls["strike"] <= hi)]
    puts = chain.puts[(chain.puts["strike"] >= lo) & (chain.puts["strike"] <= hi)]
    cv, pv = float(calls["volume"].fillna(0).sum()), float(puts["volume"].fillna(0).sum())
    base = {"expiry": chosen, "dte": dte, "spot": round(spot, 2), "call_volume": cv, "put_volume": pv}
    if cv + pv >= 1000 and cv > 0:
        return {**base, "pcr": round(pv / cv, 3), "method": "volume"}, chosen
    coi, poi = float(calls["openInterest"].fillna(0).sum()), float(puts["openInterest"].fillna(0).sum())
    if coi + poi <= 0:
        raise ValueError("过滤后成交量与持仓量均为空（可能为非交易时段数据重置）")
    return {**base, "pcr": round(poi / max(coi, 1.0), 3), "method": "open_interest",
            "call_oi": coi, "put_oi": poi}, chosen


def fetch_put_call_ratio(ticker="QQQ"):
    return _run_chain("options_pcr", {"yfinance_options": lambda: _pcr_yf_options(ticker)})


# =============================================================================
# 四、CNN 恐慌与贪婪指数
# =============================================================================

def _fg_cnn():
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json,text/html,*/*;q=0.8",
        "Referer": "https://www.cnn.com/",
    }
    resp = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=8)
    resp.raise_for_status()
    fg = resp.json().get("fear_and_greed") or {}
    score = float(fg["score"])
    if not 0 <= score <= 100:
        raise ValueError(f"CNN 返回越界值 {score}")
    return {"score": round(score, 1), "rating": fg.get("rating", "")}, str(fg.get("timestamp", ""))[:19]


def fetch_fear_and_greed():
    return _run_chain("fear_greed", {"cnn_dataviz": _fg_cnn})


# =============================================================================
# 五、聚合入口与数据状态清单
# =============================================================================

def _attempts_text(attempts):
    return "；".join(f"{SOURCE_LABELS.get(a['source'], a['source'])}: {a['status']}"
                    + (f"（{a['error']}）" if a.get("error") else "") for a in attempts)


def build_data_status(bundle):
    """把所有缺失 / 非第一顺位 / 特殊处理的数据整理成清单，供界面、日志和推送如实告知用户。"""
    issues = []
    for tk, m in bundle["equity"]["meta"].items():
        if not m["ok"]:
            issues.append({"level": "missing", "item": f"{tk} 日线", "detail": _attempts_text(m["attempts"])})
        else:
            if m["tier"] == "secondary":
                issues.append({"level": "secondary", "item": f"{tk} 日线",
                               "detail": f"来自非第一顺位源 {SOURCE_LABELS.get(m['source'])}；首选失败：{_attempts_text(m['attempts'][:-1])}"})
            if m.get("projected"):
                p = m["projected"]
                issues.append({"level": "note", "item": f"{tk} 日线",
                               "detail": f"美股盘中未收盘K线，已按开盘{p['elapsed_min']}分钟将当日成交量×{p['factor']}折算全天预估"})
    for key, label in (("treasury", "美债2Y/10Y收益率"), ("pcr", "QQQ期权PCR"), ("fear_greed", "CNN恐慌贪婪指数")):
        r = bundle[key]
        if not r["ok"]:
            issues.append({"level": "missing", "item": label, "detail": _attempts_text(r["attempts"])})
        elif r["tier"] == "secondary":
            issues.append({"level": "secondary", "item": label,
                           "detail": f"来自非第一顺位源 {SOURCE_LABELS.get(r['source'])}；首选失败：{_attempts_text(r['attempts'][:-1])}"})
    pcr = bundle["pcr"]
    if pcr["ok"] and pcr["value"].get("method") == "open_interest":
        issues.append({"level": "note", "item": "QQQ期权PCR",
                       "detail": "过滤后成交量不足1000张，本次按持仓量(Open Interest)口径计算PCR"})
    return issues


def fetch_all_inputs():
    tickers = AI_LEADERS + list(BENCHMARKS.values())
    bundle = {
        "equity": fetch_equity_daily(tickers),
        "treasury": fetch_treasury_yields(),
        "pcr": fetch_put_call_ratio(),
        "fear_greed": fetch_fear_and_greed(),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    bundle["status"] = build_data_status(bundle)
    return bundle


if __name__ == "__main__":
    b = fetch_all_inputs()
    print(f"取数时间: {b['fetched_at']}")
    for tk, m in b["equity"]["meta"].items():
        print(f"  {tk:6s} ok={m['ok']} source={m['source']} tier={m['tier']} as_of={m['as_of']}")
    for k in ("treasury", "pcr", "fear_greed"):
        r = b[k]
        print(f"  {k:10s} ok={r['ok']} source={r['source']} tier={r['tier']} as_of={r['as_of']}")
    print("数据问题清单:")
    for i in b["status"]:
        print(f"  [{i['level']}] {i['item']}: {i['detail']}")
