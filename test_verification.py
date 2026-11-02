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

    print("=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! (100% 验收通过)")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
