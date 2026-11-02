# 美股科技链与AI头部流动性拐点预警系统 (US AI Crash Monitor)

基于 Python + Streamlit 搭建的美股 AI 领涨股异动、市场宽度与流动性拐点监测工具，专为捕捉科技股局部见顶、流动性踩踏传导以及对冲 A 股（中证1000/IM）风险而设计。

---

## 核心监测维度 (四维风险模型)

1. **欧奈尔机构派发日 (Distribution Days):** 追踪纳指 100 ETF (QQQ) 近 25 个交易日内“放量下跌”的频率（>=5 天触发高危）。
2. **AI 龙头高位量价顶背离 (Volume Divergence):** 监测 NVDA, MSFT, AVGO, GOOGL, TSM 是否出现“价格贴近高点，但成交均量显著衰竭”的无量冲顶现象。
3. **板块相对强弱背离 (RS Divergence):** 跟踪半导体核心 ETF (SMH) 相对标普 500 (SPY) 的相对强弱斜率，识别核心引擎是否失去动能。
4. **期权 PCR 异动 (Put/Call Ratio):** 监控纳指近月期权平值/微虚值看跌期权买入拥挤度。

---

## 快速使用

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 启动可视化仪表盘
```bash
streamlit run app.py
```
终端会自动弹出本地浏览器页面（通常为 `http://localhost:8501`），支持每 10 分钟自动静默刷新。

### 3. (可选) 启动后台免值守自动巡检脚本
```bash
python cron_alert.py
```
可搭配企业微信或钉钉 Webhook，在风险评分突破 70 分时自动推送报警消息。

---

## 快速推送到 GitHub
如果你需要同步到远程仓库，在项目根目录下执行：
```bash
git init
git add .
git commit -m "feat: init meigujiankong risk monitor"
git branch -M main
git remote add origin https://github.com/vegayan2024/meigujiankong.git
git push -u origin main
```
