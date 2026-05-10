import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os
import time
import requests
from sklearn.linear_model import LinearRegression

st.set_page_config(page_title="Bus Counter", page_icon="🚌", layout="wide")

DB_PATH       = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "bus.db")
SERVER_BASE   = "http://localhost:8000"
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

st.markdown("""
<style>
.capacity-wrap { margin: 18px 0 24px 0; }
.capacity-label {
    display: flex; justify-content: space-between;
    font-size: 14px; color: #6b7280; margin-bottom: 8px;
}
.capacity-label .left  { font-weight: 500; color: #374151; }
.capacity-label .right { font-weight: 600; color: #111827; }
.capacity-bar {
    height: 28px; background: #f3f4f6; border-radius: 14px;
    overflow: hidden; border: 1px solid #e5e7eb;
}
.capacity-fill {
    height: 100%; border-radius: 14px;
    transition: width 0.6s cubic-bezier(.4,0,.2,1), background 0.4s ease;
}
.capacity-fill.normal   { background: linear-gradient(90deg, #10b981 0%, #059669 100%); }
.capacity-fill.elevated { background: linear-gradient(90deg, #f59e0b 0%, #d97706 100%); }
.capacity-fill.critical { background: linear-gradient(90deg, #ef4444 0%, #dc2626 100%); }
.pred-card {
    background: #fafafa; border: 1px solid #e5e7eb;
    border-radius: 14px; padding: 24px 28px; margin-top: 8px;
}
.pred-number {
    font-size: 56px; font-weight: 700; line-height: 1;
    font-feature-settings: "tnum"; transition: color 0.4s ease;
}
.pred-label { font-size: 14px; color: #6b7280; margin-top: 8px; }
.risk-pill {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600; text-transform: uppercase;
}
.risk-pill.normal   { background: #d1fae5; color: #065f46; }
.risk-pill.elevated { background: #fef3c7; color: #92400e; }
.risk-pill.critical { background: #fee2e2; color: #991b1b; }
</style>
""", unsafe_allow_html=True)


def load_events():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM events ORDER BY id ASC", conn)
    conn.close()
    if df.empty:
        return df
    df["server_time"] = pd.to_datetime(df["server_time"])
    return df


def get_device_state_fresh():
    try:
        r = requests.get(f"{SERVER_BASE}/device/state", timeout=2)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {"capacity": 10, "paused": False, "reset_token": 0}


def get_device_state_cached():
    now = time.time()
    if "state_cached" not in st.session_state or \
       now - st.session_state.get("state_cached_at", 0) > 5:
        st.session_state.state_cached = get_device_state_fresh()
        st.session_state.state_cached_at = now
    return st.session_state.state_cached


def update_device_state(payload):
    try:
        r = requests.post(f"{SERVER_BASE}/device/state", json=payload, timeout=2)
        st.session_state.state_cached_at = 0
        return r.ok
    except Exception:
        return False


def current_rate_per_minute(live_df):
    if live_df.empty:
        return 0.0
    cutoff = pd.Timestamp.now() - pd.Timedelta(minutes=5)
    recent = live_df[(live_df["server_time"] >= cutoff) &
                     (live_df["event_type"] == "entry")]
    return len(recent) / 5.0


@st.cache_data(ttl=120, show_spinner=False)
def train_model(_events, capacity):
    if _events.empty:
        return None, "no data"

    e = _events.copy().sort_values("server_time").reset_index(drop=True)
    e["date"]    = e["server_time"].dt.date
    e["hour"]    = e["server_time"].dt.hour
    e["weekday"] = e["server_time"].dt.weekday

    times    = e["server_time"].values
    is_entry = (e["event_type"].values == "entry").astype(int)
    five_min = np.timedelta64(5, "m")
    rates    = []
    for i in range(len(e)):
        cutoff = times[i] - five_min
        j = i; n = 0
        while j >= 0 and times[j] >= cutoff:
            n += is_entry[j]; j -= 1
        rates.append(n / 5.0)
    e["rate_per_min"] = rates

    samples = []
    for _, day_df in e.groupby("date"):
        day_df  = day_df.reset_index(drop=True)
        cap_rows = day_df[day_df["count_after"] >= capacity]
        if cap_rows.empty:
            continue
        for i, row in day_df.iterrows():
            future = cap_rows[cap_rows["server_time"] > row["server_time"]]
            if future.empty or row["count_after"] >= capacity:
                continue
            mins = (future.iloc[0]["server_time"] - row["server_time"]).total_seconds() / 60.0
            if mins <= 0 or mins > 240:
                continue
            samples.append({
                "count": row["count_after"], "rate": row["rate_per_min"],
                "hour": row["hour"], "weekday": row["weekday"], "mins": mins,
            })

    if len(samples) < 30:
        return None, f"not enough data ({len(samples)} samples)"

    train = pd.DataFrame(samples)
    model = LinearRegression()
    model.fit(train[["count", "rate", "hour", "weekday"]].values, train["mins"].values)
    return model, f"trained on {len(samples)} samples"


# Sidebar
state = get_device_state_fresh()
with st.sidebar:
    st.markdown("### Controls")
    st.caption("Changes reach the device within ~1 second.")
    new_cap = st.slider("Bus capacity", 2, 50, int(state["capacity"]))
    if new_cap != int(state["capacity"]):
        update_device_state({"capacity": new_cap})
    new_paused = st.toggle("Pause counting", value=bool(state["paused"]))
    if new_paused != bool(state["paused"]):
        update_device_state({"paused": new_paused})
    if st.button("Reset count to zero", type="primary", width="stretch"):
        if update_device_state({"do_reset": True}):
            st.toast("Reset signal sent to device")
    st.divider()
    st.caption(f"Capacity: {state['capacity']}")
    st.caption(f"Paused: {'yes' if state['paused'] else 'no'}")

st.title("Bus Counter")
st.caption("Real-time passenger monitoring with predictive overcrowding alerts")
st.subheader("Live status")


@st.fragment(run_every=1)
def live_metrics():
    state   = get_device_state_cached()
    bus_cap = int(state["capacity"])
    df      = load_events()
    live    = df[df["device_id"] == "bus01"].copy() if not df.empty else pd.DataFrame()

    if live.empty:
        current_count, total_entries, total_exits, seconds_since = 0, 0, 0, 999
    else:
        current_count = int(live.iloc[-1]["count_after"])
        total_entries = int((live["event_type"] == "entry").sum())
        total_exits   = int((live["event_type"] == "exit").sum())
        seconds_since = (pd.Timestamp.now() - live.iloc[-1]["server_time"]).total_seconds()

    col1, col2, col3, col4 = st.columns(4)
    with col1: st.metric("People on bus", f"{current_count} / {bus_cap}")
    with col2: st.metric("Total entries", total_entries)
    with col3: st.metric("Total exits", total_exits)
    with col4:
        if seconds_since < 30:    status = "Online"
        elif seconds_since < 120: status = "Quiet"
        else:                     status = "No signal"
        st.metric("Device", status, f"{int(seconds_since)}s ago")

    pct       = min(100, (current_count / bus_cap) * 100) if bus_cap else 0
    bar_class = "normal" if pct < 60 else ("elevated" if pct < 90 else "critical")
    st.markdown(f"""
    <div class='capacity-wrap'>
      <div class='capacity-label'>
        <span class='left'>Current capacity</span>
        <span class='right'>{current_count} / {bus_cap} &nbsp;·&nbsp; {pct:.0f}%</span>
      </div>
      <div class='capacity-bar'>
        <div class='capacity-fill {bar_class}' style='width: {pct}%;'></div>
      </div>
    </div>
    """, unsafe_allow_html=True)


@st.fragment(run_every=1)
def prediction_fragment():
    state   = get_device_state_cached()
    bus_cap = int(state["capacity"])
    df      = load_events()
    if df.empty:
        return

    live          = df[df["device_id"] == "bus01"].copy()
    current_count = int(live.iloc[-1]["count_after"]) if not live.empty else 0
    training_set  = df[df["event_type"].isin(["entry", "exit"])]
    model, msg    = train_model(training_set, bus_cap)
    now           = pd.Timestamp.now()
    rate_now      = current_rate_per_minute(live)

    st.divider()
    st.header("Predictive overcrowding alert")
    st.caption(
        "Linear regression on current count, boarding rate, hour of day and weekday "
        "— predicts minutes until the bus reaches capacity."
    )

    pred_col, info_col = st.columns([2, 1])
    with pred_col:
        if model is None:
            st.warning(f"Model not ready — {msg}.")
        elif current_count >= bus_cap:
            st.markdown("""
            <div class='pred-card'>
                <div class='pred-number' style='color:#dc2626;'>At capacity</div>
                <div class='pred-label'><span class='risk-pill critical'>critical</span>
                Bus is full</div>
            </div>""", unsafe_allow_html=True)
        else:
            features  = np.array([[current_count, rate_now, now.hour, now.weekday()]])
            pred_mins = max(0.0, float(model.predict(features)[0]))

            color, level = ("#dc2626", "critical") if pred_mins < 3 else \
                           (("#d97706", "elevated") if pred_mins < 8 else ("#059669", "normal"))

            st.markdown(f"""
            <div class='pred-card'>
                <div class='pred-number' style='color:{color};'>{pred_mins:.1f} min</div>
                <div class='pred-label'>
                    <span class='risk-pill {level}'>{level}</span>
                    predicted time until bus reaches capacity
                </div>
            </div>""", unsafe_allow_html=True)

    with info_col:
        st.markdown("**Input features**")
        st.write(f"Count: **{current_count}**")
        st.write(f"Rate: **{rate_now:.2f}/min** (last 5 min)")
        st.write(f"Hour: **{now.hour:02d}:00**")
        st.write(f"Weekday: **{WEEKDAY_NAMES[now.weekday()]}**")
        st.caption(f"Model: {msg}")


@st.fragment(run_every=5)
def recent_events_fragment():
    df = load_events()
    if df.empty:
        return
    live = df[df["device_id"] == "bus01"].copy()
    if live.empty:
        return
    st.divider()
    st.subheader("Recent events")
    recent = live.tail(15).iloc[::-1][["server_time", "event_type", "count_after"]]
    recent.columns = ["Time", "Event", "Count after"]
    st.dataframe(recent, width="stretch", hide_index=True)


live_metrics()
prediction_fragment()
recent_events_fragment()
