import streamlit as st
from database import *
from classification import FTYPES
import components as ui

ui.hero("Admin Control Portal", "System administration, verification auditing, and directory governance", "shield")

if not has_admin():
    st.info("Initial System Configuration: Establish the master administrator account. Credentials are secured using salted PBKDF2-HMAC-SHA256 encryption.")
    with st.form("setup"):
        st.markdown("#### Create Master Administrator")
        u = st.text_input("Administrator Username", placeholder="e.g. admin")
        p = st.text_input("Password (minimum 8 characters)", type="password")
        p2 = st.text_input("Confirm Password", type="password")
        if st.form_submit_button("Initialize Admin Account"):
            if not u.strip() or len(p) < 8 or p != p2:
                st.error("Please enter a valid username and matching passwords of at least 8 characters.")
            else:
                create_admin(u.strip(), p)
                st.session_state["flash"] = "Administrator account successfully provisioned."
                st.rerun()
    st.stop()

if not st.session_state.get("admin"):
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_b:
        with st.form("login"):
            st.markdown("#### Administrator Authentication")
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Authenticate"):
                if check_admin(u.strip(), p):
                    st.session_state["admin"] = u.strip()
                    st.rerun()
                else:
                    st.error("Authentication failed: invalid username or password.")
    st.stop()

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

with st.sidebar:
    st.markdown(f"<div style='padding:6px 0;font-size:13px;color:#94A3B8'>Authenticated as: <b>{st.session_state['admin']}</b></div>", unsafe_allow_html=True)
    if st.button("Terminate Session (Log Out)"):
        st.session_state.pop("admin")
        st.rerun()

t1, t2, t3 = st.tabs(["Update Collection Status", "Waste Registry Management", "Facility Directory Intake"])

with t1:
    st.markdown("#### Status Progression & Custody Transition")
    r = requests_df()
    if r.empty:
        st.info("No active collection requests found in the ledger.")
    else:
        lab = {x.id: f"{x.coll_id} | {x.destination} | {x.status} ({x.quantity_kg:.0f} kg)" for x in r.itertuples()}
        rid = st.selectbox("Select Target Collection Request", list(lab), format_func=lab.get)
        cur_row = r[r.id == rid].iloc[0]
        cur = cur_row.status
        st.caption(f"Current Status: **{cur}** | Batch ID: `{cur_row.reg_id}`")
        
        # Display registrant details and photo
        col_meta, col_photo = st.columns([2, 1])
        with col_meta:
            st.markdown(f"""
            - **Contact Person:** `{cur_row.generator}` ({cur_row.institution})
            - **Phone / Email:** **{cur_row.generator_contact}**
            - **Item Classification:** {cur_row.item_type}
            - **Material & Condition:** {cur_row.material} ({cur_row.cond})
            - **Origin Location:** {cur_row.waste_location}
            - **Batch Quantity:** **{cur_row.quantity_kg:.1f} kg**
            - **Assigned Partner:** {cur_row.destination} ({cur_row.ftype})
            - **Batch Notes / Description:** {cur_row.batch_description if str(cur_row.batch_description).strip() else "None"}
            """)
        with col_photo:
            from pathlib import Path
            img_p = cur_row.image_path
            if img_p and Path(str(img_p)).exists():
                st.image(str(img_p), caption=f"Batch Photograph ({cur_row.reg_id})", width=220)
            else:
                st.caption("No photograph registered for this batch.")
        
        c1, c2 = st.columns([2, 1])
        new = c1.selectbox("Transition To New Status", STATUSES, index=STATUSES.index(cur) if cur in STATUSES else 0, key=f"status_select_{rid}")
        notes = st.text_input("Audit Notes / Operational Remarks", placeholder="e.g. Verified by facility supervisor, pickup scheduled", key=f"admin_notes_{rid}")
        if c2.button("Apply Status Change", key=f"btn_status_{rid}"):
            rejecting = new == "Rejected" and cur != "Rejected"
            set_status(rid, new, st.session_state["admin"], notes=notes)
            if rejecting:
                rematch = rematch_after_rejection(rid)
                if rematch:
                    st.session_state["flash"] = (
                        f"Request {r[r.id == rid].coll_id.iloc[0]} marked Rejected. "
                        f"Automatically re-matched to **{rematch['name']}** ({rematch['location']}) as **{rematch['coll_id']}**."
                    )
                else:
                    st.session_state["flash"] = f"Request {r[r.id == rid].coll_id.iloc[0]} marked Rejected. No further eligible partner available."
            else:
                st.session_state["flash"] = f"Request {r[r.id == rid].coll_id.iloc[0]} transitioned from {cur} to {new}."
            st.rerun()

with t2:
    st.markdown("#### Registered Waste Ledger")
    w = query("SELECT id, reg_id, generator, institution, gen_category, item_type, material, cond, quantity_kg, location, contact, reg_date, description, waste_category, image_path, is_demo FROM waste_records ORDER BY id DESC")
    if w.empty:
        st.info("No registered waste records present in the database.")
    else:
        lab = {x.id: f"{x.reg_id} | {x.generator} - {x.institution} ({x.quantity_kg:.1f} kg){' [DEMO]' if x.is_demo else ''}" for x in w.itertuples()}
        wid = st.selectbox("Select Batch Record", list(lab), format_func=lab.get)
        w_row = w[w.id == wid].iloc[0]
        current_qty = float(w_row.quantity_kg)
        
        col_w_info, col_w_img = st.columns([2, 1])
        with col_w_info:
            st.markdown(f"""
            ##### Generator & Registration Details
            - **Contact Person:** `{w_row.generator}`
            - **Institution / Entity:** {w_row.institution}
            - **Sector / Category:** {w_row.gen_category}
            - **Phone / Email:** **{w_row.contact}**
            - **Origin Location:** {w_row.location}
            - **Registration Date:** {w_row.reg_date}
            - **Item Classification:** {w_row.item_type}
            - **Material & Condition:** {w_row.material} ({w_row.cond})
            - **Classification Outcome:** `{w_row.waste_category}`
            - **Batch Description / Notes:** {w_row.description if str(w_row.description).strip() else "None provided"}
            """)
        with col_w_img:
            from pathlib import Path
            img_p = w_row.image_path
            if img_p and Path(str(img_p)).exists():
                st.image(str(img_p), caption=f"Batch Photograph ({w_row.reg_id})", width=250)
            else:
                st.caption("No photograph uploaded for this batch.")
            
        q = st.number_input("Verified Weight Correction (kg)", min_value=0.1, value=current_qty, step=0.5, key=f"weight_input_{wid}")
        c_save, c_del = st.columns([1, 1])
        if c_save.button("Save Weight Correction", key=f"btn_weight_{wid}"):
            try:
                update_waste_qty(wid, q)
                st.session_state["flash"] = "Record quantity adjusted successfully."
                st.rerun()
            except ValueError as e:
                st.error(str(e))
                
        st.markdown("<div style='height: 10px'></div>", unsafe_allow_html=True)
        if st.checkbox("Confirm permanent deletion of this record and all associated requests"):
            if st.button("Delete Waste Record"):
                delete_waste(wid)
                st.session_state["flash"] = "Record and linked dependencies permanently purged."
                st.rerun()

with t3:
    st.markdown("#### Onboard Destination Facility or Recycler")
    with st.form("addfac"):
        c1, c2 = st.columns(2)
        n = c1.text_input("Facility / Organization Name")
        ft = c2.selectbox("Facility Classification", FTYPES)
        ms = c1.text_input("Accepted Fiber / Materials (comma separated, or All)", "All")
        col = c2.text_input("Collection Availability", "Through scheduled pickup or drop-off")
        lc = c1.text_input("Operational Location / City")
        ct = c2.text_input("Official Contact Channel", "contact@facility.org")
        ds = st.text_area("Intake Scope, Equipment Capabilities & Criteria")
        
        if st.form_submit_button("Register Facility in Directory"):
            try:
                add_recycler(n, ft, ms, col, lc, ct, ds)
                st.session_state["flash"] = f"Facility '{n.strip()}' successfully onboarded."
                st.rerun()
            except ValueError as e:
                st.error(str(e))
