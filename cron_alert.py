import time
import os
from datetime import datetime
from data_loader import (
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

def send_alert(title: str, content: str, force: bool = False):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ALERT] 正在分发手机端推送: {title}")
    res = notifier.send_alert(title=title, content=content, force=force)
    if not res.get("ok"):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 推送提示: {res.get('msg')}")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 推送成功: {res.get('details')}")

def run_check(force_alert: bool = False):
    all_tickers = AI_LEADERS + list(BENCHMARKS.values())
    data_dict = fetch_historical_data(all_tickers, period="1y")
    pcr = fetch_put_call_ratio("QQQ")
    treasury = fetch_treasury_yields()
    fear_greed = fetch_fear_and_greed()
    
    engine = CrashRiskEngine(data_dict, pcr, treasury, fear_greed)
    result = engine.evaluate_overall_risk()
    
    total_score = result["total_score"]
    short_score = result["short_term_score"]
    macro_score = result["macro_score"]
    resonance = result["resonance"]
    guide = result["action_guide"]
    
    log_msg = f"综合: {total_score}分 (短频: {short_score} | 宏观: {macro_score}) | 共振: {resonance['active']} | 状态: {guide['level']}"
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 周期巡检完成 -> {log_msg}")
    
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
        send_alert(title, content, force=force_alert)

if __name__ == "__main__":
    print(">>> 启动美股流动性双频自动化巡检后台进程 (每15分钟检查一次，集成手机端多通道推送)...")
    while True:
        try:
            run_check()
        except Exception as e:
            print(f"巡检发生异常: {e}")
        time.sleep(15 * 60)
