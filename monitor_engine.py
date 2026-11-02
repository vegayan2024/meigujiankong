import pandas as pd
import numpy as np

class CrashRiskEngine:
    def __init__(self, data_dict, pcr_val):
        self.data = data_dict
        self.pcr = pcr_val

    def calc_distribution_days(self, ticker="QQQ", window=25):
        """
        因子 1: 欧奈尔机构派发日统计 (Distribution Days)
        定义: 当日收盘下跌 >= 0.2%，且成交量较前一日放大。
        过去 window 个交易日内累计达到 5 次以上即构成高危顶部分配。
        """
        if ticker not in self.data:
            return 0, 0.0
        df = self.data[ticker].tail(window).copy()
        if len(df) < 5:
            return 0, 0.0

        pct_change = df["Close"].pct_change()
        vol_change = df["Volume"].pct_change()

        dist_days = (pct_change <= -0.002) & (vol_change > 0)
        count = int(dist_days.sum())
        score = min(count / 6.0, 1.0) * 100
        return count, score

    def calc_volume_divergence(self, tickers):
        """
        因子 2: 龙头量价顶背离率 (Volume Divergence)
        定义: 股价处于近20日高位 (<3%)，但20日成交均量较前期峰值萎缩超25% (无量冲顶)
        """
        divergence_flags = []
        for tk in tickers:
            if tk not in self.data:
                continue
            df = self.data[tk].tail(60).copy()
            if len(df) < 30:
                continue
            
            recent_close = df["Close"].iloc[-1]
            high_20 = df["Close"].rolling(20).max().iloc[-1]
            
            vol_sma20 = df["Volume"].rolling(20).mean()
            vol_peak = vol_sma20.max()
            current_vol_sma = vol_sma20.iloc[-1]
            
            near_top = (recent_close / high_20) >= 0.97
            vol_exhausted = (current_vol_sma / vol_peak) <= 0.75
            
            if near_top and vol_exhausted:
                divergence_flags.append(tk)

        ratio = len(divergence_flags) / max(len(tickers), 1)
        score = ratio * 100
        return divergence_flags, score

    def calc_relative_strength_divergence(self, leader="SMH", bench="SPY", window=20):
        """
        因子 3: 领涨板块与宽基走势背离 (RS Divergence)
        """
        if leader not in self.data or bench not in self.data:
            return 0.0, 0.0
        
        df_l = self.data[leader]["Close"].tail(window)
        df_b = self.data[bench]["Close"].tail(window)
        if len(df_l) < 2 or len(df_b) < 2:
            return 0.0, 0.0
        
        rs_ratio = df_l / df_b
        rs_slope = (rs_ratio.iloc[-1] - rs_ratio.iloc[0]) / rs_ratio.iloc[0]
        bench_slope = (df_b.iloc[-1] - df_b.iloc[0]) / df_b.iloc[0]

        if bench_slope >= 0 and rs_slope < 0:
            score = min(abs(rs_slope) * 2000, 100.0)
        elif rs_slope < -0.05:
            score = 80.0
        else:
            score = max(0.0, (1 - (rs_slope + 0.05) / 0.1) * 50)
            
        return float(rs_slope), float(score)

    def calc_options_sentiment(self):
        """
        因子 4: 期权 PCR 异动评分
        """
        pcr = self.pcr
        if pcr <= 0.7:
            score = 10.0
        elif pcr <= 1.0:
            score = 30.0 + (pcr - 0.7) * 100
        elif pcr <= 1.3:
            score = 60.0 + (pcr - 1.0) * 100
        else:
            score = 100.0
        return pcr, min(score, 100.0)

    def evaluate_overall_risk(self, weights=None):
        if weights is None:
            weights = {
                "dist_days": 0.30,
                "vol_div": 0.25,
                "rs_div": 0.25,
                "options_pcr": 0.20
            }

        dist_count, s_dist = self.calc_distribution_days()
        div_tickers, s_vol = self.calc_volume_divergence(["NVDA", "MSFT", "AVGO", "GOOGL", "TSM"])
        rs_val, s_rs = self.calc_relative_strength_divergence()
        pcr_val, s_pcr = self.calc_options_sentiment()

        total_score = (
            s_dist * weights["dist_days"] +
            s_vol * weights["vol_div"] +
            s_rs * weights["rs_div"] +
            s_pcr * weights["options_pcr"]
        )

        return {
            "total_score": round(total_score, 1),
            "details": {
                "distribution_days": {"count": dist_count, "score": round(s_dist, 1)},
                "volume_divergence": {"flagged": div_tickers, "score": round(s_vol, 1)},
                "rs_divergence": {"rs_slope_pct": round(rs_val * 100, 2), "score": round(s_rs, 1)},
                "options_pcr": {"pcr_value": round(pcr_val, 2), "score": round(s_pcr, 1)}
            }
        }
