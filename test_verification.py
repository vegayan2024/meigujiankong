# -*- coding: utf-8 -*-
"""
自动化全项回归测试脚本 (Verification Checklist Runner)
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
from monitor_engine import score_yield_curve, evaluate_all, CrashRiskEngine
from push_notifier import MobilePushNotifier
from cron_alert import run_check

def run_tests():
    print("=" * 60)
    print("美股监控系统 - 自动化综合自检与回归测试")
    print("=" * 60)

    # 1. 验证断网兜底模式下美债因子不会误触发 >= 75 分共振
    s = score_yield_curve(0.55)
    assert s < 50, f"Fallback score too high: {s}"
    print(f"[TEST 1 PASS] 美债兜底安全分: {s}")

    s2, status = score_yield_curve(0.55)
    assert s2 < 50
    print(f"[TEST 1.1 PASS] 美债解构测试: score={s2}, status={status}")

    # 2. 验证决策矩阵全空间完备性（无死区测试）
    engine = CrashRiskEngine()
    
    # 测试案例 A: [60, 60] 双中等无共振 -> 应当落入【双频温和承压】
    guide_60_60 = engine._generate_hedging_guide(s_short=60.0, s_macro=60.0, resonance=False, extreme_list=[], total_score=60.0)
    assert "双频温和承压" in guide_60_60["level"], f"Case 60/60 failed: {guide_60_60['level']}"
    print(f"[TEST 2.1 PASS] [60, 60] 成功路由至兜底象限 -> {guide_60_60['level']}")

    # 测试案例 B: [68, 52] 短高宏稳 -> 【短线战术分歧】
    guide_68_52 = engine._generate_hedging_guide(s_short=68.0, s_macro=52.0, resonance=False, extreme_list=[], total_score=60.8)
    assert "短线战术分歧" in guide_68_52["level"], f"Case 68/52 failed: {guide_68_52['level']}"
    print(f"[TEST 2.2 PASS] [68, 52] 成功路由至分歧象限 -> {guide_68_52['level']}")

    # 测试案例 C: [48, 68] 短稳宏高 -> 【宏观阴霾蓄力】
    guide_48_68 = engine._generate_hedging_guide(s_short=48.0, s_macro=68.0, resonance=False, extreme_list=[], total_score=57.0)
    assert "宏观阴霾蓄力" in guide_48_68["level"], f"Case 48/68 failed: {guide_48_68['level']}"
    print(f"[TEST 2.3 PASS] [48, 68] 成功路由至宏观蓄力 -> {guide_48_68['level']}")

    # 测试案例 D: 极端共振或总分 >= 70 -> 【高危共振警戒】带基差风控提示
    guide_res = engine._generate_hedging_guide(s_short=75.0, s_macro=75.0, resonance=True, extreme_list=["机构派发日", "科技RS走弱", "美债利差倒挂/走陡"], total_score=83.0)
    assert "高危共振警戒" in guide_res["level"]
    assert "基差风控提示" in guide_res["im_strategy"]
    assert "年化贴水率" in guide_res["im_strategy"]
    print(f"[TEST 2.4 PASS] 高危共振警戒包含实战基差提示 -> OK")

    # 测试案例 E: 低危健康 < 50
    guide_low = engine._generate_hedging_guide(s_short=30.0, s_macro=30.0, resonance=False, extreme_list=[], total_score=30.0)
    assert "双频低危健康" in guide_low["level"]
    print(f"[TEST 2.5 PASS] 低危健康 -> {guide_low['level']}")

    # 3. 验证持久化防抖文件与 2 小时设置
    notifier = MobilePushNotifier()
    assert notifier.debounce_seconds == 7200, f"Expected 7200, got {notifier.debounce_seconds}"
    # 测试突破防抖逻辑
    import time
    dedup_key = f"TEST:ALERT_{int(time.time() * 1000)}"
    # 第一次发送
    is_dup1 = notifier._is_duplicate(dedup_key, level="🟡 [双频温和承压]", score=60.0, is_resonance=False, force=False)
    assert is_dup1 is False, "First send should not be duplicate"
    # 立即再次相同级别发送 -> 应被防抖阻拦
    is_dup2 = notifier._is_duplicate(dedup_key, level="🟡 [双频温和承压]", score=62.0, is_resonance=False, force=False)
    assert is_dup2 is True, "Second identical send should be debounced"
    # 恶化跃迁：发生共振或飙升10分 -> 应穿透防抖
    is_dup3 = notifier._is_duplicate(dedup_key, level="🚨 [高危共振警戒]", score=75.0, is_resonance=True, force=False)
    assert is_dup3 is False, "Escalated breakthrough should bypass debounce"
    assert os.path.exists(".push_debounce_cache.json"), "Cache file must exist"
    print(f"[TEST 3 PASS] 持久化防抖与恶化跃迁穿透机制完全生效")

    # 4. 验证半导体 RS 连续打分无跳变
    # 比较 Slope_RS = -0.049 与 -0.051
    # 构造假数据
    import pandas as pd
    dates = pd.date_range("2026-01-01", periods=20)
    df_spy = pd.DataFrame({"Close": [100.0] * 20}, index=dates)
    # rs = -0.049
    df_smh_1 = pd.DataFrame({"Close": [100.0] + [95.1] * 19}, index=dates)
    engine_rs1 = CrashRiskEngine(data_dict={"SMH": df_smh_1, "SPY": df_spy})
    slope1, score1 = engine_rs1.calc_relative_strength_divergence()
    # rs = -0.051
    df_smh_2 = pd.DataFrame({"Close": [100.0] + [94.9] * 19}, index=dates)
    engine_rs2 = CrashRiskEngine(data_dict={"SMH": df_smh_2, "SPY": df_spy})
    slope2, score2 = engine_rs2.calc_relative_strength_divergence()
    diff = abs(score2 - score1)
    assert diff < 5.0, f"Divergence score jumped discontinuously: {score1} vs {score2} (diff={diff})"
    print(f"[TEST 4 PASS] 因子3 RS打分连续无断层: rs=-0.049 -> {score1}分, rs=-0.051 -> {score2}分 (平滑过渡)")

    # 5. 验证 Windows GBK 终端下 cron_alert 运行
    print("[TEST 5 RUN] 正在验证 cron_alert.py 运行与 Emoji 编码兼容性...")
    run_check(force_alert=False)
    print("[TEST 5 PASS] cron_alert 运行无异常，Emoji 输出正常")

    # 6. 验证核心数据缺失熔断机制 (Circuit Breaker)
    print("[TEST 6 RUN] 验证数据缺失熔断保护机制...")
    engine_empty = CrashRiskEngine(data_dict={}, pcr=None, treasury_data=None, fear_greed_data=None)
    res_empty = engine_empty.evaluate_overall_risk()
    assert res_empty["total_score"] is None, "Empty data must yield None total_score"
    assert res_empty["is_deficient"] is True, "Empty data must set is_deficient=True"
    assert "数据缺失" in res_empty["action_guide"]["level"], "Level must indicate missing data"
    print(f"[TEST 6 PASS] 空数据触发熔断保护 -> {res_empty['action_guide']['level']}")

    # 7. 验证动态权重自适应归一化 (无 PCR 条件下战术评分)
    print("[TEST 7 RUN] 验证无 PCR 条件下战术因子动态权重归一化...")
    # 构造充足的日线数据 (30日以上)
    dates_long = pd.date_range("2026-01-01", periods=35)
    def make_mock_df(base_price=100.0, step=1.0):
        closes = [base_price + i * step for i in range(35)]
        return pd.DataFrame({
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1000.0] * 35
        }, index=dates_long)

    df_qqq = make_mock_df(100.0, 1.0)
    df_spy_l = make_mock_df(100.0, 0.5)
    df_smh_l = make_mock_df(100.0, 1.5)
    data_mock = {"QQQ": df_qqq, "SPY": df_spy_l, "SMH": df_smh_l}
    for tk in ["NVDA", "MSFT", "AVGO", "GOOGL", "TSM"]:
        data_mock[tk] = make_mock_df(100.0, 0.2)

    treasury_mock = {"spread": 0.55, "spread_history": None, "source": "test", "is_fallback": False, "is_missing": False}
    fg_mock = {"score": 50.0, "rating": "neutral", "source": "test", "is_missing": False}

    # PCR 为 None 时，剩余派发日、量价背离、RS相对强弱动态加权 (30:25:25 归一化)
    engine_no_pcr = CrashRiskEngine(data_dict=data_mock, pcr=None, treasury_data=treasury_mock, fear_greed_data=fg_mock)
    res_no_pcr = engine_no_pcr.evaluate_overall_risk()
    assert res_no_pcr["short_term_score"] is not None, "Short term score must not be None when 3 factors present"
    assert res_no_pcr["short_term_details"]["options_pcr"]["score"] is None, "PCR score must be None"
    print(f"[TEST 7 PASS] 无 PCR 条件下动态归一化战术分: {res_no_pcr['short_term_score']} 分 (成功重分配权重)")

    # 8. 验证去伪高频：成交量倍数与内插估算彻底清除
    print("[TEST 8 RUN] 验证去伪高频特征...")
    from data_loader import _equity_yf
    import inspect
    import data_loader
    dl_source = inspect.getsource(data_loader)
    assert "_project_intraday_volume" not in dl_source, "Old _project_intraday_volume must be deleted"
    assert "390 / elapsed" not in dl_source, "Intraday volume scaling must be completely deleted"
    print("[TEST 8 PASS] 伪高频线性成交量放大器已彻底拔除，日线数据完全保留收盘定盘真实成交量")

    print("=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! (100% 验收通过)")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
