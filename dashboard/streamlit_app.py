import os

import httpx
import pandas as pd
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
HEADERS = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}
NON_REPLAYABLE = {"invalid_signature", "invalid_payload"}

st.set_page_config(page_title="Event Orchestrator", layout="wide")
st.title("Event Orchestrator")
st.caption("Live view of the event stream, n8n, retries and the dead letter queue")


def api_get(path: str, **params):
    resp = httpx.get(f"{API}{path}", headers=HEADERS, params=params, timeout=8)
    resp.raise_for_status()
    return resp.json()


def api_post(path: str):
    resp = httpx.post(f"{API}{path}", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


@st.fragment(run_every=3)
def live():
    try:
        stats = api_get("/admin/stats")
        events = api_get("/admin/events", limit=50)
        dead = api_get("/admin/dlq")
        n8n = api_get("/admin/n8n")
    except Exception as exc:
        st.error(f"API unreachable: {exc}")
        return

    m = stats["metrics"]
    cols = st.columns(6)
    cols[0].metric("Received", m.get("received", 0))
    cols[1].metric("Processed", m.get("processed", 0))
    cols[2].metric("Retries", m.get("retries", 0))
    dupes = int(m.get("duplicates", 0)) + int(m.get("edge_duplicates", 0))
    cols[3].metric("Duplicates blocked", dupes)
    cols[4].metric("Pending", stats["pending"])
    cols[5].metric("Dead-lettered", stats["dlq_length"])

    st.subheader("n8n")
    left, right = st.columns([1, 4])
    left.metric("Status", "Healthy" if n8n["healthy"] else "Down")
    if n8n["executions"]:
        right.dataframe(pd.DataFrame(n8n["executions"]), hide_index=True)
    elif n8n.get("error"):
        right.warning(n8n["error"])
    elif not n8n["api_configured"]:
        right.caption("Set N8N_API_KEY in .env to show n8n execution history here.")
    else:
        right.info("No n8n executions yet.")

    st.subheader("Recent events")
    if events:
        st.dataframe(pd.DataFrame(events), hide_index=True)
    else:
        st.info("No events processed yet.")

    st.subheader("Dead letter queue")
    if not dead:
        st.success("Dead letter queue is empty.")
        return
    if st.button("Replay all"):
        api_post("/admin/dlq/replay-all")
        st.rerun(scope="fragment")
    for row in dead:
        title = f"{row['event_id']} ({row['type']}) - {row['reason']}"
        with st.expander(title):
            st.write(f"Attempts: {row['attempts']}")
            st.code(row["error"])
            if row["reason"] in NON_REPLAYABLE:
                st.caption("Rejected at the door, so it cannot be replayed.")
                st.code(row["payload"])
            else:
                st.json(row["payload"])
                if st.button("Replay", key=row["entry_id"]):
                    api_post(f"/admin/dlq/{row['entry_id']}/replay")
                    st.rerun(scope="fragment")


live()