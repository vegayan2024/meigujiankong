# -*- coding: utf-8 -*-
"""
美股科技链与宏观流动性风险量化引擎 (Crash Risk Engine)
======================================================
分层架构：
  1. ⚡ 战术级高频短期信号 (机构派发日 / 龙头量价背离 / 半导体RS动能 / 期权PCR)
  2. 🌐 战略级中长频宏观信号 (美债10Y-2Y曲线 / 标普200日均线乖离 / CNN恐慌贪婪)
  3. 🔗 完备互斥阶梯状态机决策矩阵 (无死区覆盖全状态空间)
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pandas as pd
import numpy as np


class ScoreResult(float):
    """同时支持数值比较 (s < 50) 与元组解包 (score, status = ...) 的自适应对象"""
    def __new__(cls, val, stat):
        obj = super().__new__(cls, val)
        obj.status = stat
        return obj

    def __iter__(self):
        yield float(self)
        yield self.status


def score_yield_curve(spread: float, is_fallback: bool = False, spread_history=None):
    """
    美债 10Y-2Y 利差打分独立函数 (支持路径感知与兜底防误报)
    """
    if is_fallback or abs(spread - 0.55) < 1e-4:
        # 断网或兜底基准值 0.55%：锁定为安全分 30.0，严禁触发 >= 75 分共振
        return ScoreResult(30.0, "默认安全基准 (平稳扩张)")

    had_deep_inversion = False
    if spread_history:
        try:
            had_deep_inversion = any(float(s) < -0.35 for s in spread_history)
        except Exception:
            had_deep_inversion = False

    if spread < -0.35:
        score = 85.0
        status = "深度倒挂 (强确定性加息衰退蓄力期)"
    elif spread < 0:
        score = 65.0
        status = "轻度倒挂 (加息紧缩期)"
    elif 0.0 <= spread < 0.35:
        if had_deep_inversion:
            score = 75.0
            status = "倒挂后急剧走陡解挂 (高危牛陡衰退落地期)"
        else:
            score = 55.0
            status = "解挂观察期 (温和紧缩/软着陆观察)"
    elif 0.35 <= spread < 1.0:
        score = 30.0
        status = "正常陡峭化 (经济平稳)"
    else:
        score = 20.0
        status = "深度陡峭 (宽松周期)"

    return ScoreResult(round(float(score), 1), status)


class CrashRiskEngine:
    def __init__(self, data_dict=None, pcr_val=1.0, treasury_data=None, fear_greed_data=None):
        self.data = data_dict or {}
        self.pcr = self._unpack_pcr(pcr_val)
        self.treasury = self._unpack_treasury(treasury_data)
        self.fear_greed = self._unpack_fear_greed(fear_greed_data)

    def _unpack_pcr(self, pcr_val):
        if isinstance(pcr_val, dict):
            val = pcr_val.get("value") if "value" in pcr_val else pcr_val
            if isinstance(val, dict):
                return float(val.get("pcr", 1.0))
            try:
                return float(val)
            except Exception:
                return 1.0
        try:
            return float(pcr_val)
        except Exception:
            return 1.0

    def _unpack_treasury(self, t_data):
        if not t_data:
            return {"spread": 0.55, "us_2y": 4.5, "us_10y": 5.05, "source": "默认安全基准", "is_fallback": True}
        if isinstance(t_data, dict) and "value" in t_data and isinstance(t_data["value"], dict):
            d = dict(t_data["value"])
            d["source"] = t_data.get("source", "外部接口")
            d["tier"] = t_data.get("tier", "secondary")
            return d
        if isinstance(t_data, dict):
            return t_data
        return {"spread": 0.55, "us_2y": 4.5, "us_10y": 5.05, "source": "默认安全基准", "is_fallback": True}

    def _unpack_fear_greed(self, fg_data):
        if not fg_data:
            return {"score": 50.0, "rating": "neutral", "source": "默认中性"}
        if isinstance(fg_data, dict) and "value" in fg_data and isinstance(fg_data["value"], dict):
            d = dict(fg_data["value"])
            d["source"] = fg_data.get("source", "CNN")
            return d
        if isinstance(fg_data, dict):
            return fg_data
        return {"score": 50.0, "rating": "neutral", "source": "默认中性"}

    # =========================================================================
    # 一、战术级高频短期信号 (Tactical Short-Term Signals)
    # =========================================================================

    def calc_distribution_days(self, ticker="QQQ", window=25):
        """
        短频因子 1: 欧奈尔机构派发日统计 (带 V 型强反转重置机制)
        定义: 当日收盘下跌 >= 0.2%，且成交量较前一日放大。
        反转重置规则: 若自过去 25 日最低点累计反弹 >= 6.0% 且站上 20 日均线，
        则最低点之前的旧派发日权重减半（按 0.5 次计）。
        """
        if ticker not in self.data or self.data[ticker].empty:
            return 0.0, 0.0
        df = self.data[ticker].tail(window).copy()
        if len(df) < 5:
            return 0.0, 0.0

        pct_change = df["Close"].pct_change()
        vol_change = df["Volume"].pct_change()

        dist_days = (pct_change <= -0.002) & (vol_change > 0)

        # 欧奈尔反转重置判定
        low_idx = df["Close"].idxmin()
        lowest_close = float(df["Close"].loc[low_idx])
        current_close = float(df["Close"].iloc[-1])
        sma20 = float(df["Close"].rolling(20, min_periods=5).mean().iloc[-1])
        rebound_pct = (current_close - lowest_close) / max(lowest_close, 1e-6)
        is_reversal = (rebound_pct >= 0.06) and (current_close > sma20)

        effective_count = 0.0
        for dt, is_dist in dist_days.items():
            if is_dist:
                if is_reversal and dt < low_idx:
                    effective_count += 0.5  # 前期急跌途中的旧派发日减半
                else:
                    effective_count += 1.0

        score = min(effective_count / 6.0, 1.0) * 100.0
        return round(effective_count, 1), round(score, 1)

    def calc_volume_divergence(self, tickers):
        """
        短频因子 2: 龙头量价顶背离率 (带价格动能衰竭确认)
        条件 1: 股价处于近20日高位 (<3%)
        条件 2: 20日成交均量较前期峰值萎缩超25%
        条件 3: 动能衰竭确认 (近5日至少2天上影线/阴线，或 14日RSI 较20日峰值回落超5点)
        """
        divergence_flags = []
        for tk in tickers:
            if tk not in self.data or self.data[tk].empty:
                continue
            df = self.data[tk].tail(60).copy()
            if len(df) < 30:
                continue

            recent_close = float(df["Close"].iloc[-1])
            high_20 = float(df["Close"].rolling(20).max().iloc[-1])

            vol_sma20 = df["Volume"].rolling(20).mean()
            vol_peak = float(vol_sma20.max())
            current_vol_sma = float(vol_sma20.iloc[-1])

            near_top = (recent_close / max(high_20, 1e-6)) >= 0.97
            vol_exhausted = (current_vol_sma / max(vol_peak, 1.0)) <= 0.75

            # 动能衰竭双重校验
            recent_5 = df.tail(5)
            high_low_span = recent_5["High"] - recent_5["Low"] + 1e-6
            upper_shadow = (recent_5["High"] - recent_5["Close"]) / high_low_span
            shadow_exhausted = int((upper_shadow > 0.6).sum()) >= 2

            # 14日 RSI 顶背离检测
            delta = df["Close"].diff()
            gain = delta.clip(lower=0).rolling(14, min_periods=7).mean()
            loss = (-delta.clip(upper=0)).rolling(14, min_periods=7).mean()
            rs = gain / (loss + 1e-9)
            rsi = 100.0 - (100.0 / (1.0 + rs))
            rsi_recent = float(rsi.iloc[-1])
            rsi_max_20 = float(rsi.tail(20).max())
            rsi_divergence = (rsi_max_20 - rsi_recent) > 5.0

            if near_top and vol_exhausted and (shadow_exhausted or rsi_divergence):
                divergence_flags.append(tk)

        ratio = len(divergence_flags) / max(len(tickers), 1)
        score = ratio * 100.0
        return divergence_flags, round(score, 1)

    def calc_relative_strength_divergence(self, leader="SMH", bench="SPY", window=20):
        """
        短频因子 3: 领涨板块与宽基走势背离 (连续分段线性映射 + 顶背离加成)
        消除跳变断层，兼顾正常板块轮动与崩塌预警。
        """
        if leader not in self.data or bench not in self.data:
            return 0.0, 0.0
        df_l = self.data[leader]["Close"].tail(window)
        df_b = self.data[bench]["Close"].tail(window)
        if len(df_l) < 2 or len(df_b) < 2:
            return 0.0, 0.0

        rs_ratio = df_l / df_b
        rs_slope = float((rs_ratio.iloc[-1] - rs_ratio.iloc[0]) / max(rs_ratio.iloc[0], 1e-6))
        bench_slope = float((df_b.iloc[-1] - df_b.iloc[0]) / max(df_b.iloc[0], 1e-6))

        # 1. 基础相对强弱分 Base_rs (连续无断层映射)
        if rs_slope >= 0.05:
            base_rs = 10.0
        elif 0.0 <= rs_slope < 0.05:
            base_rs = 10.0 + (0.05 - rs_slope) / 0.05 * 25.0
        elif -0.08 <= rs_slope < 0.0:
            base_rs = 35.0 + (abs(rs_slope) / 0.08) * 50.0
        else:  # rs_slope < -0.08
            base_rs = min(85.0 + (abs(rs_slope) - 0.08) / 0.04 * 15.0, 100.0)

        # 2. 顶背离惩罚 Penalty_div: 标普大盘涨超1%但芯片跑输超2%
        penalty_div = 0.0
        if bench_slope > 0.01 and rs_slope < -0.02:
            penalty_div = 15.0

        final_score = min(base_rs + penalty_div, 100.0)
        return float(rs_slope), round(float(final_score), 1)

    def calc_options_sentiment(self):
        """
        短频因子 4: 近月期权 PCR 异动评分
        """
        pcr = self.pcr
        if pcr <= 0.7:
            score = 15.0
        elif pcr <= 1.0:
            score = 30.0 + (pcr - 0.7) * 100.0
        elif pcr <= 1.3:
            score = 60.0 + (pcr - 1.0) * 100.0
        else:
            score = 100.0
        return round(float(pcr), 2), round(min(score, 100.0), 1)

    # =========================================================================
    # 二、战略级中长频宏观信号 (Strategic Macro Signals)
    # =========================================================================

    def calc_yield_curve_risk(self):
        """
        中频宏观因子 1: 美债 10Y-2Y 收益率曲线利差 (带路径感知与兜底安全约束)
        """
        spread = float(self.treasury.get("spread", 0.55))
        is_fallback = bool(self.treasury.get("is_fallback", False))
        spread_history = self.treasury.get("spread_history")
        score, status = score_yield_curve(spread, is_fallback=is_fallback, spread_history=spread_history)
        return spread, score, status

    def calc_sp500_sma_deviation(self, ticker="SPY"):
        """
        中频技术因子 2: 标普500针对 200日均线的乖离率
        """
        if ticker not in self.data or self.data[ticker].empty:
            return 0.0, 30.0, "数据暂缺"
        df = self.data[ticker]
        if len(df) < 200:
            return 0.0, 30.0, "样本不足200日"

        current_close = float(df["Close"].iloc[-1])
        sma200 = float(df["Close"].rolling(200).mean().iloc[-1])
        dev_pct = (current_close - sma200) / max(sma200, 1e-6) * 100.0

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
        中频情绪因子 3: CNN 恐慌与贪婪指数
        """
        fg_score = float(self.fear_greed.get("score", 50.0))
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
    # 三、双频综合评估与完备阶梯决策矩阵
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

        # 3. 多因子极端共振检测
        all_factors = {
            "机构派发日": s_dist,
            "龙头量价背离": s_vol,
            "科技RS走弱": s_rs,
            "期权PCR对冲": s_pcr,
            "美债利差倒挂/走陡": s_spread,
            "标普200日均线乖离": s_sma,
            "宏观贪婪/恐慌": s_fg
        }
        # 严禁兜底数据触发共振计数
        if self.treasury.get("is_fallback"):
            all_factors.pop("美债利差倒挂/走陡", None)

        extreme_factors = [name for name, val in all_factors.items() if val >= 75.0]
        extreme_count = len(extreme_factors)

        resonance_active = extreme_count >= 3
        resonance_boost = 12.0 if extreme_count >= 4 else (8.0 if extreme_count == 3 else 0.0)

        # 综合评分: 短期战术占 55%，中频宏观占 45%，叠加共振加分
        base_combined = short_term_score * 0.55 + macro_score * 0.45
        total_score = min(round(base_combined + resonance_boost, 1), 100.0)

        # 4. 生成完备无死区 A股 / 中证1000 (IM) 联动对冲推演行动指引
        action_guide = self._generate_hedging_guide(
            short_term_score, macro_score, resonance_active, extreme_factors, total_score=total_score
        )

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

    def _generate_hedging_guide(self, s_short, s_macro, resonance, extreme_list, total_score=0.0):
        """
        完备互斥的阶梯状态机决策矩阵（覆盖全状态空间，无逻辑死区）
        """
        im_risk_warning = (
            "\n\n> **【基差风控提示】** 开空 IM 期指前务必核查当前年化贴水率；"
            "若早盘大幅低开导致 IM 年化贴水率 > 15%，严禁盲目追空期指以防基差收敛逼空，"
            "应优先改用买入中证1000看跌期权（MO）构建熊市价差进行保护。"
        )

        # 1. 🚨 高危共振警戒：Score_total >= 70 或 触发多因子共振
        if total_score >= 70 or resonance:
            return {
                "level": "🚨 [高危共振警戒] (Systemic Crash Threat)",
                "color": "red",
                "diagnosis": f"短频与中频宏观触发多因子极端共振（极端因子: {', '.join(extreme_list)}）。美股见顶下杀具有强破坏力与传染性。",
                "im_strategy": "A股（尤其是成长代表中证1000/IM）极易遭受海外流动性收缩踩踏。早盘切忌盲目抄底，若盘中出现弱反抽5日均线受阻，坚决在右侧开空 IM 或配置看跌期权保护现货敞口。" + im_risk_warning
            }

        # 2. ⚠️ 短线战术分歧：Score_total < 70 且未共振，短频高(>=65)而宏观稳(<55)
        elif total_score < 70 and not resonance and s_short >= 65 and s_macro < 55:
            return {
                "level": "⚠️ [短线战术分歧] (Tactical Tech Pullback)",
                "color": "orange",
                "diagnosis": "短频科技链量价背离或派发天数增加，但宏观中频（美债利差与宏观情绪）整体稳健，定性为科技成长股的高位局部休整。",
                "im_strategy": "对A股及IM期指更多体现为早盘情绪性低开扰动。低开释放浮筹后，往往易走出日内 V 型反弹或探底回升，不宜追空杀跌，可在支撑位寻找结构性博弈机会。"
            }

        # 3. ⏳ 宏观阴霾蓄力：Score_total < 70 且未共振，宏观高(>=65)而短频尚未破位(<55)
        elif total_score < 70 and not resonance and s_macro >= 65 and s_short < 55:
            return {
                "level": "⏳ [宏观阴霾蓄力] (Macro Headwind Building)",
                "color": "yellow",
                "diagnosis": "中长频宏观因子（美债利差/估值乖离）处于敏感高危带，但短频龙头尚未明显放量砸盘。属于'雷声大雨点小'的钝化期。",
                "im_strategy": "中长线资金不可盲目加高估值杠杆，IM 多头宜逢高逐步收缩风险暴露，提高组合防御性（红利/低估值），耐心等待短频右侧信号明朗。"
            }

        # 4. 🟡 双频温和承压（新增闭环兜底象限）：50 <= Score_total < 70 且不满足上述单边分歧条件
        elif 50.0 <= total_score < 70.0:
            return {
                "level": "🟡 [双频温和承压] (Moderate Pressure / Chop)",
                "color": "yellow",
                "diagnosis": "短频筹码与中频宏观均处于中等偏紧状态，市场缺乏单边突破动能，波动率中枢抬升。",
                "im_strategy": "【控制仓位/网格波段】A股与中证1000易陷入宽幅震荡，IM 期指不宜追涨杀跌；建议将多头敞口降至半仓以下，或采用买平值看跌期权（MO）构建领口策略（Collar）锁住尾部风险。"
            }

        # 5. 🟢 双频低危健康：Score_total < 50
        else:
            return {
                "level": "🟢 [双频低危健康] (Healthy Expansion)",
                "color": "green",
                "diagnosis": "高频流动性充沛，中频宏观无显著系统性衰退风险。美股若偶发下跌多属正常洗盘。",
                "im_strategy": "外部环境对A股冲击轻微，可忽略美股短线噪音，聚焦国内核心主线与自身产业逻辑，IM期指多头底仓可继续安心持有。"
            }


def evaluate_all(data_dict=None, pcr_val=1.0, treasury_data=None, fear_greed_data=None):
    """便捷入口函数"""
    engine = CrashRiskEngine(data_dict, pcr_val=pcr_val, treasury_data=treasury_data, fear_greed_data=fear_greed_data)
    return engine.evaluate_overall_risk()
