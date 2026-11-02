# -*- coding: utf-8 -*-
"""
美股流动性双频自动化巡检后台进程 (Cron Alert)
==============================================
支持：
  1. 定时后台执行（支持 15 分钟循环或系统计划任务单次调用）
  2. UTF-8 Windows 控制台防崩溃编码配置
  3. 异常与缺失数据透明呈现（不隐瞒、不静默兜底）
  4. 手机端即时多通道推送（持久化 2 小时防抖 + 恶化跃迁穿透）
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import os
from datetime import datetime
from data_loader import (
    fetch_all_inputs,
    fetch_historical_data,
    fetch_put_call_ratio,
    fetch_treasury_yields,
    fetch_fear_and_greed,
    AI_LEADERS,
    BENCHMARKS
)
from monitor_engine import CrashRiskEngine
from push_notifier import MobilePushNotifier

notifier = MobilePushNotifier()


def send_alert(title: str, content: str, level: str = "ALERT", score: float = 0.0, is_resonance: bool = False, force: bool = False):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ALERT] 正在分发手机端推送: {title}")
    res = notifier.send_alert(
        title=title,
        content=content,
        level=level,
        score=score,
        is_resonance=is_resonance,
        force=force
    )
    if not res.get("ok"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 推送提示: {res.get('msg')}")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 推送成功: {res.get('details')}")


def run_check(force_alert: bool = False):
    bundle = fetch_all_inputs()
    data_dict = bundle["equity"]["data"]
    pcr = bundle["pcr"]
    treasury = bundle["treasury"]
    fear_greed = bundle["fear_greed"]

    # 数据质量与溯源透明打印
    if bundle.get("status"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 数据源状态感知:")
        for issue in bundle["status"]:
            print(f"  - [{issue['level'].upper()}] {issue['item']}: {issue['detail']}")

    engine = CrashRiskEngine(data_dict, pcr, treasury, fear_greed)
    result = engine.evaluate_overall_risk()

    total_score = result["total_score"]
    short_score = result["short_term_score"]
    macro_score = result["macro_score"]
    resonance = result["resonance"]
    guide = result["action_guide"]

    log_msg = f"综合: {total_score}分 (短频: {short_score} | 宏观: {macro_score}) | 共振: {resonance['active']} | 状态: {guide['level']}"
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 周期巡检完成 -> {log_msg}")

    # 触发告警：综合分 >= 70，或触发极端共振，或人工强制测试
    if total_score >= 70 or resonance["active"] or force_alert:
        title = f"【美股双频预警】综合风险分突破 {total_score} 分！"
        content = (
            f"**状态定性**：{guide['level']}\n\n"
            f"- ⚡ 高频短线风险: **{short_score}** 分\n"
            f"- 🌐 中长宏观风险: **{macro_score}** 分\n"
            f"- 🔥 极端共振: {'已触发多因子共振' if resonance['active'] else '未触发'}\n\n"
            f"**核心诊断**：\n{guide['diagnosis']}\n\n"
            f"**A股/IM期指对冲指引**：\n{guide['im_strategy']}"
        )
        send_alert(
            title=title,
            content=content,
            level=guide["level"],
            score=total_score,
            is_resonance=resonance["active"],
            force=force_alert
        )


if __name__ == "__main__":
    print(">>> 启动美股流动性双频自动化巡检后台进程 (每15分钟检查一次，集成手机端多通道推送)...")
    while True:
        try:
            run_check()
        except Exception as e:
            print(f"巡检发生异常: {e}")
        time.sleep(15 * 60)
