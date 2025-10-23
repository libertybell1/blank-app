# app.py
import pandas as pd
import numpy as np
import streamlit as st

st.set_page_config(page_title="D2C Cannibalization Prototype", layout="wide")

# --------------------
# 0) 데이터 로드
# --------------------
st.sidebar.header("데이터 업로드")
file = st.sidebar.file_uploader("CSV 업로드 (UTF-8)", type=["csv"])

# 샘플 데이터 예제 버튼
if st.sidebar.button("샘플 데이터 불러오기"):
    sample = pd.DataFrame({
        "date":["2025-09-01"]*6,
        "product":["Hoodie A","Hoodie A","Hoodie A","Pants B","Pants B","Pants B"],
        "channel":["d2c","musinsa","coupang","d2c","musinsa","coupang"],
        "price":[59000,62000,61000,69000,72000,70000],
        "volume":[120,180,140,90,160,110],
        "fee_rate":[0.00,0.15,0.20,0.00,0.15,0.20],
        "unit_cost":[27000,27000,27000,33000,33000,33000]
    })
    st.session_state["df"] = sample

if file is not None:
    df = pd.read_csv(file)
    st.session_state["df"] = df

if "df" not in st.session_state:
    st.info("좌측에서 CSV를 업로드하거나 '샘플 데이터 불러오기'를 눌러주세요.")
    st.stop()

df = st.session_state["df"].copy()

# --------------------
# 1) 기본 계산
# --------------------
df["revenue"] = df["price"] * df["volume"]
df["fee_cost"] = df["revenue"] * df["fee_rate"]
df["product_margin"] = df["price"] - df["unit_cost"]     # 단위마진
df["contribution"] = df["volume"] * df["product_margin"] - df["fee_cost"]

# KPI
total_rev = df["revenue"].sum()
d2c_rev = df.loc[df["channel"].str.lower()=="d2c","revenue"].sum()
d2c_share = (d2c_rev/total_rev) if total_rev>0 else 0.0
total_contrib = df["contribution"].sum()

kpi1, kpi2, kpi3 = st.columns(3)
kpi1.metric("D2C Share", f"{d2c_share*100:.1f}%")
kpi2.metric("총 공헌이익", f"{int(total_contrib):,} 원")
kpi3.metric("총 매출", f"{int(total_rev):,} 원")

st.markdown("---")

# --------------------
# 2) 시나리오: 외부채널 감축 & 자사몰 대체율
# --------------------
st.sidebar.header("시나리오 설정")
channels = df["channel"].str.lower().unique().tolist()
non_d2c = [c for c in channels if c != "d2c"]

# 채널별 감축율 슬라이더
reduce_map = {}
for ch in non_d2c:
    reduce_map[ch] = st.sidebar.slider(f"{ch} 판매량 감축률(%)", 0, 100, 0, step=5) / 100.0

# 자사몰 대체율 (카니발라이제이션 보정)
shift_ratio = st.sidebar.slider("외부→자사몰 대체율(%)", 0, 100, 35, step=5) / 100.0

# 가격/수수료/원가 변동 옵션 (MVP에선 OFF, 필요시 ON)
st.sidebar.subheader("고급 옵션(선택)")
use_price_adjust = st.sidebar.checkbox("자사몰 전환 시 D2C 가격정책 적용", value=True)
use_fee_adjust = st.sidebar.checkbox("자사몰 수수료 0% 적용", value=True)

# --------------------
# 3) 시나리오 계산
# --------------------
sim = df.copy()

# 외부채널 감축
sim["reduce_rate"] = sim["channel"].str.lower().map(reduce_map).fillna(0.0)
sim["reduced_volume"] = sim["volume"] * sim["reduce_rate"]

# 자사몰로 전환되는 수요
# (같은 product 기준으로 묶어서 d2c에 가산)
d2c_gain = sim.groupby(["date","product"]).apply(
    lambda g: pd.Series({"d2c_gain": g.loc[g["channel"].str.lower()!="d2c","reduced_volume"].sum() * shift_ratio})
).reset_index()

# 기존 df에 merge
sim = sim.merge(d2c_gain, on=["date","product"], how="left")
sim["d2c_gain"] = sim["d2c_gain"].fillna(0.0)

# 신규 볼륨 계산
def adjust_row(row):
    ch = row["channel"].lower()
    vol = row["volume"]
    red = row["reduced_volume"]
    if ch != "d2c":
        return max(vol - red, 0)
    else:
        # 같은 date, product 내에서 d2c에만 gain 추가
        return vol + row["d2c_gain"]

sim["volume_new"] = sim.apply(adjust_row, axis=1)

# 가격/수수료 정책
if use_price_adjust:
    # D2C는 기존 price 유지 (정책 바꾸고 싶으면 여기서 조정 룰 추가)
    pass
if use_fee_adjust:
    sim.loc[sim["channel"].str.lower()=="d2c","fee_rate"] = 0.0

# 시나리오 재계산
sim["revenue_new"] = sim["price"] * sim["volume_new"]
sim["fee_cost_new"] = sim["revenue_new"] * sim["fee_rate"]
sim["contribution_new"] = sim["volume_new"] * (sim["price"] - sim["unit_cost"]) - sim["fee_cost_new"]

delta_contrib = sim["contribution_new"].sum() - df["contribution"].sum()
delta_rev = sim["revenue_new"].sum() - df["revenue"].sum()

c1, c2 = st.columns(2)
c1.metric("Δ 총 공헌이익", f"{int(delta_contrib):,} 원",
          delta=f"{int(delta_contrib):,} 원")
c2.metric("Δ 총 매출", f"{int(delta_rev):,} 원",
          delta=f"{int(delta_rev):,} 원")

st.markdown("### 채널별 공헌이익 변화")
agg_now = df.groupby("channel", as_index=False)["contribution"].sum().rename(columns={"contribution":"contrib_now"})
agg_new = sim.groupby("channel", as_index=False)["contribution_new"].sum().rename(columns={"contribution_new":"contrib_new"})
cmp = agg_now.merge(agg_new, on="channel", how="outer").fillna(0)
cmp["Δcontribution"] = cmp["contrib_new"] - cmp["contrib_now"]
st.dataframe(cmp.sort_values("Δcontribution", ascending=False), use_container_width=True)

st.markdown("### 상품×채널 매트릭스 (시나리오)")
pivot = sim.pivot_table(index="product", columns="channel", values="contribution_new", aggfunc="sum", fill_value=0)
st.dataframe(pivot.style.format("{:,.0f}"), use_container_width=True)

st.markdown("—")
st.caption("※ MVP 가정: 물류/쿠폰/광고 등 추가변수는 제외. 추후 Incrementality 보정, LTV, 가격탄력성, 채널별 다른 가격정책 반영 예정.")
