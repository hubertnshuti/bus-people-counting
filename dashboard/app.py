import streamlit as st
import sqlite3
import pandas as pd
import os
import requests

st.set_page_config(page_title="Bus Counter", page_icon="🚌", layout="wide")

DB_PATH     = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "bus.db")
SERVER_BASE = "http://localhost:8000"


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


def get_device_state():
    try:
        r = requests.get(f"{SERVER_BASE}/device/state", timeout=2)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {"capacity": 10, "paused": False, "reset_token": 0}


def update_device_state(payload):
    try:
        r = requests.post(f"{SERVER_BASE}/device/state", json=payload, timeout=2)
        return r.ok
    except Exception:
        return False


state   = get_device_state()
bus_cap = int(state["capacity"])

# Sidebar controls
with st.sidebar:
    st.markdown("### Controls")
    new_cap = st.slider("Bus capacity", 2, 50, bus_cap)
    if new_cap != bus_cap:
        update_device_state({"capacity": new_cap})

    new_paused = st.toggle("Pause counting", value=bool(state["paused"]))
    if new_paused != bool(state["paused"]):
        update_device_state({"paused": new_paused})

    if st.button("Reset count to zero", type="primary"):
        if update_device_state({"do_reset": True}):
            st.toast("Reset sent")

# Main content
st.title("Bus Counter")
st.caption("Real-time passenger monitoring")

df = load_events()
live = df[df["device_id"] == "bus01"].copy() if not df.empty else pd.DataFrame()

if live.empty:
    current_count = 0
    total_entries = 0
    total_exits   = 0
else:
    current_count = int(live.iloc[-1]["count_after"])
    total_entries = int((live["event_type"] == "entry").sum())
    total_exits   = int((live["event_type"] == "exit").sum())

col1, col2, col3 = st.columns(3)
with col1: st.metric("People on bus", f"{current_count} / {bus_cap}")
with col2: st.metric("Total entries", total_entries)
with col3: st.metric("Total exits", total_exits)

st.subheader("Recent events")
if not live.empty:
    recent = live.tail(10).iloc[::-1][["server_time", "event_type", "count_after"]]
    st.dataframe(recent, hide_index=True)
else:
    st.info("No events yet.")
