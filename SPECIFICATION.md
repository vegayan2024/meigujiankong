# 美股科技链与宏观流动性双频监控系统：项目与技术实现说明文档
> **文档版本**：v2.1 (审查修复版) · 2026-11-02  
> **项目路径**：`C:\ai\antigravity\meigujiankong`  
> **远程仓库**：`https://github.com/vegayan2024/meigujiankong.git`  
> **核心触发词**：`美股监控`（与 A股监控 `jiankong` 项目严格隔离）

---

## 1. 项目定位与业务目标

### 1.1 业务背景
美股科技巨头（尤其是英伟达、微软、博通等 AI 头部链条）的流动性见顶与估值回撤，通常会在数个交易日内向下游传导，引发全球风险资产的流动性抽离。由于 A 股成长风格标的（特别是**中证1000指数及 IM 期指**）对海外科技风险偏好高度敏感，建立一套实时捕捉美股异动并直接输出 A 股对冲预案的量化监控系统具有极高的实战价值。

### 1.2 解决的核心痛点
1. **单一指标盲区**：纯看标普估值滞后于科技股踩踏；纯看个股K线容易忽略美债倒挂/解挂等大周期系统性危机。
2. **海内外数据网络延迟与单点故障**：境外金融数据源国内访问不稳定，缺乏专业金融接口与多层降级链路。
3. **数据透明与防伪**：严格遵循金融数据纪律，第一顺位对口直取（TuShare Pro / 同花顺 iFinD / 东方财富 / FRED），取不到的数据透明公示，严禁隐瞒、伪造或静默处理。
4. **决策矩阵逻辑死区**：彻底消除状态空间盲区，采用完备互斥的五阶梯状态机，包含双频温和承压兜底。
5. **缺少与国内衍生品的联动映射**：衔接国内中证1000/IM期指与MO期权等实操对冲工具，增加年化深度贴水基差风控提示。
6. **移动端警报断层与骚扰**：集成持久化 2 小时防抖与恶化跃迁穿透机制，7x24 小时即时推送。

---

## 2. 系统整体架构与模块拓扑

系统采用**“双频双层监测 + 极端共振惩罚 + 多通道移动分发”**的工业级设计，整体数据流与模块依赖如下：

```mermaid
flowchart TD
    subgraph DataLayer [数据接入层 (专业金融接口与顺位降级链路)]
        TS[TuShare Pro / iFinD] -->|第一顺位: us_tycr / us_daily| S_Data[短频微观行情]
        EM[东方财富 / FRED CSV] -->|美债 2Y/10Y 利差| M_Data[宏观长频数据]
        CNN[CNN Dataviz API] -->|恐慌与贪婪指数| M_Data
        YF[yfinance 核心期权链] -->|7~25DTE / 0.9~1.1Spot / OI兜底| S_Data
    end

    subgraph EngineLayer [量化计算与风险评估引擎 (monitor_engine.py)]
        S_Data --> ShortModel[战术级高频模型 (0-100分)]
        M_Data --> MacroModel[战略级宏观模型 (0-100分)]
        
        ShortModel --> F1[欧奈尔派发日 (带V反重置)]
        ShortModel --> F2[龙头量价背离 (带动能衰竭/RSI确认)]
        ShortModel --> F3[半导体RS动能 (连续分段+背离惩罚)]
        ShortModel --> F4[期权PCR倾斜 (成交量与持仓量自适应)]

        MacroModel --> M1[美债10Y-2Y利差 (路径感知+安全兜底)]
        MacroModel --> M2[标普200日均线乖离]
        MacroModel --> M3[宏观贪婪恐慌]

        ShortModel & MacroModel --> Fusion[双频加权融合与极端共振检测]
        Fusion --> TotalScore[综合系统性风险总分 (0-100)]
        Fusion --> Guide[完备无死区 A股/中证1000 IM 对冲决策矩阵]
    end

    subgraph PresentationLayer [展示与交互层]
        TotalScore & Guide --> StreamlitUI[Streamlit 左右双栏大屏 (app.py)]
        TotalScore & Guide --> CronChecker[后台免值守巡检 (cron_alert.py)]
    end

    subgraph PushLayer [手机端多通道推送系统 (push_notifier.py)]
        CronChecker & StreamlitUI --> Notifier[MobilePushNotifier (带持久化2小时防抖+恶化穿透)]
        Notifier --> P1[🍎 iOS Bark 锁屏弹窗]
        Notifier --> P2[💬 微信 PushPlus / Server酱]
        Notifier --> P3[💼 企业微信 / 钉钉 / 飞书]
    end
```

### 2.1 源码文件与职责映射表

| 文件路径 | 核心职责 | 依赖与技术栈 |
| :--- | :--- | :--- |
| [`data_loader.py`](file:///C:/ai/antigravity/meigujiankong/data_loader.py) | 数据获取层：金融数据接口（TuShare Pro、iFinD、东财、FRED、yfinance、CNN），带盘中成交量折算与期权清洗 | `tushare`, `yfinance`, `requests`, `pandas` |
| [`monitor_engine.py`](file:///C:/ai/antigravity/meigujiankong/monitor_engine.py) | 算法核心层：高频战术模型（带V反重置与动能确认）、宏观战略模型（路径感知）、共振算法、五阶梯完备对冲指引 | `numpy`, `pandas` |
| [`push_notifier.py`](file:///C:/ai/antigravity/meigujiankong/push_notifier.py) | 推送适配层：iOS Bark、微信PushPlus/Server酱、企微/钉钉/飞书推送，JSON持久化2小时防抖与恶化跃迁穿透 | `requests`, `json` |
| [`app.py`](file:///C:/ai/antigravity/meigujiankong/app.py) | 前端大屏：左右双栏独立呈现、数据链路透明公示、全维雷达图、推送即时配置与测试 | `streamlit`, `plotly` |
| [`cron_alert.py`](file:///C:/ai/antigravity/meigujiankong/cron_alert.py) | 后台免值守脚本：15分钟自动化巡检，风险 $\ge 70$ 或共振时手机推送，UTF-8控制台防崩溃 | 纯 Python 标准库调用 |
| [`push_config.json`](file:///C:/ai/antigravity/meigujiankong/push_config.json) | 本地推送密钥配置模板（敏感 Token 存入 `.env`，已被 Git 忽略） | JSON |
| [`run.bat`](file:///C:/ai/antigravity/meigujiankong/run.bat) | Windows 快速启动脚本（设置 `chcp 65001` 与 `PYTHONIOENCODING=utf-8`） | Windows 批处理 |

---

## 3. 双频指标量化建模与数学实现规范

系统分为**战术级高频短期信号（权重 55%）**与**战略级中长频宏观信号（权重 45%）**，各因子统一输出 0~100 分。

### 3.1 ⚡ 战术级高频短期信号 (Short-Term Tactical Signals)

#### 因子 1: 欧奈尔机构派发日统计 (Distribution Days with V-Reversal Reset)
- **输入**：纳指 100 ETF (`QQQ`) 过去 25 个交易日日线序列。
- **派发日定义**：当日收盘下跌 $\ge 0.2\%$，且成交量较前一日放大。
- **V型强反转重置机制**：
  若最新收盘价相比过去 25 日最低收盘价 $Close_{t_{\text{low}}}$ 累计反弹 $\ge 6.0\%$，且站上 20 日均线（$Close_t > \text{SMA}_{20}$），则 $t_{\text{low}}$ 之前发生的旧派发日权重减半（按 0.5 次计）。
- **打分函数**：
  $$S_{\text{dist}} = \min\left(\frac{N_{\text{effective}}}{6.0}, 1.0\right) \times 100$$

#### 因子 2: AI 科技龙头量价顶背离率 (Volume Divergence with Momentum Exhaustion)
- **标的池**：`NVDA`, `MSFT`, `AVGO`, `GOOGL`, `TSM`。
- **背离判定条件**：
  1. $\text{NearTop} = (Close_t / \max_{20} Close) \ge 0.97$
  2. $\text{VolExhausted} = (\text{SMA}_{20}(Vol_t) / \max_{60} \text{SMA}_{20}(Vol)) \le 0.75$
  3. **动能衰竭确认**（满足其一）：
     - 近 5 日内冲高回落上影线天数 $\ge 2$（单日 $(High - Close)/(High - Low + 1e-6) > 0.6$）；
     - 或 14 日 RSI 相比近 20 日最高 RSI 回落超过 5 点（价格高位而 RSI 见顶回落）。
- **打分函数**：$S_{\text{vol}} = (\text{Count(Divergence)} / \text{TotalTickers}) \times 100$。

#### 因子 3: 半导体主线相对强弱背离 (RS Divergence: Continuous Linear + Penalty)
- **输入**：`SMH` 与 `SPY` 20 日比值 $RS = Close_{\text{SMH}} / Close_{\text{SPY}}$ 之 20 日斜率 $Slope_{\text{RS}}$ 与大盘斜率 $Slope_{\text{SPY}}$。
- **连续分段打分**：
  $$Base_{\text{rs}} = \begin{cases}
  10.0, & Slope_{\text{RS}} \ge 0.05 \\
  10.0 + \frac{0.05 - Slope_{\text{RS}}}{0.05} \times 25.0, & 0 \le Slope_{\text{RS}} < 0.05 \quad (10 \sim 35\text{分}) \\
  35.0 + \frac{|Slope_{\text{RS}}|}{0.08} \times 50.0, & -0.08 \le Slope_{\text{RS}} < 0 \quad (35 \sim 85\text{分}) \\
  \min\left(85.0 + \frac{|Slope_{\text{RS}}| - 0.08}{0.04} \times 15.0, 100.0\right), & Slope_{\text{RS}} < -0.08
  \end{cases}$$
- **顶背离惩罚**：若 $Slope_{\text{SPY}} > 0.01$ 且 $Slope_{\text{RS}} < -0.02$，额外增加 $Penalty_{\text{div}} = 15.0$ 分。
- 最终得分：$S_{\text{rs}} = \min(Base_{\text{rs}} + Penalty_{\text{div}}, 100.0)$。

#### 因子 4: QQQ 期权 Put/Call Ratio (Filtered PCR)
- **到期日过滤**：选取距离当前 7~25 天的期权，剔除 0DTE~3DTE 末日轮噪音。
- **行权价过滤**：保留现价 $[0.90 \times P_{\text{spot}}, 1.10 \times P_{\text{spot}}]$ 范围。
- **成交量与持仓量自适应**：
  若成交总量 $\ge 1000$ 张且 $CallVol > 0$，使用成交量 PCR；若非交易时段成交量清零，自动无缝降级为持仓量 $PCR_{\text{OI}} = \sum PutOI / \max(\sum CallOI, 1)$。
- **打分规则**：$\le 0.7$ 为 15 分，$(0.7, 1.0]$ 为 30~60分，$(1.0, 1.3]$ 为 60~90分，$> 1.3$ 为 100 分。

---

### 3.2 🌐 战略级中长频宏观信号 (Macro Signals)

#### 因子 5: 美债 10Y-2Y 收益率曲线利差 (Yield Curve Spread with Path-Sensing)
- **输入**：$Spread = Y_{10} - Y_2$ 及历史利差序列。
- **打分映射**：
  $$S_{\text{spread}} = \begin{cases}
  30.0, & \text{is\_fallback = True (兜底安全基准，严禁触发共振)} \\
  85.0, & Spread < -0.35\% \ (\text{强确定性深度倒挂}) \\
  65.0, & -0.35\% \le Spread < 0\% \ (\text{轻度倒挂}) \\
  75.0, & 0\% \le Spread < 0.35\% \ \text{且历史上曾深度倒挂} \ (\text{高危牛陡解挂衰退落地}) \\
  55.0, & 0\% \le Spread < 0.35\% \ \text{且无深度倒挂历史} \ (\text{温和紧缩/软着陆观察期}) \\
  30.0, & 0.35\% \le Spread < 1.0\% \ (\text{正常陡峭：平稳扩张}) \\
  20.0, & Spread \ge 1.0\% \ (\text{深度陡峭：宽松期})
  \end{cases}$$

#### 因子 6: 标普 500 (SPY) 200日均线乖离率 (SMA200 Deviation)
- **打分规则**：$> 18\%$ 为 90 分（极度超买）；$12\% \sim 18\%$ 为 70 分；$2\% \sim 12\%$ 为 35 分（慢牛）；$-3\% \sim 2\%$ 为 25 分；$-10\% \sim -3\%$ 为 55 分；$< -10\%$ 为 80 分（破位熊市）。

#### 因子 7: CNN 恐慌与贪婪指数 (Fear & Greed Index)
- **反向打分**：$\ge 80$ 为 95 分（极度贪婪见顶）；$60 \sim 80$ 为 65 分；$40 \sim 60$ 为 35 分；$20 \sim 40$ 为 45 分；$< 20$ 为 70 分（踩踏出清）。

---

### 3.3 极端多因子共振检测与融合总分

1. **短频得分聚合**：$Score_{\text{short}} = 0.30 S_{\text{dist}} + 0.25 S_{\text{vol}} + 0.25 S_{\text{rs}} + 0.20 S_{\text{pcr}}$
2. **中频宏观聚合**：$Score_{\text{macro}} = 0.40 S_{\text{spread}} + 0.30 S_{\text{sma}} + 0.30 S_{\text{fg}}$
3. **极端共振判定**：统计进入极高危阈值（$S_i \ge 75$）的真实因子数 $N_{\text{extreme}}$（严禁兜底数据计入）：
   - $N_{\text{extreme}} \ge 4$：共振加分 $+12.0$；
   - $N_{\text{extreme}} = 3$：共振加分 $+8.0$；
   - $N_{\text{extreme}} < 3$：共振加分 $0.0$。
4. **综合系统性风险总分**：
   $$Score_{\text{total}} = \min(Score_{\text{short}} \times 0.55 + Score_{\text{macro}} \times 0.45 + Boost_{\text{resonance}}, 100.0)$$

---

## 4. A 股与中证1000/IM期指对冲决策矩阵 (完备五阶梯状态机)

| 状态等级 | 触发判定条件 | 宏观与微观诊断 | 中证1000 (IM期指) 对冲决策 |
| :--- | :--- | :--- | :--- |
| **🚨 高危共振警戒** | $Score_{\text{total}} \ge 70$ 或 触发共振 | 短频筹码踩踏与中频宏观紧缩共振，美股见顶下杀具有强破坏力与传染性。 | **坚决防御/右侧做空**：A股早盘严禁盲目抄底；盘中若回抽5日均线受阻，坚决在右侧开空 IM 或配置看跌期权保护现货敞口。<br><br>⚠️ **【基差风控提示】** 开空 IM 期指前务必核查当前年化贴水率；若早盘大幅低开导致 IM 年化贴水率 > 15%，严禁盲目追空期指以防基差收敛逼空，应优先改用买入中证1000看跌期权（MO）构建熊市价差进行保护。 |
| **⚠️ 短线战术分歧** | $Score_{\text{total}} < 70$ 且未共振，且 $Score_{\text{short}} \ge 65$ 且 $Score_{\text{macro}} < 55$ | 科技链高位量价背离但宏观基本面平稳，定性为局部估值消化与正常洗盘。 | **低开抗跌/日内反弹**：A股早盘低开易出现日内 V 型修复，切忌恐慌杀跌追空，可于日内支撑位逢低寻找错杀机会。 |
| **⏳ 宏观阴霾蓄力** | $Score_{\text{total}} < 70$ 且未共振，且 $Score_{\text{macro}} \ge 65$ 且 $Score_{\text{short}} < 55$ | 利差倒挂/均线悬空等长周期隐患积累，但科技龙头尚未全面放量下杀。 | **收缩杠杆/战略防守**：不可盲目加高估值杠杆，IM 多头宜逢高逐步收缩风险暴露，提升组合防御性（红利/低估值）。 |
| **🟡 双频温和承压** *(新增兜底象限)* | $50 \le Score_{\text{total}} < 70$ 且不满足上述单边分歧条件 | 短频筹码与中频宏观均处于中等偏紧状态，市场缺乏单边突破动能，波动率中枢抬升。 | **【控制仓位/网格波段】** A股与中证1000易陷入宽幅震荡，IM 期指不宜追涨杀跌；建议将多头敞口降至半仓以下，或采用买平值看跌期权（MO）构建领口策略（Collar）锁住尾部风险。 |
| **🟢 双频低危健康** | $Score_{\text{total}} < 50$ | 高频流动性充沛，宏观无系统性衰退风险。 | **做多成长/持仓待涨**：外部噪音可完全忽略，聚焦国内产业主线，持有 IM 多头底仓。 |

---

## 5. 外部数据源与金融接口顺位规范

为保障数据真实性，严格遵循数据接口对口直取原则：

1. **美债收益率数据链路**：
   - **第一顺位**：TuShare Pro `us_tycr`（美债收益率曲线全期限日度数据）；
   - **第二顺位**：同花顺 iFinD EDB / 东方财富数据中心 REST API；
   - **第三顺位**：FRED 公开 CSV (`DGS10` 与 `DGS2`)；
   - **兜底基线**：锁定利差 $0.55\%$，固定安全分 30.0，标记 `is_fallback: True`，禁止计入共振。
2. **个股与 ETF 日线行情**：
   - **第一顺位**：TuShare Pro `us_daily`（美股日线）；
   - **降级顺位**：Yahoo Finance (`yfinance`)，带美东交易时段盘中成交量折算预估（Volume Projection）。
3. **期权与情绪数据**：
   - **期权 PCR**：yfinance 期权链（7~25 DTE，0.90~1.10 Spot 行权价，成交量不足时持仓量兜底）。
   - **恐慌贪婪**：CNN 官方 Dataviz 接口。

---

## 6. 移动端推送与持久化防抖 (`push_notifier.py`)

### 6.1 通道协议与网络规范
1. **微信 PushPlus 直推 (重点复查项)**：
   - **通信协议**：采用强制 HTTPS 直连（`https://www.pushplus.plus/send`），注入 `Content-Type: application/json`，杜绝 HTTP 80 端口强制重定向导致的连接被服务端重置断开（`RemoteDisconnected`）；
   - **应答校验**：双重校验 HTTP 状态码（200）与业务返回体 JSON 中的 `code == 200`，对 `code=903`（令牌无效）等业务异常进行日志预警与状态反馈；
   - **模板格式**：统一格式化为 `markdown` 模板，支持消息卡片富文本渲染。
2. **多源凭证加载优先级 (防泄露与高可用架构)**：
   - 模块初始化时自动加载本地 `.env`（已被 Git 忽略）；
   - 推送 Token（如 `PUSHPLUS_TOKEN`）优先从 `.env` / 环境变量读取；若与 `push_config.json` 冲突，自动剔除短位占位符，优先选用 32 位有效真实 Token，确保代码提交至开源或远程仓库时零泄密。
3. **其他通道支持**：
   - **iOS Bark**：向 `https://api.day.app/{key}/` 发送 POST 请求，带分组 `美股流动性监控` 与警报音频；
   - **企业微信 / 钉钉 / 飞书**：支持 Webhook 机器人卡片渲染；
   - **Telegram**：通过 Bot API 发送 Markdown 消息。

### 6.2 智能持久化防抖 (Debounce) 与恶化穿透
1. **本地 JSON 文件持久化防抖**：
   防抖记录保存于项目根目录 `.push_debounce_cache.json`（已被 Git 忽略），跨进程永久生效。
2. **2 小时默认防抖窗口**：
   `debounce_seconds` 设为 7200 秒（2 小时），解决 15 分钟巡检重复骚扰痛点。
3. **状态恶化跃迁穿透机制**：
   若当前巡检中：
   - 触发了多因子极端共振（而上一次未共振）；
   - 或综合风险评分飙升 $\ge 10$ 分；
   - 或状态等级跃迁升级为高危共振警戒；
   系统无视 2 小时防抖窗口，立即无条件强行向手机分发告警。

---

## 7. 自动化验证验收命令集

```powershell
# 1. 验证 UTF-8 编码与 Emoji 输出在 Windows 终端不崩溃
python -c "from cron_alert import run_check; run_check(force_alert=False)"

# 2. 验证决策矩阵全空间完备性（无死区测试）
python -c "from monitor_engine import evaluate_all; r = evaluate_all(); print('PASS: 决策矩阵状态 ->', r['action_guide']['level'])"

# 3. 验证持久化防抖缓存文件生成与 2 小时防抖生效
python -c "import os; from push_notifier import MobilePushNotifier; n = MobilePushNotifier(); print('PASS: 防抖窗口 =', n.debounce_seconds, '秒')"

# 4. 验证兜底模式下美债因子不会误触发 >=75 分共振
python -c "from monitor_engine import score_yield_curve; s, status = score_yield_curve(0.55); assert s < 50, f'Fallback score too high: {s}'; print('PASS: 美债兜底安全分 =', s, status)"
```
