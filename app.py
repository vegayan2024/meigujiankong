import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from data_loader import (
    fetch_historical_data, 
    fetch_put_call_ratio, 
    fetch_treasury_yields, 
    fetch_fear_and_greed,
    AI_LEADERS, 
    BENCHMARKS
)
from monitor_engine import CrashRiskEngine

try:
    from streamlit_autorefresh import st_autorefresh
    # 默认每 10 分钟自动刷新
    st_autorefresh(interval=10 * 60 * 1000, key="data_autorefresh")
except Exception:
    pass

import json
from datetime import datetime
from push_notifier import MobilePushNotifier, CONFIG_FILE

st.set_page_config(page_title="美股科技链与宏观流动性风险监测终端", layout="wide")

st.title("🚨 美股科技链与宏观流动性双频风险监测终端")
st.caption("分层架构：⚡ 战术级高频微观信号 (派发日/龙头量价背离/半导体RS/期权PCR) ✖ 🌐 战略级中长频宏观信号 (美债10Y-2Y曲线/200日均线乖离/CNN贪婪恐慌)")

# =============================================================================
# 侧边栏：手机端信息推送设置与即时测试
# =============================================================================
st.sidebar.header("📱 手机端信息推送")
notifier = MobilePushNotifier()
cfg = notifier.config
channels = cfg.get("channels", {})

with st.sidebar.expander("⚙️ 推送通道配置 (点击配置)", expanded=False):
    st.caption("支持 微信服务号 / iOS Bark / 企微 / 钉钉 / 飞书 接收预警")
    bark_in = st.text_input("🍎 iOS Bark 链接", value=channels.get("bark_url", ""), placeholder="https://api.day.app/your_key/")
    pp_in = st.text_input("💬 微信 PushPlus Token", value=channels.get("pushplus_token", ""), type="password")
    sc_in = st.text_input("💬 微信 Server酱 SendKey", value=channels.get("serverchan_sendkey", ""), type="password")
    wecom_in = st.text_input("💼 企业微信 Webhook", value=channels.get("wecom_webhook", ""), type="password")
    feishu_in = st.text_input("💼 飞书 Webhook", value=channels.get("feishu_webhook", ""), type="password")
    ding_in = st.text_input("💼 钉钉 Webhook", value=channels.get("dingtalk_webhook", ""), type="password")

    if st.button("💾 保存配置到本地", use_container_width=True):
        cfg["channels"]["bark_url"] = bark_in.strip()
        cfg["channels"]["pushplus_token"] = pp_in.strip()
        cfg["channels"]["serverchan_sendkey"] = sc_in.strip()
        cfg["channels"]["wecom_webhook"] = wecom_in.strip()
        cfg["channels"]["feishu_webhook"] = feishu_in.strip()
        cfg["channels"]["dingtalk_webhook"] = ding_in.strip()
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success("配置已保存到 push_config.json！")


@st.cache_data(ttl=600)
def load_all_market_data():
    bundle = fetch_all_inputs()
    return bundle

with st.spinner("正在从多源金融数据链路（TuShare Pro、同花顺、雅虎财经、CNN 官方）拉取最新行情与宏观数据..."):
    market_bundle = load_all_market_data()
    data_dict = market_bundle["equity"]["data"]
    current_pcr = market_bundle["pcr"]
    treasury_data = market_bundle["treasury"]
    fear_greed_data = market_bundle["fear_greed"]
    data_status = market_bundle.get("status", [])

if data_status:
    with st.expander("📢 数据源链路状态与透明通报（点击展开）", expanded=any(x["level"] == "missing" for x in data_status)):
        for issue in data_status:
            if issue["level"] == "missing":
                st.error(f"❌ **【缺失】{issue['item']}**：{issue['detail']}")
            elif issue["level"] == "secondary":
                st.warning(f"⚠️ **【降级】{issue['item']}**：{issue['detail']}")
            else:
                st.info(f"ℹ️ **【提示】{issue['item']}**：{issue['detail']}")

engine = CrashRiskEngine(data_dict, current_pcr, treasury_data, fear_greed_data)
risk_result = engine.evaluate_overall_risk()

total_score = risk_result["total_score"]
short_score = risk_result["short_term_score"]
macro_score = risk_result["macro_score"]
resonance = risk_result["resonance"]
s_details = risk_result["short_term_details"]
m_details = risk_result["macro_details"]
guide = risk_result["action_guide"]

# 侧边栏即时测试按钮
if st.sidebar.button("🚀 测试发送手机推送", use_container_width=True):
    test_title = f"【美股监控测试】综合风险分: {total_score}分"
    test_content = (
        f"**测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**状态定性**：{guide['level']}\n\n"
        f"- ⚡ 高频短线: **{short_score}** 分\n"
        f"- 🌐 中长宏观: **{macro_score}** 分\n"
        f"- 🔥 多因子共振: {'已触发' if resonance['active'] else '未触发'}\n\n"
        f"**A股/IM期指对冲建议**：\n{guide['im_strategy']}"
    )
    res = notifier.send_alert(test_title, test_content, force=True)
    if res.get("ok"):
        st.sidebar.success(f"推送已发出！通道结果: {res.get('details')}")
    else:
        st.sidebar.warning(f"推送未发出: {res.get('msg')}")

# =============================================================================
# 顶部总览卡片栏
# =============================================================================
c1, c2, c3, c4 = st.columns(4)

# 1. 综合风险评分
if total_score >= 70:
    c1.metric("综合系统性风险总分", f"{total_score} / 100", "高危共振警戒", delta_color="inverse")
elif total_score >= 45:
    c1.metric("综合系统性风险总分", f"{total_score} / 100", "中度分歧累积", delta_color="off")
else:
    c1.metric("综合系统性风险总分", f"{total_score} / 100", "整体健康安全", delta_color="normal")

# 2. 高频短期战术评分
if short_score >= 70:
    c2.metric("⚡ 高频短期战术风险", f"{short_score} / 100", "科技链筹码松动", delta_color="inverse")
elif short_score >= 45:
    c2.metric("⚡ 高频短期战术风险", f"{short_score} / 100", "结构性分化", delta_color="off")
else:
    c2.metric("⚡ 高频短期战术风险", f"{short_score} / 100", "微观流动性充沛", delta_color="normal")

# 3. 中长期战略宏观评分
if macro_score >= 70:
    c3.metric("🌐 中长频宏观战略风险", f"{macro_score} / 100", "衰退/利差高压", delta_color="inverse")
elif macro_score >= 45:
    c3.metric("🌐 中长频宏观战略风险", f"{macro_score} / 100", "宏观温和承压", delta_color="off")
else:
    c3.metric("🌐 中长频宏观战略风险", f"{macro_score} / 100", "大周期扩张平稳", delta_color="normal")

# 4. 共振警报状态
if resonance["active"]:
    c4.metric("多因子共振状态", "🔥 触发极端共振", f"{resonance['extreme_count']}个因子极度高危 (+{int(resonance['boost'])}分)", delta_color="inverse")
else:
    c4.metric("多因子共振状态", "未触发共振", f"高危因子: {resonance['extreme_count']} 个", delta_color="normal")

st.markdown("---")

# =============================================================================
# 双频双栏分立展示专栏 (高频短期 VS 中长期中频)
# =============================================================================
col_short, col_macro = st.columns([1, 1], gap="large")

# -----------------------------------------------------------------------------
# 左栏：⚡ 战术级高频短期信号专栏
# -----------------------------------------------------------------------------
with col_short:
    st.subheader("⚡ 高频战术级短线信号 (Tactical Signals)")
    st.caption("核心观察：纳指与AI领涨股流动性拐点、筹码派发节奏与衍生品防守买盘")
    
    # 短频指标矩阵
    s1, s2 = st.columns(2)
    s1.metric(
        "QQQ 近25日机构派发天数", 
        f"{s_details['distribution_days']['count']} 天", 
        "≥5天高度警惕机构抛售" if s_details['distribution_days']['count'] >= 5 else "机构抛压平稳"
    )
    s2.metric(
        "QQQ 近月期权 Put/Call Ratio", 
        f"{s_details['options_pcr']['pcr_value']}", 
        "对冲买盘激增" if s_details['options_pcr']['pcr_value'] > 1.1 else "偏中性情绪"
    )
    
    s3, s4 = st.columns(2)
    flagged = s_details['volume_divergence']['flagged']
    s3.metric(
        "高位无量冲顶龙头数", 
        f"{len(flagged)} 只", 
        ", ".join(flagged) if flagged else "无显著量价顶背离"
    )
    rs_slope = s_details['rs_divergence']['rs_slope_pct']
    s4.metric(
        "半导体 (SMH / SPY) 20日动能斜率", 
        f"{rs_slope}%", 
        "科技相对走弱" if rs_slope < 0 else "科技领涨大盘"
    )

    st.markdown("##### 核心领涨板块相对强弱 (SMH / SPY)")
    if "SMH" in data_dict and "SPY" in data_dict:
        rs_series = data_dict["SMH"]["Close"] / data_dict["SPY"]["Close"]
        fig_rs = go.Figure()
        fig_rs.add_trace(go.Scatter(
            x=rs_series.index, 
            y=rs_series.values, 
            mode='lines', 
            name='SMH / SPY 动能比',
            line=dict(color='#0052FF', width=2)
        ))
        fig_rs.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            height=280,
            xaxis_title="交易日期",
            yaxis_title="相对强弱比 (上行代表芯片强于标普)"
        )
        st.plotly_chart(fig_rs, use_container_width=True)

# -----------------------------------------------------------------------------
# 右栏：🌐 战略级中长频宏观信号专栏
# -----------------------------------------------------------------------------
with col_macro:
    st.subheader("🌐 中长期战略级宏观信号 (Macro Signals)")
    st.caption("核心观察：美债收益率曲线形态、大周期牛熊均线超买偏离与宏观情绪钟摆")

    m1, m2, m3 = st.columns(3)
    # 美债利差
    spread_info = m_details['yield_spread']
    m1.metric(
        "美债 10Y-2Y 曲线利差",
        f"{spread_info['spread']}%",
        f"{spread_info['status']}"
    )
    # 200日均线乖离
    sma_info = m_details['sma200_deviation']
    m2.metric(
        "标普500 (SPY) 200日均线乖离",
        f"{sma_info['dev_pct']}%",
        f"{sma_info['status']}"
    )
    # 恐慌贪婪
    fg_info = m_details['fear_greed']
    m3.metric(
        "CNN 恐慌与贪婪指数",
        f"{int(fg_info['value'])}",
        f"{fg_info['status']}"
    )

    st.markdown("##### 全维风险因子雷达图 (Radar Profile)")
    categories = [
        "机构派发日", 
        "龙头量价背离", 
        "科技相对强弱", 
        "期权PCR对冲", 
        "美债曲线利差", 
        "标普200日乖离", 
        "宏观情绪贪婪"
    ]
    scores = [
        s_details['distribution_days']['score'],
        s_details['volume_divergence']['score'],
        s_details['rs_divergence']['score'],
        s_details['options_pcr']['score'],
        m_details['yield_spread']['score'],
        m_details['sma200_deviation']['score'],
        m_details['fear_greed']['score']
    ]

    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=scores + [scores[0]],
        theta=categories + [categories[0]],
        fill='toself',
        fillcolor='rgba(235, 87, 87, 0.25)',
        line=dict(color='#EB5757', width=2),
        name='当前全维风险暴露'
    ))
    fig_radar.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickfont=dict(size=10)),
            angularaxis=dict(tickfont=dict(size=11))
        ),
        showlegend=False,
        height=280,
        margin=dict(l=40, r=40, t=20, b=20)
    )
    st.plotly_chart(fig_radar, use_container_width=True)

st.markdown("---")

# =============================================================================
# 底部：🎯 针对 A 股与中证1000 (IM) 期指的双频联动对冲矩阵
# =============================================================================
st.subheader("🎯 A股与中证1000/IM期指双频对冲联动矩阵")

col_guide_left, col_guide_right = st.columns([1, 2])

with col_guide_left:
    st.markdown(f"#### 状态研判：{guide['level']}")
    st.markdown(f"**综合风险评分：** `{total_score} / 100`")
    st.markdown(f"- ⚡ 高频短线分: **{short_score}** 分")
    st.markdown(f"- 🌐 中长宏观分: **{macro_score}** 分")
    if resonance["active"]:
        st.error(f"⚠️ 触发黑天鹅共振加分 (+{int(resonance['boost'])}分)，极端因子数达到 {resonance['extreme_count']} 个！")

with col_guide_right:
    st.markdown(f"**核心诊断逻辑：**\n{guide['diagnosis']}")
    st.info(f"**中证1000 (IM) 与A股实操策略：**\n{guide['im_strategy']}")
