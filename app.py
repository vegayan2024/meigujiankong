import streamlit as st
import pandas as pd
import plotly.graph_objects as go
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

import json
from datetime import datetime
from push_notifier import MobilePushNotifier, CONFIG_FILE

st.set_page_config(page_title="美股与海外流动性定盘监测终端", layout="wide")

st.title("🏛️ 美股与海外流动性定盘监测终端")
st.caption("每日晨会研判：监控昨夜美股大科技筹码变动与宏观风险，判断对今早 A 股与中证1000 (IM) 的真实冲击")

# =============================================================================
# 侧边栏：手机端信息推送设置与即时测试
# =============================================================================
st.sidebar.header("📱 手机端信息推送")
notifier = MobilePushNotifier()
cfg = notifier.config
channels = cfg.get("channels", {})

if st.sidebar.button("🔄 强制刷新最新定盘数据", width="stretch"):
    st.cache_data.clear()
    st.rerun()

with st.sidebar.expander("⚙️ 推送通道配置 (点击配置)", expanded=False):
    st.caption("支持 微信服务号 / iOS Bark / 企微 / 钉钉 / 飞书 接收预警")
    bark_in = st.text_input("🍎 iOS Bark 链接", value=channels.get("bark_url", ""), placeholder="https://api.day.app/your_key/")
    pp_in = st.text_input("💬 微信 PushPlus Token", value=channels.get("pushplus_token", ""), type="password")
    sc_in = st.text_input("💬 微信 Server酱 SendKey", value=channels.get("serverchan_sendkey", ""), type="password")
    wecom_in = st.text_input("💼 企业微信 Webhook", value=channels.get("wecom_webhook", ""), type="password")
    feishu_in = st.text_input("💼 飞书 Webhook", value=channels.get("feishu_webhook", ""), type="password")
    ding_in = st.text_input("💼 钉钉 Webhook", value=channels.get("dingtalk_webhook", ""), type="password")

    if st.button("💾 保存配置到本地", width="stretch"):
        cfg["channels"]["bark_url"] = bark_in.strip()
        cfg["channels"]["pushplus_token"] = pp_in.strip()
        cfg["channels"]["serverchan_sendkey"] = sc_in.strip()
        cfg["channels"]["wecom_webhook"] = wecom_in.strip()
        cfg["channels"]["feishu_webhook"] = feishu_in.strip()
        cfg["channels"]["dingtalk_webhook"] = ding_in.strip()
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success("配置已保存到 push_config.json！")


@st.cache_data(ttl=3600)
def load_all_market_data():
    bundle = fetch_all_inputs()
    return bundle

with st.spinner("正在从金融数据链路拉取EOD收盘与宏观定盘数据..."):
    market_bundle = load_all_market_data()
    data_dict = market_bundle["equity"]["data"]
    current_pcr = market_bundle["pcr"]
    treasury_data = market_bundle["treasury"]
    fear_greed_data = market_bundle["fear_greed"]
    data_status = market_bundle.get("status", [])

coverage = market_bundle.get("coverage", {})
if data_status or (coverage and coverage.get("ok", 0) < coverage.get("total", 0)):
    with st.expander("📢 数据源链路状态与透明通报（点击展开）",
                     expanded=any(x["level"] == "missing" for x in data_status)):
        cov_ok, cov_total = coverage.get("ok", "?"), coverage.get("total", "?")
        if coverage and coverage.get("ok", 0) < coverage.get("total", 0):
            missing = ", ".join(coverage.get("missing", []))
            st.warning(
                f"⚠️ **评分数据覆盖：{cov_ok}/{cov_total} 个标的**（缺失: {missing}）。"
                "引擎已启用动态权重归一化与安全熔断保护，防止缺失数据虚构安全分。"
            )
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
is_deficient = risk_result.get("is_deficient", False)

# 侧边栏即时测试按钮
if st.sidebar.button("🚀 测试发送手机推送", width="stretch"):
    test_score_str = f"{total_score}分" if total_score is not None else "数据熔断"
    test_title = f"【美股定盘测试】综合风险: {test_score_str}"
    test_content = (
        f"**测试时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**状态定性**：{guide['level']}\n\n"
        f"- ⚡ 战术定盘: **{short_score if short_score is not None else '数据不足'}** 分\n"
        f"- 🌐 中长宏观: **{macro_score if macro_score is not None else '数据不足'}** 分\n"
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
if total_score is not None:
    if total_score >= 70:
        c1.metric("全球综合传导风险", f"{total_score} / 100", "高危共振警戒", delta_color="inverse",
                  help="【0-100分，越低越安全】综合衡量美股对A股的带崩杀伤力。<50分为安全，50-70分为震荡分化，≥70分为系统性高危。")
    elif total_score >= 45:
        c1.metric("全球综合传导风险", f"{total_score} / 100", "中度分歧累积", delta_color="off",
                  help="【0-100分，越低越安全】综合衡量美股对A股的带崩杀伤力。<50分为安全，50-70分为震荡分化，≥70分为系统性高危。")
    else:
        c1.metric("全球综合传导风险", f"{total_score} / 100", "整体健康安全", delta_color="normal",
                  help="【0-100分，越低越安全】综合衡量美股对A股的带崩杀伤力。<50分为安全，50-70分为震荡分化，≥70分为系统性高危。")
else:
    c1.metric("全球综合传导风险", "数据不足", "触发熔断保护", delta_color="off",
              help="外部金融数据源缺失，系统自动熔断中性保护。")

# 2. 战术短期定盘评分
if short_score is not None:
    if short_score >= 70:
        c2.metric("⚡ 科技股短期抛压", f"{short_score} / 100", "科技链筹码松动", delta_color="inverse",
                  help="【短线指标】反映纳指与AI巨头最近几天的机构抛盘力度、龙头虚涨情况与期权对冲防守情绪。")
    elif short_score >= 45:
        c2.metric("⚡ 科技股短期抛压", f"{short_score} / 100", "结构性分化", delta_color="off",
                  help="【短线指标】反映纳指与AI巨头最近几天的机构抛盘力度、龙头虚涨情况与期权对冲防守情绪。")
    else:
        c2.metric("⚡ 科技股短期抛压", f"{short_score} / 100", "微观流动性充沛", delta_color="normal",
                  help="【短线指标】反映纳指与AI巨头最近几天的机构抛盘力度、龙头虚涨情况与期权对冲防守情绪。")
else:
    c2.metric("⚡ 科技股短期抛压", "数据不足", "有效因子不足2个", delta_color="off")

# 3. 中长期战略宏观评分
if macro_score is not None:
    if macro_score >= 70:
        c3.metric("🌐 美国宏观经济大势", f"{macro_score} / 100", "衰退/利差高压", delta_color="inverse",
                  help="【大周期指标】反映美国国债利差、大盘偏离年线距离与全市场贪婪/恐慌度。代表宏观大气候。")
    elif macro_score >= 45:
        c3.metric("🌐 美国宏观经济大势", f"{macro_score} / 100", "宏观温和承压", delta_color="off",
                  help="【大周期指标】反映美国国债利差、大盘偏离年线距离与全市场贪婪/恐慌度。代表宏观大气候。")
    else:
        c3.metric("🌐 美国宏观经济大势", f"{macro_score} / 100", "大周期扩张平稳", delta_color="normal",
                  help="【大周期指标】反映美国国债利差、大盘偏离年线距离与全市场贪婪/恐慌度。代表宏观大气候。")
else:
    c3.metric("🌐 美国宏观经济大势", "数据不足", "有效因子不足2个", delta_color="off")

# 4. 共振警报状态
if resonance["active"]:
    c4.metric("🔥 极端风险共振预警", "触发极端共振", f"{resonance['extreme_count']}个因子极度高危 (+{int(resonance['boost'])}分)", delta_color="inverse",
              help="当短期和宏观有3个以上指标同时严重报警时，表明暴风雨来临，需坚决减仓防守！")
else:
    c4.metric("🔥 极端风险共振预警", "未触发共振", f"高危因子: {resonance['extreme_count']} 个", delta_color="normal",
              help="当前未发生多指标同时恶化，美股偶发震荡多属局部洗盘，不会引起系统性踩踏。")

st.markdown("---")

# =============================================================================
# 双频双栏分立展示专栏 (战术定盘 VS 中长期宏观)
# =============================================================================
col_short, col_macro = st.columns([1, 1], gap="large")

# -----------------------------------------------------------------------------
# 左栏：⚡ 战术级EOD定盘信号专栏
# -----------------------------------------------------------------------------
with col_short:
    st.subheader("⚡ 科技股短期筹码雷达（主力暗中出货了吗？）")
    st.caption("核心观察：华尔街机构是否在抛售纳指、AI龙头是否虚涨滞涨、衍生品避险情绪")
    
    # 短频指标矩阵
    s1, s2 = st.columns(2)
    dist_cnt = s_details['distribution_days']['count']
    s1.metric(
        "机构放量出货天数 (QQQ 近25日)", 
        f"{dist_cnt} 天" if dist_cnt is not None else "缺失", 
        ("≥5天高度警惕机构抛售" if dist_cnt >= 5 else "机构抛压平稳") if dist_cnt is not None else "无法计算",
        help="近25个交易日中，纳指放量下跌的天数。≥5天代表大主力在借反弹大举暗中出货，筹码开始松动。"
    )
    pcr_val = s_details['options_pcr']['pcr_value']
    s2.metric(
        "做空买保险比例 (期权 PCR)", 
        f"{pcr_val}" if pcr_val is not None else "缺失 (缺省中性)", 
        ("对冲买盘激增" if pcr_val > 1.1 else "偏中性情绪") if pcr_val is not None else "未接入期权链",
        help="纳指看跌/看涨期权交易比例。数值越高代表买看跌期权买保险防暴跌的人越多。>1.1为恐慌避险，<0.7为乐观。"
    )
    
    s3, s4 = st.columns(2)
    flagged = s_details['volume_divergence']['flagged']
    s3.metric(
        "高位虚涨/滞涨龙头数 (AI核心股)", 
        f"{len(flagged)} 只" if flagged is not None else "缺失", 
        (", ".join(flagged) if flagged else "无显著量价顶背离") if flagged is not None else "数据缺失",
        help="监控英伟达、微软、谷歌、台积电、博通。如果在高位缩量虚涨或见顶滞涨，说明领涨核心股动能衰竭。"
    )
    rs_slope = s_details['rs_divergence']['rs_slope_pct']
    s4.metric(
        "芯片领涨力度 (SMH/SPY 20日走势)", 
        f"{rs_slope}%" if rs_slope is not None else "缺失", 
        ("芯片相对走弱 (主线休整)" if rs_slope < 0 else "芯片领涨大盘 (主线强劲)") if rs_slope is not None else "数据缺失",
        help="半导体芯片ETF相对标普大盘的走势强弱。芯片是美股牛市发动机。为负代表芯片跑输大盘，科技线在调整。"
    )

    st.markdown("##### 核心领涨板块相对强弱 (芯片 ETF vs 标普大盘)")
    if "SMH" in data_dict and "SPY" in data_dict and not data_dict["SMH"].empty and not data_dict["SPY"].empty:
        rs_series = data_dict["SMH"]["Close"] / data_dict["SPY"]["Close"]
        fig_rs = go.Figure()
        fig_rs.add_trace(go.Scatter(
            x=rs_series.index, 
            y=rs_series.values, 
            mode='lines', 
            name='芯片/标普 强弱比',
            line=dict(color='#0052FF', width=2)
        ))
        fig_rs.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            height=280,
            xaxis_title="交易日期",
            yaxis_title="相对强弱 (曲线往上走代表芯片强于大盘)"
        )
        st.plotly_chart(fig_rs, width="stretch")

# -----------------------------------------------------------------------------
# 右栏：🌐 战略级中长频宏观信号专栏
# -----------------------------------------------------------------------------
with col_macro:
    st.subheader("🌐 宏观与大盘健康度（美国大盘底子硬不硬？）")
    st.caption("核心观察：美国国债利率信号、标普500距离年线位置、华尔街贪婪恐慌度")

    m1, m2, m3 = st.columns(3)
    # 美债利差
    spread_info = m_details['yield_spread']
    m1.metric(
        "美债收益率利差 (10Y-2Y)",
        f"{spread_info['spread']}%" if spread_info['spread'] is not None else "缺失",
        f"{spread_info['status']}",
        help="美国10年期与2年期国债利差，经济衰退核心晴雨表。如果长期倒挂（负数）后迅速转正走陡，往往预示衰退落地。"
    )
    # 200日均线乖离
    sma_info = m_details['sma200_deviation']
    m2.metric(
        "标普500 偏离年线距离",
        f"{sma_info['dev_pct']}%" if sma_info['dev_pct'] is not None else "缺失",
        f"{sma_info['status']}",
        help="标普500距离200日均线（牛熊年线）的偏离度。>15%极度超买吹泡沫；在0%附近说明踩在关键支撑位震荡；<-10%破位熊市。"
    )
    # 恐慌贪婪
    fg_info = m_details['fear_greed']
    m3.metric(
        "市场情绪体温计 (CNN恐慌贪婪)",
        f"{int(fg_info['value'])}" if fg_info['value'] is not None else "缺失",
        f"{fg_info['status']}",
        help="华尔街情绪指标（0-100）。<25为极度恐慌（大家割肉抛盘），>75为极度贪婪（散户狂热见顶信号）。"
    )

    st.markdown("##### 7大风险因子全景雷达")
    categories = [
        "机构派发日", 
        "龙头量价背离", 
        "科技相对强弱", 
        "期权PCR对冲", 
        "美债曲线利差", 
        "标普200日乖离", 
        "宏观情绪贪婪"
    ]
    raw_scores = [
        s_details['distribution_days']['score'],
        s_details['volume_divergence']['score'],
        s_details['rs_divergence']['score'],
        s_details['options_pcr']['score'],
        m_details['yield_spread']['score'],
        m_details['sma200_deviation']['score'],
        m_details['fear_greed']['score']
    ]
    scores = [float(s) if s is not None else 0.0 for s in raw_scores]

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
    st.plotly_chart(fig_radar, width="stretch")

st.markdown("---")

# =============================================================================
# 底部：🎯 针对 A 股与中证1000 (IM) 期指的双频联动对冲矩阵
# =============================================================================
st.subheader("🎯 今日 A股 与 中证1000 (IM) 实操对冲锦囊")

col_guide_left, col_guide_right = st.columns([1, 2])

with col_guide_left:
    st.markdown(f"#### 状态定性：{guide['level']}")
    score_display = f"`{total_score} / 100`" if total_score is not None else "`⚠️ 数据缺失熔断`"
    st.markdown(f"**全球综合风险：** {score_display}")
    short_display = f"**{short_score}** 分" if short_score is not None else "数据不足"
    st.markdown(f"- ⚡ 科技短期抛压: {short_display}")
    macro_display = f"**{macro_score}** 分" if macro_score is not None else "数据不足"
    st.markdown(f"- 🌐 美国宏观大势: {macro_display}")
    if resonance["active"]:
        st.error(f"⚠️ 触发黑天鹅共振加分 (+{int(resonance['boost'])}分)，极端因子数达到 {resonance['extreme_count']} 个！")

with col_guide_right:
    st.markdown(f"**核心诊断逻辑：**\n{guide['diagnosis']}")
    st.info(f"**中证1000 (IM) 与A股实操策略：**\n{guide['im_strategy']}")

