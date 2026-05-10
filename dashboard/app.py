import streamlit as st
import sqlite3
import pandas as pd
import os
import time
import requests

st.set_page_config(page_title="Bus Counter", page_icon="🚌", layout="wide")

DB_PATH     = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "bus.db")
SERVER_BASE = "http://localhost:8000"

st.markdown("""
<style>
.capacity-wrap { margin: 18px 0 24px 0; }
.capacity-label {
    display: flex; justify-content: space-between;
    font-size: 14px; color: #6b7280; margin-bottom: 8px;
    font-feature-settings: "tnum";
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

    pct = min(100, (current_count / bus_cap) * 100) if bus_cap else 0
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
recent_events_fragment()
