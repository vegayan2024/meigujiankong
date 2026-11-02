import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from data_loader import fetch_historical_data, fetch_put_call_ratio, AI_LEADERS, BENCHMARKS
from monitor_engine import CrashRiskEngine

try:
    from streamlit_autorefresh import st_autorefresh
    # 默认每 10 分钟静默自动刷新前端拉取数据
    st_autorefresh(interval=10 * 60 * 1000, key="data_autorefresh")
except Exception:
    pass

st.set_page_config(page_title="AI & 美股流动性拐点监测终端", layout="wide")

st.title("🚨 美股科技链与AI头部流动性拐点预警系统")
st.caption("监测维度: 欧奈尔机构派发日 (Distribution Days) | 龙头高位量价背离 | 科技相对强弱 (RS) | 衍生品 PCR 异动")

@st.cache_data(ttl=600)
def load_all_market_data():
    all_tickers = AI_LEADERS + list(BENCHMARKS.values())
    raw_data = fetch_historical_data(all_tickers, period="6mo")
    pcr = fetch_put_call_ratio("QQQ")
    return raw_data, pcr

with st.spinner("正在从雅虎财经拉取最新日线行情及期权链数据..."):
    data_dict, current_pcr = load_all_market_data()

engine = CrashRiskEngine(data_dict, current_pcr)
risk_result = engine.evaluate_overall_risk()

score = risk_result["total_score"]
details = risk_result["details"]

# 顶部指标栏
c1, c2, c3, c4 = st.columns(4)

if score >= 70:
    c1.metric("综合崩盘/见顶风险评分", f"{score} / 100", "高危警戒", delta_color="inverse")
elif score >= 45:
    c1.metric("综合崩盘/见顶风险评分", f"{score} / 100", "中度分歧", delta_color="off")
else:
    c1.metric("综合崩盘/见顶风险评分", f"{score} / 100", "健康/低危", delta_color="normal")

c2.metric("QQQ 近25日机构派发天数", f"{details['distribution_days']['count']} 天", 
          "≥5天提示顶部" if details['distribution_days']['count'] >= 5 else "正常")

flagged_stocks = details['volume_divergence']['flagged']
c3.metric("高位无量冲顶龙头数", f"{len(flagged_stocks)} 只", 
          ", ".join(flagged_stocks) if flagged_stocks else "无显著背离")

c4.metric("QQQ 近月期权 Put/Call Ratio", f"{details['options_pcr']['pcr_value']}", 
          "看跌对冲激增" if details['options_pcr']['pcr_value'] > 1.1 else "偏中性")

st.markdown("---")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("四维风险因子分解")
    categories = ["机构派发日", "龙头量能背离", "科技相对强弱(RS)", "期权PCR倾斜"]
    factor_scores = [
        details['distribution_days']['score'],
        details['volume_divergence']['score'],
        details['rs_divergence']['score'],
        details['options_pcr']['score']
    ]

    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=factor_scores + [factor_scores[0]],
        theta=categories + [categories[0]],
        fill='toself',
        name='当前风险暴露'
    ))
    fig_radar.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=False,
        margin=dict(l=40, r=40, t=30, b=30)
    )
    st.plotly_chart(fig_radar, use_container_width=True)

with col_right:
    st.subheader("核心领涨板块相对强弱 (SMH / SPY)")
    if "SMH" in data_dict and "SPY" in data_dict:
        rs_series = data_dict["SMH"]["Close"] / data_dict["SPY"]["Close"]
        fig_rs = go.Figure()
        fig_rs.add_trace(go.Scatter(x=rs_series.index, y=rs_series.values, mode='lines', name='SMH / SPY 相对强弱比'))
        fig_rs.update_layout(
            margin=dict(l=20, r=20, t=30, b=20),
            xaxis_title="日期",
            yaxis_title="比值 (上行代表半导体强于大盘)"
        )
        st.plotly_chart(fig_rs, use_container_width=True)

st.subheader("A股与IM期指联动预案提示")
st.markdown(f"""
- **当前核心诊断：** 综合得分为 **{score}** 分。
- **操作指引：**
  - **评分 < 45：** 科技链流动性健康，美股下挫多为良性休整，A股若低开易出现日内修复；
  - **45 <= 评分 < 70：** 出现结构性背离，不可盲目追高，关注期指均线乖离，适度收缩成长敞口；
  - **评分 >= 70：** 顶部高危共振。美股杀跌将向下游传导，A股（尤其是中证1000/IM）早盘情绪低开后，等待日线/60分钟回抽5日线阻力受阻，择机右侧开空或配置看跌期权保护。
""")
