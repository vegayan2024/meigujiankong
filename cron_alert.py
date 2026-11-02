import time
import requests
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

# 可选：配置你的钉钉 / 企业微信 Webhook 机器人链接
WEBHOOK_URL = ""  # 例如: "https://oapi.dingtalk.com/robot/send?access_token=xxxx"

def send_alert(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ALERT] 风险警报: {message}")
    if WEBHOOK_URL:
        try:
            payload = {"msgtype": "text", "text": {"content": f"[美股流动性双频监控] {message}"}}
            requests.post(WEBHOOK_URL, json=payload, timeout=5)
        except Exception as e:
            print(f"Webhook 推送失败: {e}")

def run_check():
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
    
    if total_score >= 70 or resonance["active"]:
        alert_text = (
            f"【美股双频监控高危警报】综合风险评分达到 {total_score} 分！\n"
            f"- ⚡ 高频短线风险分: {short_score}\n"
            f"- 🌐 中长宏观风险分: {macro_score}\n"
            f"- 🔥 共振状态: {'已触发多因子极端共振' if resonance['active'] else '未触发'}\n"
            f"- 🎯 对冲推演建议: {guide['im_strategy']}"
        )
        send_alert(alert_text)

if __name__ == "__main__":
    print(">>> 启动美股流动性双频自动化巡检后台进程 (每15分钟检查一次)...")
    while True:
        try:
            run_check()
        except Exception as e:
            print(f"巡检发生异常: {e}")
        time.sleep(15 * 60)
