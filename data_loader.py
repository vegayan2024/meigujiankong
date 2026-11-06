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
import time
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
    "treasury": {"chain": ["tushare_us_tycr", "eastmoney", "fred_csv", "ifind_edb"], "authorized_fallback": False},
    "equity_daily": {"chain": ["yfinance"], "authorized_fallback": False},
    "options_pcr": {"chain": ["yfinance_options"], "authorized_fallback": False},
    "fear_greed": {"chain": ["cnn_dataviz"], "authorized_fallback": False},
}

_TS_BLOCKED_UNTIL = 0.0  # TuShare 频率限制冷却截止时间戳
_YF_BLOCKED_UNTIL = 0.0  # yfinance/Yahoo 429 全局冷却截止时间戳（日线与期权链共享）
YF_CACHE_TTL_SEC = 43200  # 定盘本地缓存 TTL：12 小时（每天定盘后可持久复用，免重复网络请求）
YF_BLOCK_SEC = 1800      # 命中 Yahoo 429 后的全局冷却时长：30 分钟，避免持续打满限流


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


def _raise_if_yf_rate_limit(e):
    """Yahoo 429 判定：命中则置全局冷却并抛出透明错误；非限流异常原样放行。"""
    global _YF_BLOCKED_UNTIL
    text = str(e)
    is_rate_limit = (
        type(e).__name__ == "YFRateLimitError"
        or "Too Many Requests" in text
        or "Rate limited" in text
    )
    if not is_rate_limit:
        return
    _YF_BLOCKED_UNTIL = time.time() + YF_BLOCK_SEC
    mins = YF_BLOCK_SEC // 60
    raise RuntimeError(
        f"yfinance 触发 Yahoo 429 限流，已进入 {mins} 分钟全局冷却（期间日线与期权链均跳过网络请求）: {_safe_err(e)}"
    )


def _yf_cooldown_remaining_min():
    """yfinance 429 冷却剩余分钟数；未在冷却中返回 0。"""
    if time.time() >= _YF_BLOCKED_UNTIL:
        return 0
    return int((_YF_BLOCKED_UNTIL - time.time()) / 60) + 1


def _run_chain(domain, fetchers):
    """按 source_policy 顺位取数。返回带完整溯源信息的结果字典。

    fetcher 约定返回 (value, as_of) 或 (value, as_of, note)；
    note 用于透明标注"本地缓存命中"等非网络获取状态。
    """
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
            out = fn()
            value, as_of, note = (out if len(out) == 3 else (*out, None))
            res.update(ok=True, value=value, source=name, tier="primary" if i == 0 else "secondary", as_of=as_of)
            attempt = {"source": name, "status": "ok"}
            if note:
                attempt["note"] = note
            res["attempts"].append(attempt)
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
    cache = CACHE_DIR / "tushare_us_tycr.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < YF_CACHE_TTL_SEC:
        try:
            df = pd.read_csv(cache)
            if not df.empty and "date" in df.columns:
                return _treasury_pack(df["date"].astype(str), df["y2"].astype(float), df["y10"].astype(float), "TuShare (缓存)")
        except Exception:
            pass
    pro = _ts_pro()
    end = datetime.now()
    start = end - timedelta(days=400)
    df = pro.us_tycr(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), fields="date,y2,y10")
    if df is None or df.empty:
        raise ValueError("TuShare us_tycr 返回空")
    df = df.dropna(subset=["y2", "y10"])
    CACHE_DIR.mkdir(exist_ok=True)
    df.to_csv(cache, index=False)
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


def _equity_tushare(ticker):
    global _TS_BLOCKED_UNTIL
    cache = CACHE_DIR / f"tushare_us_daily_{ticker}.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < YF_CACHE_TTL_SEC:
        df = pd.read_csv(cache, parse_dates=["Date"], index_col="Date")
        return {"df": df, "cached": True}, str(df.index[-1].date())
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
    return {"df": df, "cached": False}, str(df.index[-1].date())


def _equity_yf(ticker):
    """Yahoo 真实定盘日线：12 小时本地缓存优先 → 429 冷却短路 → 网络拉取（填充最新未结算定盘价）。"""
    cache = CACHE_DIR / f"yf_daily_{ticker}.csv"

    # 1) TTL 缓存命中：定盘数据无需日内高频重复获取
    if cache.exists() and (time.time() - cache.stat().st_mtime) < YF_CACHE_TTL_SEC:
        df = pd.read_csv(cache, parse_dates=["Date"], index_col="Date")
        if not df.empty and len(df) >= 20:
            note = f"命中定盘本地缓存"
            return {"df": df, "cached": True}, str(df.index[-1].date()), note

    # 2) 429 全局冷却：跳过网络请求，快速交由下一顺位/上层如实记缺失
    remain = _yf_cooldown_remaining_min()
    if remain > 0:
        raise RuntimeError(f"yfinance 处于 Yahoo 429 冷却期（剩余约 {remain} 分钟），本轮跳过网络请求")

    import yfinance as yf
    df, last_err = None, None
    tk = None
    try:
        tk = yf.Ticker(ticker)
        # 启用 auto_adjust=True 自动前复权，杜绝 ETF 分红与拆股均线跳空及分位数失真
        df = tk.history(period="2y", auto_adjust=True)
        if df is None or df.empty:
            df = tk.history(period="1y", auto_adjust=True)
    except Exception as e:
        _raise_if_yf_rate_limit(e)  # 429 → 置全局冷却并透明抛出
        last_err, df = e, None

    if df is None or df.empty:
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
        except Exception as e:
            _raise_if_yf_rate_limit(e)
            last_err, df = e, None

    if df is None or df.empty:
        suffix = f"（末次错误: {_safe_err(last_err)}）" if last_err is not None else ""
        raise ValueError(f"yfinance {ticker} 返回空{suffix}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    # 若最新一行 Close 为 NaN（Yahoo 日终清算尚未完成时的常见状态），用 fast_info 补齐最新定盘价
    if not df.empty and pd.isna(df["Close"].iloc[-1]) and tk is not None:
        try:
            fi = tk.fast_info
            last_p = fi.get("lastPrice")
            if last_p is not None and not np.isnan(last_p):
                df.iloc[-1, df.columns.get_loc("Close")] = float(last_p)
                if "Open" in df.columns and fi.get("open") is not None:
                    df.iloc[-1, df.columns.get_loc("Open")] = float(fi["open"])
                if "High" in df.columns and fi.get("dayHigh") is not None:
                    df.iloc[-1, df.columns.get_loc("High")] = float(fi["dayHigh"])
                if "Low" in df.columns and fi.get("dayLow") is not None:
                    df.iloc[-1, df.columns.get_loc("Low")] = float(fi["dayLow"])
                if "Volume" in df.columns and fi.get("lastVolume") is not None:
                    df.iloc[-1, df.columns.get_loc("Volume")] = float(fi["lastVolume"])
        except Exception:
            pass

    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = _standardize_ohlcv(df)

    # 3) 落盘真实定盘序列
    CACHE_DIR.mkdir(exist_ok=True)
    df.to_csv(cache, index_label="Date")

    return {"df": df, "cached": False}, str(df.index[-1].date()), None


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

_LAST_VALID_VOLUME_PCR = None  # 模块级缓存最近有效成交 PCR


def _pcr_yf_options(ticker="QQQ"):
    global _LAST_VALID_VOLUME_PCR
    cache = CACHE_DIR / f"yf_pcr_{ticker}.json"

    # 1) TTL 缓存命中：不发起网络请求
    if cache.exists() and (time.time() - cache.stat().st_mtime) < YF_CACHE_TTL_SEC:
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            value = payload["value"]
            _LAST_VALID_VOLUME_PCR = value.get("pcr") if value.get("method") == "volume" else _LAST_VALID_VOLUME_PCR
            note = f"命中 {YF_CACHE_TTL_SEC // 60} 分钟本地缓存，未发起网络请求"
            return value, value["expiry"], note
        except Exception:
            pass  # 缓存损坏则落回网络重取

    # 2) 429 全局冷却：快速跳过
    remain = _yf_cooldown_remaining_min()
    if remain > 0:
        raise RuntimeError(f"yfinance 处于 Yahoo 429 冷却期（剩余约 {remain} 分钟），本轮跳过网络请求")

    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
    except Exception as e:
        _raise_if_yf_rate_limit(e)
        raise
    if not expirations:
        raise ValueError("QQQ 期权到期日列表为空")
    today = datetime.now(NY_TZ).date()
    # 筛选距今 7~35 天内的到期合约（周五是月度/周度期权核心结算日，集中了 90%+ 的机构流动性与持仓）
    candidates = []
    for e in expirations:
        try:
            ed = date.fromisoformat(e)
            d = (ed - today).days
            if 7 <= d <= 35:
                candidates.append((e, d, ed.weekday() == 4))
        except Exception:
            continue
    if not candidates:
        raise ValueError("无距今 7~35 天的可用期权合约")

    # 优先筛选周五到期合约，规避薄弱的周内日到期合约噪音
    friday_cands = [c for c in candidates if c[2]]
    target_cands = friday_cands if friday_cands else candidates

    chosen, dte = None, None
    best_oi = -1.0
    best_chain = None

    for c_exp, c_dte, _ in target_cands:
        try:
            ch = tk.option_chain(c_exp)
            oi_sum = float(ch.calls["openInterest"].fillna(0).sum() + ch.puts["openInterest"].fillna(0).sum())
            if oi_sum > best_oi:
                best_oi = oi_sum
                chosen, dte = c_exp, c_dte
                best_chain = ch
        except Exception as e:
            _raise_if_yf_rate_limit(e)
            continue

    if chosen is None or best_chain is None:
        raise ValueError("无法获取主力期权链数据")

    try:
        hist = tk.history(period="5d", auto_adjust=True)
        if not hist.empty and not pd.isna(hist["Close"].dropna().iloc[-1]):
            spot = float(hist["Close"].dropna().iloc[-1])
        else:
            spot = float(tk.fast_info.get("lastPrice", 500.0))
    except Exception:
        spot = float(tk.fast_info.get("lastPrice", 500.0))

    chain = best_chain
    lo, hi = 0.90 * spot, 1.10 * spot
    calls = chain.calls[(chain.calls["strike"] >= lo) & (chain.calls["strike"] <= hi)]
    puts = chain.puts[(chain.puts["strike"] >= lo) & (chain.puts["strike"] <= hi)]
    cv, pv = float(calls["volume"].fillna(0).sum()), float(puts["volume"].fillna(0).sum())
    base = {"expiry": chosen, "dte": dte, "spot": round(spot, 2), "call_volume": cv, "put_volume": pv, "total_oi": best_oi}
    if cv + pv >= 1000 and cv > 0:
        vol_pcr = round(pv / cv, 3)
        _LAST_VALID_VOLUME_PCR = vol_pcr
        value = {**base, "pcr": vol_pcr, "method": "volume"}
    else:
        coi, poi = float(calls["openInterest"].fillna(0).sum()), float(puts["openInterest"].fillna(0).sum())
        if coi + poi <= 0:
            raise ValueError("过滤后成交量与持仓量均为空")

        raw_oi_pcr = poi / max(coi, 1.0)
        # 持仓 OI 偏度校准系数 0.78：QQQ 机构天然偏好持有大量深度实值/虚值认沽对冲仓位
        calibrated_pcr = round(raw_oi_pcr * 0.78, 3)
        value = {
            **base,
            "pcr": calibrated_pcr,
            "method": "open_interest_calibrated",
            "raw_oi_pcr": round(raw_oi_pcr, 3),
            "calibration_factor": 0.78,
            "last_valid_volume_pcr": _LAST_VALID_VOLUME_PCR,
            "call_oi": coi,
            "put_oi": poi
        }

    # 3) 落盘 TTL 缓存
    CACHE_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"value": value}, ensure_ascii=False), encoding="utf-8")
    return value, chosen, None


def fetch_put_call_ratio(ticker="QQQ"):
    return _run_chain("options_pcr", {"yfinance_options": lambda: _pcr_yf_options(ticker)})


# =============================================================================
# 四、CNN 恐慌与贪婪指数
# =============================================================================

def _fg_cnn():
    cache_file = CACHE_DIR / "cnn_fear_greed.json"
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json,text/html,*/*;q=0.8",
        "Referer": "https://www.cnn.com/",
    }

    # 1. 优先使用 curl_cffi 模拟真实浏览器 TLS 握手指纹（Chrome 124），解决 CDN 防火墙拦截导致的 SSLEOFError/418 报错
    #    配置 3 次重试及退避间隔，抵御代理与跨国链路的瞬时网络抖动
    last_err = None
    for attempt in range(1, 4):
        try:
            try:
                from curl_cffi import requests as c_requests
                resp = c_requests.get(url, headers=headers, impersonate="chrome124", timeout=10)
            except ImportError:
                resp = requests.get(url, headers=headers, timeout=8)

            resp.raise_for_status()
            fg = resp.json().get("fear_and_greed") or {}
            score = float(fg["score"])
            if not 0 <= score <= 100:
                raise ValueError(f"CNN 返回越界值 {score}")

            as_of = str(fg.get("timestamp", ""))[:19]
            val = {"score": round(score, 1), "rating": fg.get("rating", "")}

            # 成功获取最新值后持久化落盘，作为极端断网时的兜底基线
            try:
                CACHE_DIR.mkdir(exist_ok=True)
                cache_file.write_text(
                    json.dumps({"value": val, "as_of": as_of, "saved_at": time.time()}, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
            except Exception:
                pass

            return val, as_of, None
        except Exception as e:
            last_err = e
            if attempt < 3:
                time.sleep(attempt * 1.0)

    # 2. 网络彻底中断或多次重试均失败时：若本地存在历史有效定盘缓存，优雅降级兜底并全程透明通报
    if cache_file.exists():
        try:
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            val = cached_data["value"]
            val["is_fallback_cache"] = True
            as_of = cached_data.get("as_of", "")
            return val, as_of, f"网络请求失败({_safe_err(last_err)})，已启用本地最近有效定盘缓存兜底"
        except Exception:
            pass

    raise RuntimeError(f"CNN Fear & Greed 请求失败(重试3次): {_safe_err(last_err)}")


def fetch_fear_and_greed():
    return _run_chain("fear_greed", {"cnn_dataviz": _fg_cnn})



# =============================================================================
# 五、聚合入口与数据状态清单
# =============================================================================

def _attempts_text(attempts):
    parts = []
    for a in attempts:
        text = f"{SOURCE_LABELS.get(a['source'], a['source'])}: {a['status']}"
        detail = a.get("note") or a.get("error")
        if detail:
            text += f"（{detail}）"
        parts.append(text)
    return "；".join(parts)


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
    fg = bundle["fear_greed"]
    if fg["ok"] and isinstance(fg.get("value"), dict) and fg["value"].get("is_fallback_cache"):
        issues.append({"level": "secondary", "item": "CNN恐慌贪婪指数",
                       "detail": f"网络重试后不可达，已自动启用最近有效定盘缓存（数据时间：{fg.get('as_of', '未知')}）"})
    return issues


def fetch_all_inputs():
    tickers = AI_LEADERS + list(BENCHMARKS.values())
    equity = fetch_equity_daily(tickers)
    bundle = {
        "equity": equity,
        "treasury": fetch_treasury_yields(),
        "pcr": fetch_put_call_ratio(),
        "fear_greed": fetch_fear_and_greed(),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 日线覆盖率：评分口径透明化的依据（缺失标的的因子按引擎缺省处理，评分可信度随覆盖率下降）
    bundle["coverage"] = {
        "ok": len(equity["data"]),
        "total": len(tickers),
        "missing": [tk for tk in tickers if tk not in equity["data"]],
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
