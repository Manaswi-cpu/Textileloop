from pathlib import Path
import streamlit as st
from database import requests_df, set_status, rematch_after_rejection, query, STATUSES
import components as ui

ui.hero("Collection Requests & Partner Portal", "Comprehensive ledger of dispatch requests, logistics status, and authorized partner updates", "truck")

if st.session_state.get("flash_collection"):
    st.success(st.session_state.pop("flash_collection"))

r = requests_df(exclude_demo=True)

t1, t2 = st.tabs(["Dispatch Requests Ledger", "Collection Partner Update Portal"])

with t1:
    st.subheader("Filter Requests")
    c_f1, c_f2 = st.columns(2)
    pick = c_f1.multiselect("Filter by Processing Status", STATUSES, key="filter_status")
    dests = sorted(r.destination.unique().tolist()) if not r.empty else []
    pick_dest = c_f2.multiselect("Filter by Destination Facility", dests, key="filter_dest")
    
    filtered_r = r
    if pick:
        filtered_r = filtered_r[filtered_r.status.isin(pick)]
    if pick_dest:
        filtered_r = filtered_r[filtered_r.destination.isin(pick_dest)]

    if filtered_r.empty:
        st.info("No collection requests found. Submitting a waste registration creates one automatically when a partner matches.")
    else:
        display_cols = {
            "coll_id": "Collection Tracking ID",
            "reg_id": "Source Batch ID",
            "material": "Material",
            "waste_category": "Category",
            "destination": "Destination Facility",
            "quantity_kg": "Quantity (kg)",
            "request_date": "Request Date",
            "status": "Current Status"
        }
        r_display = filtered_r[list(display_cols.keys())].rename(columns=display_cols)
        st.dataframe(r_display, hide_index=True, width="stretch")

with t2:
    st.subheader("Authorized Partner Status & Logistics Updates")
    st.caption("Authorized recycling partners, NGOs, and bio-disposal operators can update the status and chain-of-custody logs for their assigned requests.")
    
    partners_df = query("SELECT id, name, ftype, location FROM recycler_directory ORDER BY name")
    if partners_df.empty:
        st.warning("No partner facilities registered in the system.")
    else:
        partner_map = {row.id: f"{row.name} ({row.ftype} — {row.location})" for row in partners_df.itertuples()}
        selected_partner_id = st.selectbox("Select Your Facility / Organization", list(partner_map), format_func=partner_map.get, key="partner_select")
        partner_name = partners_df[partners_df.id == selected_partner_id].name.iloc[0]
        
        if "recycler_id" in r.columns and not r.empty:
            partner_reqs = r[r.recycler_id == selected_partner_id]
        else:
            partner_reqs = r.iloc[0:0] if not r.empty else r
        if partner_reqs.empty:
            st.info(f"No collection requests currently assigned to **{partner_name}**.")
        else:
            st.markdown(f"#### Assigned Requests for **{partner_name}** ({len(partner_reqs)} Total)")
            req_map = {row.id: f"{row.coll_id} | {row.material} ({row.quantity_kg:.1f} kg) — Status: {row.status}" for row in partner_reqs.itertuples()}
            target_req_id = st.selectbox("Select Assigned Collection Request", list(req_map), format_func=req_map.get, key="partner_req_select")
            
            selected_row = partner_reqs[partner_reqs.id == target_req_id].iloc[0]
            
            # Display request details & batch photo
            col_info, col_img = st.columns([2, 1])
            with col_info:
                st.markdown(f"""
                - **Tracking ID:** `{selected_row.coll_id}`
                - **Source Waste ID:** `{selected_row.reg_id}`
                - **Contact Person:** `{selected_row.generator}` ({selected_row.institution})
                - **Phone / Email:** **{selected_row.generator_contact}**
                - **Item Classification:** {selected_row.item_type}
                - **Material & Condition:** {selected_row.material} ({selected_row.cond})
                - **Assigned Quantity:** **{selected_row.quantity_kg:.1f} kg**
                - **Origin Facility:** {selected_row.waste_location}
                - **Batch Notes / Description:** {selected_row.batch_description if str(selected_row.batch_description).strip() else "None provided"}
                - **Current Processing Status:** **{selected_row.status}**
                """)
            with col_img:
                img_path = selected_row.image_path
                if img_path and Path(str(img_path)).exists():
                    st.image(str(img_path), caption=f"Batch Photo ({selected_row.reg_id})", width=220)
                else:
                    st.caption("No photograph uploaded for this batch.")
            
            st.markdown("##### Update Custody Status")
            with st.form("partner_update_form"):
                new_st = st.selectbox("New Custody Status", STATUSES, index=STATUSES.index(selected_row.status) if selected_row.status in STATUSES else 0)
                notes_input = st.text_input("Logistics Remarks / Custody Notes", placeholder="e.g. Dispatched transport vehicle MH-12-3456, goods received at processing dock")
                if st.form_submit_button("Submit Partner Status Update"):
                    rejecting = new_st == "Rejected" and selected_row.status != "Rejected"
                    set_status(target_req_id, new_st, f"Partner: {partner_name}", notes=notes_input)
                    if rejecting:
                        rematch = rematch_after_rejection(target_req_id)
                        if rematch:
                            st.session_state["flash_collection"] = (
                                f"Request {selected_row.coll_id} was rejected by {partner_name}. "
                                f"It was automatically re-dispatched to **{rematch['name']}** ({rematch['location']}) as **{rematch['coll_id']}**."
                            )
                        else:
                            st.session_state["flash_collection"] = (
                                f"Request {selected_row.coll_id} was rejected by {partner_name}. "
                                "No further eligible partner is available, so the batch is unallocated."
                            )
                    else:
                        st.session_state["flash_collection"] = f"Request {selected_row.coll_id} updated to '{new_st}' by {partner_name}."
                    st.rerun()

st.caption("All status transitions, operator IDs, and timestamps are logged into the audited chain of custody.")
