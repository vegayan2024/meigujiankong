import time
import requests
from datetime import datetime
from data_loader import fetch_historical_data, fetch_put_call_ratio, AI_LEADERS, BENCHMARKS
from monitor_engine import CrashRiskEngine

# 可选：配置你的钉钉 / 企业微信 Webhook 机器人链接
WEBHOOK_URL = ""  # 例如: "https://oapi.dingtalk.com/robot/send?access_token=xxxx"

def send_alert(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚨 风险警报: {message}")
    if WEBHOOK_URL:
        try:
            payload = {"msgtype": "text", "text": {"content": f"[美股流动性监控] {message}"}}
            requests.post(WEBHOOK_URL, json=payload, timeout=5)
        except Exception as e:
            print(f"Webhook 推送失败: {e}")

def run_check():
    all_tickers = AI_LEADERS + list(BENCHMARKS.values())
    data_dict = fetch_historical_data(all_tickers, period="6mo")
    pcr = fetch_put_call_ratio("QQQ")
    
    engine = CrashRiskEngine(data_dict, pcr)
    result = engine.evaluate_overall_risk()
    
    score = result["total_score"]
    details = result["details"]
    
    log_msg = f"综合评分: {score} | 派发日: {details['distribution_days']['count']}天 | 背离标的: {details['volume_divergence']['flagged']} | PCR: {details['options_pcr']['pcr_value']}"
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 周期巡检完成 -> {log_msg}")
    
    if score >= 70:
        alert_text = f"【高危预警】综合风险分达到 {score} 分！\n- 机构派发天数: {details['distribution_days']['count']} 天\n- 量价背离龙头: {details['volume_divergence']['flagged']}\n- 建议做好IM/A股科技敞口对冲预案！"
        send_alert(alert_text)

if __name__ == "__main__":
    print(">>> 启动美股流动性自动化巡检后台进程 (每15分钟检查一次)...")
    while True:
        try:
            run_check()
        except Exception as e:
            print(f"巡检发生异常: {e}")
        time.sleep(15 * 60)
