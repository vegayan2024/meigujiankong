# -*- coding: utf-8 -*-
"""
美股科技链与流动性定盘监测巡检进程 (EOD Morning Alert)
======================================================
运行模式：
  1. 默认单次运行 (EOD/晨会定盘模式，推荐配合 Windows 任务计划程序在每个工作日 07:30 执行)
  2. 守护进程模式 (--daemon，自动休眠至下一工作日 07:30 执行)
  3. 强制测试模式 (--force，强制立即推送通知测试通道)
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import argparse
import time
import os
from datetime import datetime, timedelta, timezone
from data_loader import fetch_all_inputs
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


def get_seconds_until_next_run(target_hour: int = 7, target_minute: int = 30) -> float:
    """
    计算距离下一个工作日 07:30 (北京时间 UTC+8) 的休眠秒数
    """
    tz_bj = timezone(timedelta(hours=8))
    now = datetime.now(tz_bj)
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    # 如果今天已经过了目标时间，或者今天是周末，移到下一个交易日
    if target <= now:
        target += timedelta(days=1)
    
    # 跳过周六 (5) 和 周日 (6)
    while target.weekday() >= 5:
        target += timedelta(days=1)

    return max((target - now).total_seconds(), 60.0)


def run_check(force_alert: bool = False):
    bundle = fetch_all_inputs()
    data_dict = bundle["equity"]["data"]
    pcr = bundle["pcr"]
    treasury = bundle["treasury"]
    fear_greed = bundle["fear_greed"]
    coverage = bundle.get("coverage", {})
    cov_ok, cov_total = coverage.get("ok", "?"), coverage.get("total", "?")
    cov_text = f"日线覆盖 {cov_ok}/{cov_total} 标的"
    if coverage.get("missing"):
        cov_text += f"（缺失: {', '.join(coverage['missing'])}）"

    # 数据质量与溯源透明打印
    if bundle.get("status"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 数据源状态感知:")
        for issue in bundle["status"]:
            print(f"  - [{issue['level'].upper()}] {issue['item']}: {issue['detail']}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 评分数据覆盖: {cov_text}")

    engine = CrashRiskEngine(data_dict, pcr, treasury, fear_greed)
    result = engine.evaluate_overall_risk()

    total_score = result["total_score"]
    short_score = result["short_term_score"]
    macro_score = result["macro_score"]
    resonance = result["resonance"]
    guide = result["action_guide"]
    is_deficient = result.get("is_deficient", False)

    # 1. 缺失数据熔断分支
    if is_deficient or total_score is None:
        log_msg = (f"综合: ⚠️ 数据不足触发熔断保护 (战术: {short_score} | 宏观: {macro_score}) | "
                   f"状态: {guide['level']} | {cov_text}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 定盘巡检完成 -> {log_msg}")

        if force_alert:
            title = "【美股定盘风控报告】⚠️ 核心指标缺失，触发数据熔断保护"
            content = (
                f"**状态定性**：{guide['level']}\n\n"
                f"- ⚡ 战术定盘风险: **{short_score if short_score is not None else '数据不足'}**\n"
                f"- 🌐 中长宏观风险: **{macro_score if macro_score is not None else '数据不足'}**\n"
                f"- 📊 **数据覆盖：{cov_text}**\n\n"
                f"**核心诊断**：\n{guide['diagnosis']}\n\n"
                f"**A股/IM期指对冲指引**：\n{guide['im_strategy']}"
            )
            send_alert(
                title=title,
                content=content,
                level=guide["level"],
                score=0.0,
                is_resonance=False,
                force=True
            )
        return

    # 2. 正常评分分支
    log_msg = (f"综合: {total_score}分 (战术: {short_score} | 宏观: {macro_score}) | "
               f"共振: {resonance['active']} | 状态: {guide['level']} | {cov_text}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 定盘巡检完成 -> {log_msg}")

    # 触发告警：综合分 >= 70，或触发极端共振，或人工强制测试
    if total_score >= 70 or resonance["active"] or force_alert:
        if force_alert and total_score < 70 and not resonance["active"]:
            title = f"【美股晨会定盘测试】综合风险分: {total_score} 分"
        else:
            title = f"【美股晨会定盘预警】综合风险分突破 {total_score} 分！"

        content = (
            f"**状态定性**：{guide['level']}\n\n"
            f"- ⚡ 战术定盘风险: **{short_score}** 分\n"
            f"- 🌐 中长宏观风险: **{macro_score}** 分\n"
            f"- 🔥 极端共振: {'已触发多因子共振' if resonance['active'] else '未触发'}\n"
            f"- 📊 **数据覆盖：{cov_text}**\n\n"
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
    parser = argparse.ArgumentParser(description="美股科技链与流动性晨会定盘巡检脚本")
    parser.add_argument("--force", action="store_true", help="强制发送手机端预警测试推送")
    parser.add_argument("--daemon", action="store_true", help="以守护进程模式运行，休眠至下一工作日07:30自动执行")
    args = parser.parse_args()

    if args.daemon:
        print(">>> 启动美股流动性晨会定盘守护进程模式 (每工作日 07:30 北京时间自动定盘)...")
        while True:
            try:
                run_check(force_alert=args.force)
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 巡检异常: {e}")
            sleep_sec = get_seconds_until_next_run(7, 30)
            next_run_dt = datetime.now() + timedelta(seconds=sleep_sec)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 巡检已完成。下次定盘时间: {next_run_dt.strftime('%Y-%m-%d %H:%M:%S')} (休眠 {sleep_sec/3600:.2f} 小时)")
            time.sleep(sleep_sec)
    else:
        print(">>> 执行美股科技链与流动性晨会定盘单次巡检...")
        run_check(force_alert=args.force)
