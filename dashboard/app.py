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
DISPLAY_HOURS = list(range(5, 23))
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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
    overflow: hidden; border: 1px solid #e5e7eb; position: relative;
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
.pred-label { font-size: 14px; color: #6b7280; margin-top: 8px; letter-spacing: 0.01em; }
.pred-bar { margin-top: 16px; height: 6px; background: #e5e7eb; border-radius: 3px; overflow: hidden; }
.pred-bar-fill {
    height: 100%; border-radius: 3px;
    transition: width 0.6s cubic-bezier(.4,0,.2,1), background 0.4s ease;
}
.risk-pill {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600; letter-spacing: 0.02em; text-transform: uppercase;
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
def train_time_to_full_model(_events, capacity):
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
        day_df   = day_df.reset_index(drop=True)
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


# ======================================================================
# SIDEBAR
# ======================================================================
state = get_device_state_fresh()
with st.sidebar:
    st.markdown("### Controls")
    st.caption("Changes propagate to the device within ~1 second.")
    new_cap = st.slider("Bus capacity", 2, 50, int(state["capacity"]), key="capacity_slider")
    if new_cap != int(state["capacity"]):
        update_device_state({"capacity": new_cap})
    new_paused = st.toggle("Pause counting", value=bool(state["paused"]), key="pause_toggle",
                           help="When ON, passages are detected but not counted.")
    if new_paused != bool(state["paused"]):
        update_device_state({"paused": new_paused})
    if st.button("Reset count to zero", width="stretch", type="primary"):
        if update_device_state({"do_reset": True}):
            st.toast("Reset signal sent to device")
    st.divider()
    st.caption("**Device status**")
    st.caption(f"Capacity: {state['capacity']}")
    st.caption(f"Paused: {'yes' if state['paused'] else 'no'}")

st.title("Bus Counter")
st.caption("Real-time passenger monitoring with predictive overcrowding alerts")
st.subheader("Live status")


# ======================================================================
# LIVE METRICS — 1 second, no flash
# ======================================================================
@st.fragment(run_every=1)
def fast_fragment():
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


# ======================================================================
# PREDICTION — 1 second, no flash
# ======================================================================
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
    model, msg    = train_time_to_full_model(training_set, bus_cap)
    now           = pd.Timestamp.now()
    rate_now      = current_rate_per_minute(live)

    st.divider()
    st.header("Predictive overcrowding alert")
    st.caption(
        "A linear regression model takes the current count, recent boarding rate, "
        "hour of day, and weekday, and predicts how many minutes until the bus "
        "reaches capacity."
    )

    pred_col, info_col = st.columns([2, 1])
    with pred_col:
        if model is None:
            st.warning(f"Model not ready — {msg}.")
        elif current_count >= bus_cap:
            st.markdown("""
            <div class='pred-card'>
                <div class='pred-number' style='color:#dc2626;'>At capacity</div>
                <div class='pred-label'>
                    <span class='risk-pill critical'>critical</span>
                    Bus is full — dispatch a follow-up bus
                </div>
            </div>""", unsafe_allow_html=True)
        else:
            features  = np.array([[current_count, rate_now, now.hour, now.weekday()]])
            pred_mins = max(0.0, float(model.predict(features)[0]))

            # blend ML with mechanical calculation — pure ML was too optimistic at low rates
            if rate_now > 0.05:
                mechanical = (bus_cap - current_count) / rate_now
                pred_mins  = 0.4 * pred_mins + 0.6 * mechanical
                pred_mins  = max(0.0, pred_mins)

            if pred_mins < 3:
                color, level, bar_color = "#dc2626", "critical", "#dc2626"
            elif pred_mins < 8:
                color, level, bar_color = "#d97706", "elevated", "#f59e0b"
            else:
                color, level, bar_color = "#059669", "normal", "#10b981"

            urgency_pct = max(0, min(100, (1.0 - min(pred_mins, 30) / 30) * 100))
            st.markdown(f"""
            <div class='pred-card'>
                <div class='pred-number' style='color:{color};'>{pred_mins:.1f} min</div>
                <div class='pred-label'>
                    <span class='risk-pill {level}'>{level}</span>
                    predicted time until bus reaches capacity
                </div>
                <div class='pred-bar'>
                    <div class='pred-bar-fill'
                         style='width:{urgency_pct}%; background:{bar_color};'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    with info_col:
        st.markdown("**Input features**")
        st.write(f"Current count: **{current_count}**")
        st.write(f"Boarding rate: **{rate_now:.2f}/min** (last 5 min)")
        st.write(f"Hour of day: **{now.hour:02d}:00**")
        st.write(f"Weekday: **{WEEKDAY_NAMES[now.weekday()]}**")
        st.caption(f"Model: {msg}")


# ======================================================================
# COUNT OVER TIME
# ======================================================================
@st.fragment(run_every=30)
def count_chart_fragment():
    state   = get_device_state_cached()
    bus_cap = int(state["capacity"])
    df      = load_events()
    live    = df[df["device_id"] == "bus01"].copy() if not df.empty else pd.DataFrame()
    movement = live[live["event_type"].isin(["entry", "exit"])].copy() if not live.empty else pd.DataFrame()

    st.divider()
    st.subheader("Count over time")
    if movement.empty:
        st.info("No entry/exit events yet.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=movement["server_time"], y=movement["count_after"],
        mode="lines+markers",
        line=dict(color="#6366f1", width=2.5),
        marker=dict(size=7, color="#4f46e5"),
        name="People on bus",
    ))
    fig.add_hline(y=bus_cap, line_dash="dash", line_color="#ef4444", line_width=1.5,
                  annotation_text="Capacity", annotation_position="top right",
                  annotation_font_color="#ef4444")
    fig.update_layout(
        height=320, margin=dict(l=20, r=20, t=10, b=20),
        xaxis_title="", yaxis_title="People on bus",
        yaxis=dict(range=[0, max(bus_cap + 2, movement["count_after"].max() + 2)]),
        plot_bgcolor="white", xaxis=dict(gridcolor="#f3f4f6"), yaxis_gridcolor="#f3f4f6",
    )
    st.plotly_chart(fig, width="stretch", key="count_over_time_chart")


# ======================================================================
# HEATMAP
# ======================================================================
@st.fragment(run_every=60)
def heatmap_fragment():
    df = load_events()
    if df.empty:
        return

    live = df[df["device_id"] == "bus01"].copy()
    seed = df[df["device_id"] == "bus01-seed"].copy()

    seed_count  = int((seed["event_type"] == "entry").sum())
    real_count  = int((live["event_type"] == "entry").sum())
    total_model = seed_count + real_count

    st.divider()
    st.header("Learned demand pattern")
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Simulated boardings", f"{seed_count:,}")
    with c2: st.metric("Real boardings", f"{real_count:,}")
    with c3: st.metric("Used by model", f"{total_model:,}")
    st.caption("Model learns from both 14 days of simulated history and every real boarding.")

    hist = df[df["event_type"] == "entry"].copy()
    if total_model < 20:
        st.info("Run `python scripts/seed_history.py` once to seed historical data.")
        return

    hist["hour"]    = hist["server_time"].dt.hour
    hist["weekday"] = hist["server_time"].dt.weekday
    hist["date"]    = hist["server_time"].dt.date

    per_day = hist.groupby(["date", "weekday", "hour"]).size().reset_index(name="boardings")
    avg     = per_day.groupby(["weekday", "hour"])["boardings"].mean().reset_index()
    grid    = (avg.pivot(index="weekday", columns="hour", values="boardings")
                  .reindex(index=range(7), columns=DISPLAY_HOURS).fillna(0))

    heatmap = go.Figure(data=go.Heatmap(
        z=grid.round(1).values,
        x=[f"{h:02d}:00" for h in DISPLAY_HOURS],
        y=WEEKDAY_NAMES,
        colorscale=[
            [0.00, "#f8fafc"], [0.15, "#dbeafe"], [0.35, "#93c5fd"],
            [0.55, "#6366f1"], [0.80, "#4338ca"], [1.00, "#1e1b4b"],
        ],
        colorbar=dict(title=dict(text="Avg boardings", side="right"),
                      thickness=14, len=0.85, outlinewidth=0),
        hovertemplate="<b>%{y} %{x}</b><br>Avg %{z} boardings/hour<extra></extra>",
        xgap=2, ygap=2,
    ))
    heatmap.update_layout(
        height=360, margin=dict(l=20, r=20, t=10, b=20),
        xaxis_title="Hour of day", yaxis_title="",
        yaxis=dict(autorange="reversed"), plot_bgcolor="white",
    )
    st.plotly_chart(heatmap, width="stretch", key="heatmap_chart")

    best = avg.loc[avg["boardings"].idxmax()]
    st.success(
        f"**Busiest:** {WEEKDAY_NAMES[int(best['weekday'])]} "
        f"{int(best['hour']):02d}:00 — {best['boardings']:.1f} boardings/hour"
    )


# ======================================================================
# DAILY HISTORY
# ======================================================================
@st.fragment(run_every=60)
def daily_history_fragment():
    df = load_events()
    if df.empty:
        return

    live = df[df["device_id"] == "bus01"].copy()
    st.divider()
    st.header("Daily history")
    st.caption("Boarding activity per day — real events only.")

    if live.empty:
        st.info("No real events yet.")
        return

    live["date"] = live["server_time"].dt.date
    daily = (
        live.groupby(["date", "event_type"]).size().reset_index(name="count")
            .pivot(index="date", columns="event_type", values="count").fillna(0).reset_index()
    )
    for col in ("entry", "exit"):
        if col not in daily.columns:
            daily[col] = 0
    daily = daily.sort_values("date", ascending=False)

    st.dataframe(pd.DataFrame({
        "Date":       daily["date"].astype(str),
        "Entries":    daily["entry"].astype(int),
        "Exits":      daily["exit"].astype(int),
        "Net change": (daily["entry"] - daily["exit"]).astype(int),
    }), width="stretch", hide_index=True)

    last14 = daily.sort_values("date").tail(14)
    if not last14.empty:
        bar = go.Figure()
        bar.add_trace(go.Bar(x=last14["date"].astype(str), y=last14["entry"],
                             name="Entries", marker_color="#6366f1"))
        bar.add_trace(go.Bar(x=last14["date"].astype(str), y=last14["exit"],
                             name="Exits", marker_color="#94a3b8"))
        bar.update_layout(
            height=300, margin=dict(l=20, r=20, t=10, b=20),
            barmode="group", xaxis_title="", yaxis_title="Events",
            plot_bgcolor="white", yaxis_gridcolor="#f3f4f6",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(bar, width="stretch", key="daily_bar_chart")


# ======================================================================
# RECENT EVENTS
# ======================================================================
@st.fragment(run_every=5)
def recent_events_fragment():
    df = load_events()
    if df.empty:
        return
    live = df[df["device_id"] == "bus01"].copy()
    if live.empty:
        return
    st.divider()
    st.subheader("Recent live events")
    st.caption("Latest events from the ESP32. Seeded historical events excluded.")
    recent = live.tail(15).iloc[::-1][["server_time", "event_type", "count_after", "device_id"]]
    recent.columns = ["Time", "Event", "Count after", "Device"]
    st.dataframe(recent, width="stretch", hide_index=True)


# ======================================================================
# Render
# ======================================================================
fast_fragment()
prediction_fragment()
count_chart_fragment()
heatmap_fragment()
daily_history_fragment()
recent_events_fragment()
