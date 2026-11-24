from pathlib import Path
import streamlit as st
from database import requests_df, log_df, STATUSES
import components as ui

ui.hero("Waste Tracking & Chain of Custody", "Monitor real-time dispatch progress, custody transfers, and verified recovery outcomes", "timeline")

r = requests_df(exclude_demo=True)

status_icon_map = {
    "Pending": "clock",
    "Approved": "check_circle",
    "Scheduled": "calendar",
    "Collected": "truck",
    "Processing": "cog",
    "Reused": "shirt",
    "Recycled": "recycle",
    "Upcycled (Craft Products)": "craft",
    "Disposed": "shield",
    "Rejected": "x_circle"
}

status_color_map = {
    "Pending": "#D97706",
    "Approved": "#2563EB",
    "Scheduled": "#0284C7",
    "Collected": "#7C3AED",
    "Processing": "#4F46E5",
    "Reused": "#059669",
    "Recycled": "#10B981",
    "Upcycled (Craft Products)": "#40916C",
    "Disposed": "#0284C7",
    "Rejected": "#DC2626"
}

st.subheader("Status Breakdown Across All Batches")
cards_html = []
for s in STATUSES:
    icon_name = status_icon_map.get(s, "info")
    col = status_color_map.get(s, "#1B4332")
    count = int((r.status == s).sum()) if not r.empty else 0
    weight = r[r.status == s].quantity_kg.sum() if not r.empty else 0.0
    i_html = ui.icon_svg(icon_name, 20, col)
    cards_html.append(f'''
    <div class="tl-step">
        <div style="width:36px;height:36px;border-radius:8px;background:{col}15;border:1px solid {col}30;display:flex;align-items:center;justify-content:center;margin:0 auto 10px;">
            {i_html}
        </div>
        <b style="color:{col}">{s}</b>
        <span class="desc"><b>{count}</b> requests<br>{weight:,.0f} kg</span>
    </div>
    ''')
ui.render_html(f'<div class="tl-flow">{"".join(cards_html)}</div>')

if r.empty:
    st.info("No active collection requests in the system.")
else:
    st.subheader("Track Specific Request")
    
    # Quick Search Bar
    search_q = st.text_input("Search by Collection Tracking ID (CR-...) or Batch ID (TW-...)", placeholder="e.g. CR-20261124-XXXX or TW-20261124-XXXX").strip()
    
    selected_idx = 0
    lab = {x.id: f"{x.coll_id} | {x.destination} ({x.quantity_kg:.1f} kg - {x.status})" for x in r.itertuples()}
    
    if search_q:
        matched = r[(r.coll_id.str.contains(search_q, case=False, na=False)) | (r.reg_id.str.contains(search_q, case=False, na=False))]
        if not matched.empty:
            rid = matched.id.iloc[0]
            st.success(f"Matched Tracking Record: **{matched.coll_id.iloc[0]}** (Source Batch: `{matched.reg_id.iloc[0]}`)")
        else:
            st.warning("No tracking record found matching that identifier. Showing dropdown list below.")
            rid = st.selectbox("Select Request Identifier", list(lab), format_func=lab.get, index=0)
    else:
        rid = st.selectbox("Select Request Identifier", list(lab), format_func=lab.get, index=0)
    
    req_row = r[r.id == rid].iloc[0]
    cur = req_row.status
    coll_id = req_row.coll_id
    
    # Automated Live Notification Banner
    logs = log_df(rid)
    if not logs.empty:
        latest = logs.iloc[-1]
        notes_str = f" — *Remarks: {latest['notes']}*" if latest.get('notes') and str(latest['notes']).strip() else ""
        st.info(f"🔔 **Automated Status Update Notification:** Request `{coll_id}` transitioned to **{latest.new_status}** by **{latest.changed_by}** on {latest.changed_at} UTC{notes_str}")
    
    # Request Info & Photograph Preview
    c_det, c_photo = st.columns([2, 1])
    with c_det:
        st.markdown(f"""
        - **Unique Collection Tracking ID:** `{coll_id}`
        - **Source Waste Batch ID:** `{req_row.reg_id}`
        - **Registrant / Contact Person:** `{req_row.generator}` ({req_row.institution})
        - **Phone / Email:** **{req_row.generator_contact}**
        - **Item Classification:** {req_row.item_type}
        - **Material Specification:** {req_row.material} ({req_row.cond})
        - **Waste Category:** {req_row.waste_category}
        - **Batch Quantity:** **{req_row.quantity_kg:.1f} kg**
        - **Origin Location:** {req_row.waste_location}
        - **Destination Partner:** {req_row.destination} ({req_row.ftype})
        - **Batch Description / Notes:** {req_row.batch_description if str(req_row.batch_description).strip() else "None provided"}
        - **Current Custody Status:** **{cur}**
        """)
        
    with c_photo:
        img_p = req_row.image_path
        if img_p and Path(str(img_p)).exists():
            st.image(str(img_p), caption=f"Batch Photograph ({req_row.reg_id})", width=240)
        else:
            st.caption("No photograph registered for this batch.")
            
    ftype = req_row.ftype if "ftype" in req_row else ""
    if ftype == "Authorized waste collection agency" or cur == "Disposed":
        final_step = "Disposed"
    elif cur == "Recycled":
        final_step = "Recycled"
    elif cur == "Upcycled (Craft Products)":
        final_step = "Upcycled (Craft Products)"
    else:
        final_step = "Reused"
    path = ["Pending", "Approved", "Scheduled", "Collected", "Processing", final_step]
    
    if cur == "Rejected":
        st.error("Status: Rejected. The batch quantity has been returned to the available unallocated inventory.")
    else:
        reached = path.index(cur) if cur in path else 0
        stepper_items = []
        for k, s in enumerate(path):
            is_active = k <= reached
            is_current = k == reached
            bg = "#1B4332" if is_active else "#F1F5F9"
            txt_color = "#FFFFFF" if is_active else "#64748B"
            border = "2px solid #52B788" if is_current else ("1px solid #1B4332" if is_active else "1px solid #E2E8F0")
            stepper_items.append(f'''
            <div class="tl-step" style="background:{bg};border:{border};color:{txt_color}">
                <div style="font-size:11px;font-weight:600;opacity:0.85;margin-bottom:2px">Step {k+1}</div>
                <b style="color:{txt_color};font-size:13px">{s}</b>
            </div>
            ''')
        ui.render_html(f'<div class="tl-flow">{"".join(stepper_items)}</div>')
    
    st.subheader("Custody & Verification Audit Log")
    if not logs.empty:
        display_log = logs.copy()
        display_log.columns = ["Previous Status", "New Status", "Operator / Partner", "Logistics & Custody Remarks", "Timestamp (UTC)"]
        display_log["Logistics & Custody Remarks"] = display_log["Logistics & Custody Remarks"].fillna("—")
        st.dataframe(display_log, hide_index=True, width="stretch")
    else:
        st.caption("No status changes recorded for this request yet.")
