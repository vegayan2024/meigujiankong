# 美股科技链与宏观流动性双频监控系统：项目与技术实现说明文档
> **文档版本**：v2.0 · 2026-11-02  
> **项目路径**：`C:\ai\antigravity\meigujiankong`  
> **远程仓库**：`https://github.com/vegayan2024/meigujiankong.git`  
> **核心触发词**：`美股监控`（与 A股监控 `jiankong` 项目严格隔离）

---

## 1. 项目定位与业务目标

### 1.1 业务背景
美股科技巨头（尤其是英伟达、微软、博通等 AI 头部链条）的流动性见顶与估值回撤，通常会在数个交易日内向下游传导，引发全球风险资产的流动性抽离。由于 A 股成长风格标的（特别是**中证1000指数及 IM 期指**）对海外科技风险偏好高度敏感，建立一套实时捕捉美股异动并直接输出 A 股对冲预案的量化监控系统具有极高的实战价值。

### 1.2 解决的核心痛点
1. **单一指标盲区**：纯看标普估值滞后于科技股踩踏；纯看个股K线容易忽略美债倒挂/解挂等大周期系统性危机。
2. **海内外数据网络延迟与单点故障**：境外金融数据源国内访问不稳定，缺乏高可用降级链路。
3. **缺少与国内衍生品的联动映射**：现有海外开源监控工具仅停留在美股自身观察，没有衔接国内中证1000/IM期指等实操对冲工具。
4. **移动端警报断层**：桌面端大屏无法做到 7x24 小时随身无感预警。

---

## 2. 系统整体架构与模块拓扑

系统采用**“双频双层监测 + 极端共振惩罚 + 多通道移动分发”**的工业级设计，整体数据流与模块依赖如下：

```mermaid
flowchart TD
    subgraph DataLayer [数据接入层 (多源与降级链路)]
        D1[雅虎财经 yfinance] -->|日线OHLCV / QQQ期权链| S_Data[短频微观行情]
        D2[东方财富 API (国内直连)] -->|美债 2Y/10Y 利差| M_Data[宏观长频数据]
        D3[CNN Dataviz API] -->|恐慌与贪婪指数| M_Data
        D4[yfinance ^TNX] -.->|美债降级备用| M_Data
    end

    subgraph EngineLayer [量化计算与风险评估引擎 (monitor_engine.py)]
        S_Data --> ShortModel[战术级高频模型 (0-100分)]
        M_Data --> MacroModel[战略级宏观模型 (0-100分)]
        
        ShortModel --> F1[欧奈尔派发日]
        ShortModel --> F2[龙头量价背离]
        ShortModel --> F3[半导体RS动能]
        ShortModel --> F4[期权PCR倾斜]

        MacroModel --> M1[美债10Y-2Y利差]
        MacroModel --> M2[标普200日均线乖离]
        MacroModel --> M3[宏观贪婪恐慌]

        ShortModel & MacroModel --> Fusion[双频加权融合与极端共振检测]
        Fusion --> TotalScore[综合系统性风险总分 (0-100)]
        Fusion --> Guide[A股/中证1000 IM 对冲决策矩阵]
    end

    subgraph PresentationLayer [展示与交互层]
        TotalScore & Guide --> StreamlitUI[Streamlit 左右双栏大屏 (app.py)]
        TotalScore & Guide --> CronChecker[后台免值守巡检 (cron_alert.py)]
    end

    subgraph PushLayer [手机端多通道推送系统 (push_notifier.py)]
        CronChecker & StreamlitUI --> Notifier[MobilePushNotifier (带10分钟防抖)]
        Notifier --> P1[🍎 iOS Bark 锁屏弹窗]
        Notifier --> P2[💬 微信 PushPlus / Server酱]
        Notifier --> P3[💼 企业微信 / 钉钉 / 飞书]
    end
```

### 2.1 源码文件与职责映射表

| 文件路径 | 核心职责 | 依赖与技术栈 |
| :--- | :--- | :--- |
| [`data_loader.py`](file:///C:/ai/antigravity/meigujiankong/data_loader.py) | 数据获取层：纳指/龙头ETF数据、期权PCR、东方财富美债直连、CNN情绪 | `yfinance`, `requests`, `pandas` |
| [`monitor_engine.py`](file:///C:/ai/antigravity/meigujiankong/monitor_engine.py) | 算法核心层：高频战术模型、宏观战略模型、共振算法、A股对冲指引 | `numpy`, `pandas` |
| [`push_notifier.py`](file:///C:/ai/antigravity/meigujiankong/push_notifier.py) | 推送适配层：iOS Bark、微信PushPlus/Server酱、企微/钉钉/飞书推送与防抖 | `requests`, `json` |
| [`app.py`](file:///C:/ai/antigravity/meigujiankong/app.py) | 前端大屏：左右双栏独立呈现、全维雷达图、动能折线图、推送即时配置与测试 | `streamlit`, `plotly` |
| [`cron_alert.py`](file:///C:/ai/antigravity/meigujiankong/cron_alert.py) | 后台免值守脚本：15分钟自动化巡检，风险 $\ge 70$ 或共振时手机推送 | 纯 Python 标准库调用 |
| [`push_config.json`](file:///C:/ai/antigravity/meigujiankong/push_config.json) | 本地密钥与通道配置模板（已被 Git 忽略敏感数据） | JSON |
| [`run.bat`](file:///C:/ai/antigravity/meigujiankong/run.bat) | Windows 快速启动脚本（指定 `python -m` 避免环境污染） | Windows 批处理 |

---

## 3. 双频指标量化建模与数学实现规范

系统分为**战术级高频短期信号（权重 55%）**与**战略级中长频宏观信号（权重 45%）**，各因子统一输出 0~100 分（分数越高代表风险越极端）。

### 3.1 ⚡ 战术级高频短期信号 (Short-Term Tactical Signals)

#### 因子 1: 欧奈尔机构派发日统计 (Distribution Days)
- **输入**：纳指 100 ETF (`QQQ`) 过去 25 个交易日日线序列。
- **派发日定义**：
  $$\text{DistDay}_t = \begin{cases} 1, & \text{pct\_change}(Close_t) \le -0.2\% \quad \text{and} \quad Volume_t > Volume_{t-1} \\ 0, & \text{otherwise} \end{cases}$$
- **打分函数**：
  $$S_{\text{dist}} = \min\left(\frac{\sum_{i=0}^{24} \text{DistDay}_{t-i}}{6.0}, 1.0\right) \times 100$$
  *(注：25日内累计达到 5~6 次放量下跌，判定为高位机构集中出逃)*。

#### 因子 2: AI 科技龙头量价顶背离率 (Volume Divergence)
- **标的池**：`NVDA`, `MSFT`, `AVGO`, `GOOGL`, `TSM`。
- **背离判定条件**（针对每只标的）：
  $$\text{NearTop} = \left(\frac{Close_t}{\max_{i \in [0, 19]} Close_{t-i}}\right) \ge 0.97$$
  $$\text{VolExhausted} = \left(\frac{\text{SMA}_{20}(Volume_t)}{\max_{i \in [0, 59]} \text{SMA}_{20}(Volume_{t-i})}\right) \le 0.75$$
- **打分函数**：
  $$S_{\text{vol}} = \frac{\text{Count}(\text{NearTop} \wedge \text{VolExhausted})}{\text{TotalTickers}} \times 100$$
  *(注：高位股价离峰值不足 3%，但成交均量萎缩超过 25%，反映无量虚拉出货)*。

#### 因子 3: 半导体主线相对强弱背离 (RS Divergence)
- **输入**：领涨半导体 ETF (`SMH`) 与标普 500 (`SPY`) 过去 20 个交易日比值 $RS_t = Close_{\text{SMH}, t} / Close_{\text{SPY}, t}$。
- **斜率计算**：$Slope_{\text{RS}} = \frac{RS_t - RS_{t-19}}{RS_{t-19}}$，$Slope_{\text{SPY}} = \frac{Close_{\text{SPY}, t} - Close_{\text{SPY}, t-19}}{Close_{\text{SPY}, t-19}}$。
- **打分规则**：
  $$S_{\text{rs}} = \begin{cases} 
  \min(|Slope_{\text{RS}}| \times 2000, 100), & Slope_{\text{SPY}} \ge 0 \ \text{and} \ Slope_{\text{RS}} < 0 \ (\text{大盘涨但芯片弱}) \\
  80, & Slope_{\text{RS}} < -0.05 \ (\text{芯片相对崩塌}) \\
  \max\left(0, \left(1 - \frac{Slope_{\text{RS}} + 0.05}{0.1}\right) \times 50\right), & \text{otherwise}
  \end{cases}$$

#### 因子 4: QQQ 近月期权 Put/Call Ratio (PCR 异动)
- **输入**：QQQ 近月到期平值及虚值期权成交总量之比 $PCR = \frac{\sum PutVol}{\sum CallVol}$。
- **分段打分函数**：
  $$S_{\text{pcr}} = \begin{cases}
  15.0, & PCR \le 0.7 \ (\text{极度多头看涨}) \\
  30.0 + (PCR - 0.7) \times 100, & 0.7 < PCR \le 1.0 \ (\text{中性均衡}) \\
  60.0 + (PCR - 1.0) \times 100, & 1.0 < PCR \le 1.3 \ (\text{看跌对冲增加}) \\
  100.0, & PCR > 1.3 \ (\text{极端恐慌对冲踩踏})
  \end{cases}$$

---

### 3.2 🌐 战略级中长频宏观信号 (Macro Signals)

#### 因子 5: 美债 10Y-2Y 收益率曲线利差 (Yield Curve Spread)
- **输入**：美债 10年期收益率 $Y_{10}$ 与 2年期收益率 $Y_2$ 之差 $Spread = Y_{10} - Y_2$。
- **金融周期逻辑与打分映射**：
  $$S_{\text{spread}} = \begin{cases}
  85.0, & Spread < -0.5\% \ (\text{深度倒挂：衰退蓄力期}) \\
  65.0, & -0.5\% \le Spread < 0\% \ (\text{轻度倒挂：紧缩期}) \\
  75.0, & 0\% \le Spread < 0.35\% \ (\text{倒挂后解挂走陡：高危衰退落地期}) \\
  30.0, & 0.35\% \le Spread < 1.0\% \ (\text{正常陡峭：平稳扩张}) \\
  20.0, & Spread \ge 1.0\% \ (\text{深度陡峭：宽松期})
  \end{cases}$$

#### 因子 6: 标普 500 (SPY) 200日均线乖离率 (SMA200 Deviation)
- **输入**：标普 500 复权收盘价与 200 日简单移动均线偏离度 $Dev\% = \frac{Close_t - \text{SMA}_{200}(Close_t)}{\text{SMA}_{200}(Close_t)} \times 100$。
- **打分规则**：
  $$S_{\text{sma}} = \begin{cases}
  90.0, & Dev\% > 18\% \ (\text{大周期严重超买悬空}) \\
  70.0, & 12\% < Dev\% \le 18\% \ (\text{过热状态}) \\
  35.0, & 2\% \le Dev\% \le 12\% \ (\text{慢牛良性运行}) \\
  25.0, & -3\% \le Dev\% < 2\% \ (\text{均线回踩支撑}) \\
  55.0, & -10\% \le Dev\% < -3\% \ (\text{均线下破弱势}) \\
  80.0, & Dev\% < -10\% \ (\text{深度破位熊市流动性缺失})
  \end{cases}$$

#### 因子 7: CNN 恐慌与贪婪指数 (Fear & Greed Index)
- **输入**：CNN 官方官方 0~100 情绪指数。
- **反向打分函数**：
  $$S_{\text{fg}} = \begin{cases}
  95.0, & FG \ge 80 \ (\text{极度贪婪：顶部狂热，反向极度危险}) \\
  65.0, & 60 \le FG < 80 \ (\text{贪婪偏热}) \\
  35.0, & 40 \le FG < 60 \ (\text{中性安全}) \\
  45.0, & 20 \le FG < 40 \ (\text{恐慌释放中}) \\
  70.0, & FG < 20 \ (\text{极度恐慌踩踏})
  \end{cases}$$

---

### 3.3 极端多因子共振检测 (Resonance Detection) 与综合评分

1. **短频得分聚合**：
   $$Score_{\text{short}} = 0.30 \cdot S_{\text{dist}} + 0.25 \cdot S_{\text{vol}} + 0.25 \cdot S_{\text{rs}} + 0.20 \cdot S_{\text{pcr}}$$
2. **中频宏观得分聚合**：
   $$Score_{\text{macro}} = 0.40 \cdot S_{\text{spread}} + 0.30 \cdot S_{\text{sma}} + 0.30 \cdot S_{\text{fg}}$$
3. **极端共振判定机制**：
   遍历上述 7 个因子的分值，统计进入极高危阈值（$S_i \ge 75$）的因子数量 $N_{\text{extreme}}$：
   $$Boost_{\text{resonance}} = \begin{cases} 
   12.0, & N_{\text{extreme}} \ge 4 \\ 
   8.0, & N_{\text{extreme}} = 3 \\ 
   0.0, & N_{\text{extreme}} < 3 
   \end{cases}$$
4. **综合系统性风险总分**：
   $$Score_{\text{total}} = \min\left(Score_{\text{short}} \times 0.55 + Score_{\text{macro}} \times 0.45 + Boost_{\text{resonance}}, 100.0\right)$$

---

## 4. A 股与中证1000/IM期指对冲决策矩阵

根据双频信号状态组合，系统向终端与手机推送以下四象限实操推演指令：

| 双频状态象限 | 触发判定条件 | 宏观与微观诊断 | 中证1000 (IM期指) 对冲决策 |
| :--- | :--- | :--- | :--- |
| **🚨 高危共振警戒** | $Score_{\text{total}} \ge 70$ 或 触发共振 | 短频筹码踩踏与中频宏观紧缩共振，美股见顶下杀具有强破坏力与传染性。 | **坚决防御/右侧做空**：A股早盘严禁盲目抄底；盘中若回抽5日均线受阻，坚决在右侧开空 IM 或配置看跌期权保护现货敞口。 |
| **⚠️ 短线战术分歧** | $Score_{\text{short}} \ge 65$ 且 $Score_{\text{macro}} < 50$ | 科技链高位量价背离但宏观基本面平稳，定性为局部估值消化与正常洗盘。 | **低开抗跌/日内反弹**：A股早盘低开易出现日内 V 型修复，切忌恐慌杀跌追空，可于日内支撑位逢低寻找错杀机会。 |
| **⏳ 宏观阴霾蓄力** | $Score_{\text{short}} < 50$ 且 $Score_{\text{macro}} \ge 65$ | 利差倒挂/均线悬空等长周期隐患积累，但科技龙头尚未全面放量下杀。 | **收缩杠杆/战略防守**：不可盲目加高估值杠杆，IM 多头宜逢高逐步收缩风险暴露，提升组合防御性（红利/低估值）。 |
| **🟢 双频低危健康** | 综合得分 $< 50$ | 高频流动性充沛，宏观无系统性衰退风险。 | **做多成长/持仓待涨**：外部噪音可完全忽略，聚焦国内产业主线，持有 IM 多头底仓。 |

---

## 5. 外部数据源容灾与网络降级架构

为保障国内网络环境下长期稳定运行，系统针对所有关键数据实现了多层兜底链：

1. **美债收益率数据链路**：
   - **第一顺位（首选）**：东方财富数据中心 REST API（国内直连、无代理依赖、0.8 秒响应）；
   - **第二顺位（降级）**：yfinance `^TNX` 端点；
   - **第三顺位（兜底）**：内置基线平坦利差估算（$Spread = 0.20\%$），保证计算流程不报错。
2. **市场情绪指数链路**：
   - **主入口**：CNN 官方 `production.dataviz.cnn.io` 接口，注入防 418 请求头；
   - **降级**：网络超时抛错时捕获异常，回退为 50.0 中性值。
3. **个股与期权行情链路**：
   - 提取日线拉取 1 年数据，满足 200 日均线计算样本量；
   - 雅虎期权链偶发 30 秒超时抛出异常时，安全回退为 1.0 中性 PCR，杜绝主进程崩溃。

---

## 6. 移动端推送系统技术规范 (`push_notifier.py`)

### 6.1 支持通道协议
1. **iOS Bark**：向 `https://api.day.app/{key}/` 发送 POST 请求，带分组 `美股流动性监控` 与警报音频 `alarm.caf`。
2. **微信直推 (PushPlus / Server酱)**：调用官方 REST 接口，推送标准 Markdown 渲染卡片。
3. **企业微信 / 钉钉 / 飞书**：Webhook 机器人通道，格式化为卡片或 Markdown 消息。

### 6.2 智能防抖设计 (Debounce)
- 每个警报以 `{level}:{title}` 作为哈希 Key 存入内存滑动窗口；
- 默认设置 600 秒（10分钟）防抖阈值；在窗口期内的相同告警级别静默忽略，防止高频巡检对交易员手机造成骚扰。

---

## 7. 质量审计与自动化验证清单

任何其他工具、LLM 或代码审核代理可通过以下步骤自检代码完备性：

### 7.1 本地测试命令集
```powershell
# 1. 验证全部 Python 依赖完备
python -c "import streamlit, streamlit_autorefresh, yfinance, pandas, numpy, plotly, requests; print('PASS: 依赖库正常')"

# 2. 验证东方财富美债国内直连接口
python -c "from data_loader import fetch_treasury_yields; t = fetch_treasury_yields(); assert t['us_10y'] > 0; print('PASS: 美债数据正常:', t)"

# 3. 验证 CNN 恐慌与贪婪接口
python -c "from data_loader import fetch_fear_and_greed; fg = fetch_fear_and_greed(); assert 0 <= fg['score'] <= 100; print('PASS: CNN情绪正常:', fg)"

# 4. 验证核心风险引擎计算逻辑与共振
python -c "from cron_alert import run_check; run_check(force_alert=True); print('PASS: 双频引擎与推送闭环正常')"

# 5. 验证 Streamlit 前端渲染与接口无阻塞
python -m streamlit run app.py --server.headless true --server.port 8502
```

### 7.2 核心断言点
- [x] `monitor_engine.py` 数据切片长度小于 2 时不抛出 `IndexError`。
- [x] 控制台输出与日志字符串移除了可能在 Windows GBK 环境下崩溃的多字节字符。
- [x] Streamlit 页面左右双栏分立，左侧为高频战术信号，右侧为中长期宏观信号。
- [x] 启动脚本统一使用 `python -m pip` 与 `python -m streamlit`，彻底规避失效的 Python 路径干扰。
