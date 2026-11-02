import pandas as pd
import numpy as np

class CrashRiskEngine:
    def __init__(self, data_dict, pcr_val=1.0, treasury_data=None, fear_greed_data=None):
        self.data = data_dict
        self.pcr = pcr_val
        self.treasury = treasury_data or {"spread": 0.20, "us_2y": 4.5, "us_10y": 4.7, "source": "默认基准"}
        self.fear_greed = fear_greed_data or {"score": 50.0, "rating": "neutral", "source": "默认中性"}

    # =========================================================================
    # 一、战术级高频短期信号 (Tactical Short-Term Signals - 偏微观流动性与筹码)
    # =========================================================================

    def calc_distribution_days(self, ticker="QQQ", window=25):
        """
        短频因子 1: 欧奈尔机构派发日统计 (Distribution Days)
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
        return count, round(score, 1)

    def calc_volume_divergence(self, tickers):
        """
        短频因子 2: 龙头量价顶背离率 (Volume Divergence)
        定义: 股价处于近20日高位 (<3%)，但20日成交均量较前期峰值萎缩超25% (无量冲顶)
        """
        divergence_flags = []
        for tk in tickers:
            if tk not in self.data:
                continue
            df = self.data[tk].tail(60).copy()
            if len(df) < 30:
                continue
            
            recent_close = float(df["Close"].iloc[-1])
            high_20 = float(df["Close"].rolling(20).max().iloc[-1])
            
            vol_sma20 = df["Volume"].rolling(20).mean()
            vol_peak = float(vol_sma20.max())
            current_vol_sma = float(vol_sma20.iloc[-1])
            
            near_top = (recent_close / high_20) >= 0.97
            vol_exhausted = (current_vol_sma / max(vol_peak, 1.0)) <= 0.75
            
            if near_top and vol_exhausted:
                divergence_flags.append(tk)

        ratio = len(divergence_flags) / max(len(tickers), 1)
        score = ratio * 100
        return divergence_flags, round(score, 1)

    def calc_relative_strength_divergence(self, leader="SMH", bench="SPY", window=20):
        """
        短频因子 3: 领涨板块与宽基走势背离 (RS Divergence)
        半导体作为科技主引擎，若相对大盘走弱则往往是流动性衰竭前兆。
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
            
        return float(rs_slope), round(float(score), 1)

    def calc_options_sentiment(self):
        """
        短频因子 4: 近月期权 PCR 异动评分 (Put/Call Ratio)
        衡量短期微虚值看跌期权的拥挤与对冲防守烈度。
        """
        pcr = self.pcr
        if pcr <= 0.7:
            score = 15.0
        elif pcr <= 1.0:
            score = 30.0 + (pcr - 0.7) * 100
        elif pcr <= 1.3:
            score = 60.0 + (pcr - 1.0) * 100
        else:
            score = 100.0
        return round(float(pcr), 2), round(min(score, 100.0), 1)

    # =========================================================================
    # 二、战略级中长频宏观信号 (Strategic Macro Signals - 偏信用与经济周期)
    # =========================================================================

    def calc_yield_curve_risk(self):
        """
        中频宏观因子 1: 美债 10Y-2Y 收益率曲线利差状态 (Yield Curve Spread)
        经济学逻辑: 深度倒挂提示加息过紧，而倒挂后骤然陡峭化解挂（0~0.4%）则是衰退来临的教科书级警报。
        """
        spread = float(self.treasury.get("spread", 0.20))
        if spread < -0.5:
            score = 85.0
            status = "深度倒挂 (衰退蓄力期)"
        elif spread < 0:
            score = 65.0
            status = "轻度倒挂 (加息紧缩期)"
        elif 0 <= spread < 0.35:
            score = 75.0
            status = "倒挂后解挂走陡 (高危衰退落地期)"
        elif 0.35 <= spread < 1.0:
            score = 30.0
            status = "正常陡峭化 (经济平稳)"
        else:
            score = 20.0
            status = "深度陡峭 (宽松周期)"
            
        return spread, round(score, 1), status

    def calc_sp500_sma_deviation(self, ticker="SPY"):
        """
        中频技术因子 2: 标普500针对 200日均线 (牛熊分水岭) 的乖离率
        衡量大周期牛市超买或熊市破位程度。
        """
        if ticker not in self.data:
            return 0.0, 30.0, "数据暂缺"
        df = self.data[ticker]
        if len(df) < 200:
            return 0.0, 30.0, "样本不足200日"
        
        current_close = float(df["Close"].iloc[-1])
        sma200 = float(df["Close"].rolling(200).mean().iloc[-1])
        dev_pct = (current_close - sma200) / sma200 * 100

        if dev_pct > 18.0:
            score = 90.0
            status = "极度超买悬空 (大周期顶部钝化)"
        elif dev_pct > 12.0:
            score = 70.0
            status = "偏强过热 (技术面拉高风险)"
        elif dev_pct >= 2.0:
            score = 35.0
            status = "健康慢牛趋势"
        elif dev_pct >= -3.0:
            score = 25.0
            status = "均线支撑蓄势"
        elif dev_pct >= -10.0:
            score = 55.0
            status = "破位回调测试"
        else:
            score = 80.0
            status = "严重下破熊市 (流动性紧缩)"

        return round(dev_pct, 2), round(score, 1), status

    def calc_macro_sentiment(self):
        """
        中频情绪因子 3: CNN 恐慌与贪婪指数 (Fear & Greed Index)
        反向指标: 极度贪婪往往是大顶特征，极度恐慌多见于崩盘出清段。
        """
        fg_score = float(self.fear_greed.get("score", 50.0))
        rating = self.fear_greed.get("rating", "neutral")
        
        if fg_score >= 80:
            score = 95.0
            status = f"极度贪婪 ({int(fg_score)}) - 狂热见顶信号"
        elif fg_score >= 60:
            score = 65.0
            status = f"偏向贪婪 ({int(fg_score)}) - 情绪偏热"
        elif fg_score >= 40:
            score = 35.0
            status = f"中性平衡 ({int(fg_score)}) - 情绪良性"
        elif fg_score >= 20:
            score = 45.0
            status = f"恐慌区间 ({int(fg_score)}) - 抛压释放中"
        else:
            score = 70.0
            status = f"极度恐慌 ({int(fg_score)}) - 流动性踩踏出清"

        return fg_score, round(score, 1), status

    # =========================================================================
    # 三、双频综合评估与多因子共振检测 (Dual-Frequency Fusion & Resonance)
    # =========================================================================

    def evaluate_overall_risk(self):
        # 1. 战术级高频短期信号计算
        dist_count, s_dist = self.calc_distribution_days()
        div_tickers, s_vol = self.calc_volume_divergence(["NVDA", "MSFT", "AVGO", "GOOGL", "TSM"])
        rs_val, s_rs = self.calc_relative_strength_divergence()
        pcr_val, s_pcr = self.calc_options_sentiment()

        # 短期加权: 派发日 30%, 龙头背离 25%, 科技RS 25%, 期权PCR 20%
        short_term_score = (
            s_dist * 0.30 +
            s_vol * 0.25 +
            s_rs * 0.25 +
            s_pcr * 0.20
        )

        # 2. 战略级中长频宏观信号计算
        spread_val, s_spread, spread_status = self.calc_yield_curve_risk()
        sma_dev_val, s_sma, sma_status = self.calc_sp500_sma_deviation()
        fg_val, s_fg, fg_status = self.calc_macro_sentiment()

        # 宏观中频加权: 美债利差 40%, 200日均线乖离 30%, 恐慌贪婪 30%
        macro_score = (
            s_spread * 0.40 +
            s_sma * 0.30 +
            s_fg * 0.30
        )

        # 3. 多因子极端共振检测 (Resonance Detection)
        # 统计短频和中频所有因子中达到 >= 75 分极高危的因子数量
        all_factors = {
            "机构派发日": s_dist,
            "龙头量价背离": s_vol,
            "科技RS走弱": s_rs,
            "期权PCR对冲": s_pcr,
            "美债利差倒挂/走陡": s_spread,
            "标普200日均线乖离": s_sma,
            "宏观贪婪/恐慌": s_fg
        }
        extreme_factors = [name for name, val in all_factors.items() if val >= 75.0]
        extreme_count = len(extreme_factors)
        
        resonance_active = extreme_count >= 3
        resonance_boost = 12.0 if extreme_count >= 4 else (8.0 if extreme_count == 3 else 0.0)

        # 综合评分: 短期战术占 55%，中频宏观占 45%，叠加共振加分
        base_combined = short_term_score * 0.55 + macro_score * 0.45
        total_score = min(round(base_combined + resonance_boost, 1), 100.0)

        # 4. 生成 A股 / 中证1000 (IM) 联动对冲推演行动指引
        action_guide = self._generate_hedging_guide(short_term_score, macro_score, resonance_active, extreme_factors)

        return {
            "total_score": total_score,
            "short_term_score": round(short_term_score, 1),
            "macro_score": round(macro_score, 1),
            "resonance": {
                "active": resonance_active,
                "boost": resonance_boost,
                "extreme_count": extreme_count,
                "extreme_factors": extreme_factors
            },
            "short_term_details": {
                "distribution_days": {"count": dist_count, "score": s_dist},
                "volume_divergence": {"flagged": div_tickers, "score": s_vol},
                "rs_divergence": {"rs_slope_pct": round(rs_val * 100, 2), "score": s_rs},
                "options_pcr": {"pcr_value": pcr_val, "score": s_pcr}
            },
            "macro_details": {
                "yield_spread": {"spread": spread_val, "score": s_spread, "status": spread_status, "source": self.treasury.get("source", "")},
                "sma200_deviation": {"dev_pct": sma_dev_val, "score": s_sma, "status": sma_status},
                "fear_greed": {"value": fg_val, "score": s_fg, "status": fg_status, "source": self.fear_greed.get("source", "")}
            },
            "action_guide": action_guide
        }

    def _generate_hedging_guide(self, s_short, s_macro, resonance, extreme_list):
        if resonance or s_short >= 70 and s_macro >= 65:
            return {
                "level": "[高危共振警戒] (Systemic Crash Threat)",
                "color": "red",
                "diagnosis": f"短频与中频宏观触发多因子极端共振（极端因子: {', '.join(extreme_list)}）。美股见顶下杀具有强破坏力与传染性。",
                "im_strategy": "A股（尤其是成长代表中证1000/IM）极易遭受海外流动性收缩踩踏。早盘切忌盲目抄底，若盘中出现弱反抽5日均线受阻，坚决在右侧开空 IM 或配置看跌期权保护现货敞口。"
            }
        elif s_short >= 65 and s_macro < 50:
            return {
                "level": "[短线战术分歧] (Tactical Tech Pullback)",
                "color": "orange",
                "diagnosis": "短频科技链量价背离或派发天数增加，但宏观中频（美债利差与宏观情绪）整体稳健，定性为科技成长股的高位局部休整。",
                "im_strategy": "对A股及IM期指更多体现为早盘情绪性低开扰动。低开释放浮筹后，往往易走出日内 V 型反弹或探底回升，不宜追空杀跌，可在支撑位寻找结构性博弈机会。"
            }
        elif s_short < 50 and s_macro >= 65:
            return {
                "level": "[宏观阴霾蓄力] (Macro Headwind Building)",
                "color": "yellow",
                "diagnosis": "中长频宏观因子（美债利差/估值乖离）处于敏感高危带，但短频龙头尚未明显放量砸盘。属于'雷声大雨点小'的钝化期。",
                "im_strategy": "中长线资金不可盲目加高估值杠杆，IM 多头宜逢高逐步收缩风险暴露，提高组合防御性（红利/低估值），耐心等待短频右侧信号明朗。"
            }
        else:
            return {
                "level": "[双频低危健康] (Healthy Expansion)",
                "color": "green",
                "diagnosis": "高频流动性充沛，中频宏观无显著系统性衰退风险。美股若偶发下跌多属正常洗盘。",
                "im_strategy": "外部环境对A股冲击轻微，可忽略美股短线噪音，聚焦国内核心主线与自身产业逻辑，IM期指多头底仓可继续安心持有。"
            }
