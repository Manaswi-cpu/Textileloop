import re
from datetime import date
import streamlit as st
from database import add_waste, dispatch_new_registration, query
from classification import *
import components as ui

ui.hero("Register Textile Waste", "Log institutional and commercial fabric inventory for automated classification and collection", "document")

st.subheader("1. Waste Origin & Generator")
c1, c2 = st.columns(2)
gen = c1.text_input("Point of Contact / Generator Name", placeholder="e.g. Dr. Rajesh Sharma", key="reg_gen")
inst = c2.text_input("Institution or Organization", placeholder="e.g. Pune Institute of Technology", key="reg_inst")
gc = c1.selectbox("Generator Sector", GEN_CATS, key="reg_gc")
loc = c2.text_input("Facility Location / City", placeholder="e.g. Pune, Mumbai, Latur, Amravati", key="reg_loc")
contact = c1.text_input("Contact Email or Phone Number", placeholder="e.g. contact@institution.edu or 9876543210", key="reg_contact")
rdate = c2.date_input("Registration Date", value=date.today(), max_value=date.today(), key="reg_date")

st.subheader("2. Material Specifications")
m1, m2, m3 = st.columns(3)
item = m1.selectbox("Item Classification", ITEM_TYPES, key="reg_item")
mat = m2.selectbox("Primary Fiber / Material", MATERIALS, key="reg_mat")
cond = m3.selectbox("Material Condition", CONDITIONS, key="reg_cond")

q1, q2 = st.columns([1, 2])
qty = q1.number_input("Batch Quantity (kg)", min_value=0.0, step=0.5, format="%.1f", key="reg_qty")
desc = q2.text_input("Additional Notes or Batch Description", placeholder="e.g. Sorted uniforms, buttons removed, clean dry storage", key="reg_desc")

st.subheader("3. Batch Photograph Verification")
st.caption("Upload or capture a photograph of the fabric batch for visual condition verification by recycling partners and administrators.")
photo_mode = st.radio("Photograph Method", ["Capture with Camera", "Upload Image File", "No Photograph"], horizontal=True, key="reg_photo_mode")

active_img = None
if photo_mode == "Capture with Camera":
    st.info("Allow camera access in your browser when prompted, then click 'Take Photo'.")
    active_img = st.camera_input("Device Camera Preview", key="reg_cam_input")
elif photo_mode == "Upload Image File":
    active_img = st.file_uploader("Select Image File (JPG, JPEG, PNG, WEBP)", type=["jpg", "jpeg", "png", "webp"], key="reg_file_up")

if active_img:
    st.image(active_img, caption="Photograph Preview — Verified for Ingestion", width=320)

st.subheader("4. Destination Facility & NGO Selection")
dest_mode = st.radio(
    "Destination Routing Option",
    ["Automatic Recommendation & Matching (Default)", "Select Specific NGO / Donation Centre / Recycler"],
    horizontal=True,
    key="reg_dest_mode"
)

selected_partner_id = None
if dest_mode == "Select Specific NGO / Donation Centre / Recycler":
    st.caption("You can choose your preferred destination facility directly from verified NGOs and recycling centers.")
    dir_df = query("SELECT id, name, ftype, materials, collection, location, contact, description FROM recycler_directory ORDER BY name")
    
    col_cfilter, col_pselect = st.columns([1, 2])
    city_choice = col_cfilter.selectbox(
        "Filter by City",
        ["All Cities", "Amravati", "Latur", "Mumbai", "Pune", "Other Regions"],
        key="reg_ngo_city_filter"
    )
    
    filtered_dir = dir_df
    if city_choice == "Amravati":
        filtered_dir = filtered_dir[filtered_dir.location.str.contains("Amravati", case=False, na=False)]
    elif city_choice == "Latur":
        filtered_dir = filtered_dir[filtered_dir.location.str.contains("Latur", case=False, na=False)]
    elif city_choice == "Mumbai":
        filtered_dir = filtered_dir[filtered_dir.location.str.contains("Mumbai", case=False, na=False)]
    elif city_choice == "Pune":
        filtered_dir = filtered_dir[filtered_dir.location.str.contains("Pune|Moshi|Talegaon", case=False, na=False)]
    elif city_choice == "Other Regions":
        filtered_dir = filtered_dir[~filtered_dir.location.str.contains("Amravati|Latur|Mumbai|Pune|Moshi|Talegaon", case=False, na=False)]
        
    if filtered_dir.empty:
        st.warning("No facilities listed under this city filter.")
    else:
        partner_labels = {
            row.id: f"{row.name} ({row.location} — {row.ftype})"
            for row in filtered_dir.itertuples()
        }
        selected_partner_id = col_pselect.selectbox(
            "Select Receiving NGO / Facility",
            list(partner_labels),
            format_func=partner_labels.get,
            key="reg_selected_partner"
        )
        
        # Display selected partner brief
        chosen_partner = filtered_dir[filtered_dir.id == selected_partner_id].iloc[0]
        st.info(
            f"📍 **Selected Destination:** **{chosen_partner['name']}**\n\n"
            f"- **Classification:** `{chosen_partner['ftype']}` | **Location:** {chosen_partner['location']}\n"
            f"- **Collection Method:** {chosen_partner['collection']}\n"
            f"- **Intake Scope:** {chosen_partner['description']}"
        )
else:
    st.info("🤖 **Automatic Matching Enabled:** The system will automatically evaluate fabric fiber type, condition, and location to connect your batch to the most suitable registered facility.")

st.markdown("<div style='height: 14px'></div>", unsafe_allow_html=True)
go = st.button("Submit Waste Registration", key="btn_reg_submit", type="primary")

if go:
    ok_contact = re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact.strip()) or len(re.sub(r"\D", "", contact)) >= 10
    if not (gen.strip() and inst.strip() and loc.strip()):
        st.error("Generator name, institution, and location are mandatory fields.")
    elif qty <= 0:
        st.error("Batch quantity must be greater than 0 kg.")
    elif not ok_contact:
        st.error("Please provide a valid email address or 10-digit telephone number.")
    else:
        reg = add_waste(dict(
            generator=gen.strip(),
            institution=inst.strip(),
            gen_category=gc,
            item_type=item,
            material=mat,
            cond=cond,
            quantity_kg=qty,
            location=loc.strip(),
            contact=contact.strip(),
            reg_date=rdate,
            description=desc.strip(),
            file_obj=active_img
        ))
        match = dispatch_new_registration(reg, preferred_partner_id=selected_partner_id)
        if match:
            dest_verb = "Directly connected to selected facility" if selected_partner_id else "Automatically matched to"
            st.success(
                f"Registration successful! System Tracking Identifier: **{reg}**. "
                f"{dest_verb} **{match['name']}** ({match['location']}) — Collection Request **{match['coll_id']}** created."
            )
        else:
            st.success(f"Registration successful. System Tracking Identifier: **{reg}**")
            st.warning("No eligible partner matched this category and region. The batch is saved and remains unallocated.")
        if active_img:
            st.info("Batch photograph stored and linked to Registration ID.")
        ui.result_card(classify(mat, cond, gc))
