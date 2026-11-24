import re
import sqlite3, hashlib, hmac, secrets
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from classification import classify, SUITABLE

DB = Path(__file__).parent / "data" / "textileloop.db"
UPLOAD_DIR = Path(__file__).parent / "data" / "uploads"
PROCESSING_OUTCOMES = ("Reused", "Recycled", "Upcycled (Craft Products)")
STATUSES = ["Pending", "Approved", "Scheduled", "Collected", "Processing", *PROCESSING_OUTCOMES, "Disposed", "Rejected"]
_PLACE_STOP = {
    "and", "the", "via", "with", "for", "from", "partners", "partner",
    "drives", "drive", "through", "confirm", "facility", "service", "area",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, pw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS waste_records(id INTEGER PRIMARY KEY, reg_id TEXT UNIQUE NOT NULL, generator TEXT NOT NULL, institution TEXT NOT NULL,
 gen_category TEXT NOT NULL, item_type TEXT NOT NULL, material TEXT NOT NULL, cond TEXT NOT NULL, quantity_kg REAL NOT NULL CHECK(quantity_kg>0),
 location TEXT NOT NULL, contact TEXT NOT NULL, reg_date TEXT NOT NULL, description TEXT, waste_category TEXT NOT NULL, image_path TEXT, is_demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS recycler_directory(id INTEGER PRIMARY KEY, name TEXT NOT NULL, ftype TEXT NOT NULL, materials TEXT NOT NULL,
 collection TEXT NOT NULL, location TEXT NOT NULL, contact TEXT NOT NULL, description TEXT, is_demo INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS collection_requests(id INTEGER PRIMARY KEY, coll_id TEXT UNIQUE NOT NULL,
 waste_id INTEGER NOT NULL REFERENCES waste_records(id) ON DELETE CASCADE, recycler_id INTEGER NOT NULL REFERENCES recycler_directory(id),
 quantity_kg REAL NOT NULL CHECK(quantity_kg>0), request_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending');
CREATE TABLE IF NOT EXISTS processing_records(id INTEGER PRIMARY KEY, collection_id INTEGER UNIQUE NOT NULL REFERENCES collection_requests(id) ON DELETE CASCADE,
 outcome TEXT NOT NULL CHECK(outcome IN ('Reused','Recycled','Upcycled (Craft Products)')), quantity_kg REAL NOT NULL, verified INTEGER NOT NULL DEFAULT 1, processed_date TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status_log(id INTEGER PRIMARY KEY, collection_id INTEGER NOT NULL REFERENCES collection_requests(id) ON DELETE CASCADE,
 old_status TEXT, new_status TEXT NOT NULL, changed_by TEXT, notes TEXT, changed_at TEXT NOT NULL);
"""

def conn():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("PRAGMA foreign_keys=ON")
    return c

def query(sql, params=()):
    with closing(conn()) as c:
        return pd.read_sql_query(sql, c, params=params)

def _id(prefix):
    return f"{prefix}-{date.today():%Y%m%d}-{secrets.token_hex(2).upper()}"

def save_image(file_obj, reg_id):
    """Save an uploaded image or camera capture to disk and return path."""
    if file_obj is None:
        return None
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Determine extension
    ext = "jpg"
    name = getattr(file_obj, "name", "")
    if "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
    filename = f"{reg_id}.{ext}"
    dest = UPLOAD_DIR / filename
    
    # Read bytes from UploadedFile or BytesIO
    if hasattr(file_obj, "getvalue"):
        data = file_obj.getvalue()
    elif hasattr(file_obj, "read"):
        data = file_obj.read()
    else:
        data = bytes(file_obj)
        
    with open(dest, "wb") as f:
        f.write(data)
    return str(dest)

def _insert_waste(c, d):
    cat = classify(d["material"], d["cond"], d["gen_category"])["category"]
    reg = _id("TW")
    img_path = d.get("image_path")
    if not img_path and d.get("file_obj"):
        img_path = save_image(d["file_obj"], reg)
    c.execute("INSERT INTO waste_records(reg_id,generator,institution,gen_category,item_type,material,cond,quantity_kg,location,contact,reg_date,description,waste_category,image_path,is_demo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (reg, d["generator"], d["institution"], d["gen_category"], d["item_type"], d["material"], d["cond"], d["quantity_kg"],
               d["location"], d["contact"], str(d["reg_date"]), d.get("description", ""), cat, img_path, d.get("is_demo", 0)))
    return reg

def add_waste(d):
    if d["quantity_kg"] <= 0:
        raise ValueError("Quantity must be greater than zero.")
    with closing(conn()) as c, c:
        return _insert_waste(c, d)

def remaining(waste_id):
    df = query("SELECT w.quantity_kg - COALESCE((SELECT SUM(quantity_kg) FROM collection_requests WHERE waste_id=w.id AND status!='Rejected'),0) AS r FROM waste_records w WHERE w.id=?", (int(waste_id),))
    return float(df.r[0]) if len(df) else 0.0

def add_request(waste_id, recycler_id, qty, notes=""):
    with closing(conn()) as c, c:
        total = c.execute("SELECT quantity_kg FROM waste_records WHERE id=?", (int(waste_id),)).fetchone()[0]
        used = c.execute("SELECT COALESCE(SUM(quantity_kg),0) FROM collection_requests WHERE waste_id=? AND status!='Rejected'", (int(waste_id),)).fetchone()[0]
        if qty <= 0 or qty > total - used + 1e-9:
            raise ValueError(f"Quantity must be between 0 and {total - used:.1f} kg (what is left unrequested).")
        cid = _id("CR")
        c.execute("INSERT INTO collection_requests(coll_id,waste_id,recycler_id,quantity_kg,request_date,status) VALUES(?,?,?,?,?,'Pending')",
                  (cid, int(waste_id), int(recycler_id), qty, str(date.today())))
        c.execute("INSERT INTO status_log(collection_id,old_status,new_status,changed_by,notes,changed_at) VALUES(last_insert_rowid(),NULL,'Pending','system',?,datetime('now'))",
                  (notes.strip() if notes else "Initial dispatch request initiated",))
        return cid

def set_status(req_id, new, user, notes=""):
    with closing(conn()) as c, c:
        row = c.execute("SELECT status, quantity_kg FROM collection_requests WHERE id=?", (int(req_id),)).fetchone()
        if row is None or row[0] == new:
            return
        c.execute("UPDATE collection_requests SET status=? WHERE id=?", (new, int(req_id)))
        c.execute("INSERT INTO status_log(collection_id,old_status,new_status,changed_by,notes,changed_at) VALUES(?,?,?,?,?,datetime('now'))",
                  (int(req_id), row[0], new, user, notes.strip() if notes else None))
        if new in PROCESSING_OUTCOMES:  # UNIQUE(collection_id) means one processing record per request
            c.execute("INSERT OR REPLACE INTO processing_records(collection_id,outcome,quantity_kg,verified,processed_date) VALUES(?,?,?,1,date('now'))", (int(req_id), new, row[1]))
        else:
            c.execute("DELETE FROM processing_records WHERE collection_id=?", (int(req_id),))

def update_waste_qty(wid, qty):
    with closing(conn()) as c, c:
        used = c.execute("SELECT COALESCE(SUM(quantity_kg),0) FROM collection_requests WHERE waste_id=? AND status!='Rejected'", (int(wid),)).fetchone()[0]
        if qty <= 0 or qty < used:
            raise ValueError(f"Quantity must be above 0 and at least the {used:.1f} kg already requested.")
        c.execute("UPDATE waste_records SET quantity_kg=? WHERE id=?", (qty, int(wid)))

def remove_waste_image(wid):
    """Delete the uploaded image file from disk and clear its path in the database."""
    with closing(conn()) as c, c:
        row = c.execute("SELECT image_path FROM waste_records WHERE id=?", (int(wid),)).fetchone()
        if row and row[0]:
            p = Path(row[0])
            if p.exists():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            c.execute("UPDATE waste_records SET image_path=NULL WHERE id=?", (int(wid),))

def delete_waste(wid):
    remove_waste_image(wid)
    with closing(conn()) as c, c:
        c.execute("DELETE FROM waste_records WHERE id=?", (int(wid),))

def requests_df(exclude_demo=False):
    sql = ("SELECT cr.id, cr.coll_id, w.id AS waste_id, w.reg_id, w.generator, w.institution, w.contact AS generator_contact, "
           "w.item_type, w.material, w.cond, w.waste_category, w.description AS batch_description, w.reg_date, w.image_path, w.is_demo, "
           "w.location AS waste_location, rd.id AS recycler_id, rd.name AS destination, rd.ftype, rd.location AS recycler_location, "
           "cr.quantity_kg, cr.request_date, cr.status "
           "FROM collection_requests cr JOIN waste_records w ON w.id=cr.waste_id JOIN recycler_directory rd ON rd.id=cr.recycler_id ")
    if exclude_demo:
        sql += "WHERE w.is_demo=0 "
    sql += "ORDER BY cr.id DESC"
    return query(sql)

def log_df(req_id):
    return query("SELECT old_status, new_status, changed_by, notes, changed_at FROM status_log WHERE collection_id=? ORDER BY id", (int(req_id),))

def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()

def has_admin():
    return int(query("SELECT COUNT(*) n FROM users").n[0]) > 0

def create_admin(username, pw):
    salt = secrets.token_hex(16)
    with closing(conn()) as c, c:
        c.execute("INSERT INTO users(username,salt,pw_hash) VALUES(?,?,?)", (username, salt, _hash(pw, salt)))

def check_admin(username, pw):
    df = query("SELECT salt, pw_hash FROM users WHERE username=?", (username,))
    return len(df) == 1 and hmac.compare_digest(df.pw_hash[0], _hash(pw, df.salt[0]))

FACILITIES = [
    # Mumbai Cloth Donation & Recycling Centres
    ("Clothes donation by Hopenlife Foundation", "Donation centre", "All", "Drop-off & scheduled collection drives", "Mumbai",
     "https://share.google/jv1MqvqCmWuAbq5iy",
     "Mumbai donation initiative. Accepts wearable garments, clothes, blankets, and textiles for charitable distribution to underprivileged communities."),
    ("Roadside Family Foundation", "NGO", "All", "Drop-off and local collection", "Mumbai",
     "https://share.google/BhmnvZN5ZAnxpgbwb",
     "Mumbai welfare NGO providing clothing support, warm apparel, and essential textiles to roadside and homeless families."),
    ("ReCircle Head Office (Swachh Sustainable Solutions)", "Textile recycling company", "All", "Institutional drives & commercial pickup", "Mumbai",
     "https://share.google/1mNvP61uVLKqYheU1",
     "Leading circular resource recovery enterprise in Mumbai. Collects post-consumer garments, institutional textiles, and routes non-wearable fabrics to recycling partners."),
    ("Clothing donation center by bombay recycling concern", "Donation centre", "All", "Drop-off center & drives", "Mumbai",
     "https://share.google/TWpCcBQzKIBGdll2g",
     "Textile collection and sorting center in Mumbai. Accepts wearable clothing for donation and channels damaged fabric scraps to upcycling/recycling."),
    ("Sukachi Sawali Welfare Foundation", "NGO", "All", "Drop-off & community drives", "Mumbai",
     "https://share.google/9XxuOf660UOQJoBlu",
     "Welfare foundation in Mumbai accepting old clothes, bedsheets, and garments for distribution to marginalized families."),

    # Latur Cloth Donation & Upcycling Centres
    ("Kanakdhara Charitable Trust Latur", "NGO", "All", "Drop-off & community drives", "Latur",
     "https://share.google/I8cLJrWD041GFO6KB",
     "Latur-based charitable trust coordinating cloth donation drives and distribution to rural and drought-affected families in Marathwada."),
    ("Sakhi Social Enterprise Network", "Upcycling organization", "All", "Artisan collection & workshop drop-off", "Latur",
     "https://share.google/1cfITxEpOHyPMqz7k",
     "Social enterprise in Latur empowering rural women artisans. Accepts clean cotton, silk, and fabric scraps for handcrafted textile and artisan upcycling."),
    ("Bal Vikas Mahila Mandal", "NGO", "All", "Drop-off center & drives", "Latur",
     "https://share.google/sNLKQk8iizXEDn1Q8",
     "Women-led NGO in Latur working in child development and women welfare. Collects wearable garments, uniforms, and warm clothing for children and women."),
    ("Kapada Bank Latur", "Donation centre", "All", "Drop-off & phone coordination", "Latur",
     "Phone: 7020630453",
     "Cloth bank initiative in Latur providing free clothing to economically weaker sections. Direct coordination via 7020630453 for drop-off and pickups."),

    # Amravati Cloth Donation, NGOs & Upcycling Centres
    ("DISHA for Victims (Disha Foundation)", "NGO", "All", "Drop-off & community drives", "Amravati",
     "https://share.google/pt0EL5prC3fakWXv2",
     "Amravati NGO supporting rehabilitation and victim welfare. Accepts wearable clothing, blankets, and garments for distribution to distressed and vulnerable families."),
    ("SARE Foundation", "NGO", "All", "Drop-off center & scheduled drives", "Amravati",
     "https://share.google/aDhiVZ9zoH6dNWrez",
     "Amravati-based social development foundation. Coordinates textile and cloth collection drives for underprivileged rural and urban communities."),
    ("Sukhaya Foundation", "Donation centre", "All", "Drop-off & community outreach", "Amravati",
     "https://share.google/tty5SsSLz7qXPGuSc",
     "Charitable foundation in Amravati facilitating used clothing donations, warm wear, and household linen distribution for low-income households."),
    ("Gokul Ashram", "Donation centre", "All", "Direct drop-off at Ashram", "Amravati",
     "https://share.google/nwP0V84VPUGBNAepk",
     "Ashram and residential shelter in Amravati. Accepts wearable clothing, blankets, bedsheets, and everyday apparel for resident care and community aid."),
    ("Hope Multipurpose Society", "NGO", "All", "Drop-off and local collection", "Amravati",
     "https://share.google/XYsaKIFTNh1Fehcsg",
     "Multipurpose welfare society operating across Amravati district. Distributes donated garments, educational uniforms, and clothes to underprivileged children and families."),
    ("Maseera Matching Center & Textile Bank", "Upcycling organization", "All", "Drop-off & artisan coordination", "Amravati",
     "https://share.google/ZeC5K4eU0MKShnrrW",
     "Textile and cloth centre in Amravati. Engages in sorting reusable fabrics, matching materials, and coordinating fabric repurposing and local cloth distribution."),

    # Pune Recycling, Upcycling, NGOs & Clinical Disposal
    ("Eco Regain Solutions Pvt. Ltd.", "Upcycling organization", "All", "Confirm with facility", "Pune",
     "Confirm directly with the organization",
     "Pune-based upcycling enterprise. Accepts old clothes and scrap fabrics to produce handcrafted rugs, bags, and artisan home products."),
    ("SWaCH Pune - V Collect", "NGO", "All", "V Collect pickup & drop-off centers", "Pune",
     "https://swachcoop.com",
     "Waste picker cooperative working with Pune Municipal Corporation. V Collect takes reusable clothes, linens, and apparel for community redistribution."),
    ("Samajbandh", "NGO", "All", "Community collection drives", "Pune",
     "Confirm directly with the organization",
     "Pune NGO working in tribal and rural areas of Maharashtra. Distributes donated clothing, blankets, and garments."),
    ("Respun", "Textile recycling company", "All", "Collection drives & institutional pickup", "Pune / National",
     "Confirm directly with the organization",
     "Collects used clothing from educational institutions, colleges, and drives for fiber regeneration and recycling."),
    ("Passco Environmental Solutions Pvt. Ltd.", "Authorized waste collection agency", "All", "Through institutional membership", "Moshi, Pune",
     "Authorized MPCB handler",
     "Common biomedical waste treatment facility in Moshi, Pune. Strictly authorized for clinical and hazardous healthcare textiles."),
    ("Life Secure Enterprises", "Authorized waste collection agency", "All", "Through institutional membership", "Talegaon Dabhade, Pune",
     "Authorized MPCB handler",
     "Common biomedical waste treatment facility in Talegaon Dabhade, Pune. Authorized handler for infectious linens and bio-waste."),
]

def add_recycler(name, ftype, materials, collection, location, contact, description):
    if not (name.strip() and location.strip()):
        raise ValueError("Name and location are required.")
    with closing(conn()) as c, c:
        c.execute("INSERT INTO recycler_directory(name,ftype,materials,collection,location,contact,description,is_demo) VALUES(?,?,?,?,?,?,?,0)",
                  (name.strip(), ftype, materials.strip() or "All", collection.strip() or "Confirm with facility", location.strip(), contact.strip() or "Confirm directly", description.strip()))

def _place_tokens(text):
    return {p for p in re.split(r"[^a-z0-9]+", str(text).lower()) if len(p) > 2 and p not in _PLACE_STOP}

def _accepts_material(materials, material):
    accepted = [m.strip() for m in str(materials).split(",")]
    return "All" in accepted or material in accepted

def region_score(waste_location, facility_location):
    """Higher scores mean a closer geographic fit. Exact or contained locations rank above a national listing."""
    waste = str(waste_location).strip().lower()
    facility = str(facility_location).strip().lower()
    if not waste or not facility:
        return 0
    if waste == facility or facility in waste:
        return 100
    waste_tokens = _place_tokens(waste)
    facility_tokens = _place_tokens(facility)
    overlap = waste_tokens & facility_tokens
    if overlap:
        return 60 + 10 * len(overlap) - len(facility_tokens - waste_tokens)
    if "india" in facility_tokens:
        return 10
    return 0

def best_partner(waste_id, exclude_ids=()):
    """Pick the eligible directory partner with the closest category and region fit."""
    waste = query("SELECT id, material, waste_category, location FROM waste_records WHERE id=?", (int(waste_id),))
    if waste.empty:
        return None
    row = waste.iloc[0]
    suitable = SUITABLE.get(row.waste_category, [])
    if not suitable:
        return None
    directory = query("SELECT id, name, ftype, materials, location FROM recycler_directory")
    excluded = {int(i) for i in exclude_ids}
    ranked = []
    for partner in directory.itertuples(index=False):
        if int(partner.id) in excluded or partner.ftype not in suitable:
            continue
        if not _accepts_material(partner.materials, row.material):
            continue
        score = region_score(row.location, partner.location)
        ranked.append((
            score,
            len(_place_tokens(partner.location)),
            -suitable.index(partner.ftype),
            -int(partner.id),
            {"id": int(partner.id), "name": partner.name, "ftype": partner.ftype, "location": partner.location, "score": score},
        ))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:4], reverse=True)
    return ranked[0][4]

def _dispatch(waste_id, qty, notes, exclude_ids=()):
    partner = best_partner(waste_id, exclude_ids)
    if partner is None:
        return None
    partner["coll_id"] = add_request(int(waste_id), partner["id"], float(qty), notes=notes)
    return partner

def dispatch_new_registration(reg_id, preferred_partner_id=None):
    """Create a collection request for a newly registered batch using either a preferred partner or the best directory match."""
    waste = query("SELECT id, quantity_kg FROM waste_records WHERE reg_id=?", (reg_id,))
    if waste.empty:
        return None
    waste_id = int(waste.id[0])
    qty = float(waste.quantity_kg[0])
    
    if preferred_partner_id:
        partner_df = query("SELECT id, name, ftype, location FROM recycler_directory WHERE id=?", (int(preferred_partner_id),))
        if not partner_df.empty:
            p_row = partner_df.iloc[0]
            cid = add_request(waste_id, int(preferred_partner_id), qty, notes="User manually selected preferred destination facility during registration.")
            return {"id": int(preferred_partner_id), "name": p_row["name"], "ftype": p_row["ftype"], "location": p_row["location"], "coll_id": cid}

    return _dispatch(
        waste_id,
        qty,
        "Automatically matched at registration by category and region.",
    )

def rematch_after_rejection(req_id):
    """Send a rejected quantity to the next eligible partner that has not already been tried."""
    req = query(
        "SELECT cr.waste_id, cr.quantity_kg, cr.status, rd.name AS rejected_by "
        "FROM collection_requests cr JOIN recycler_directory rd ON rd.id=cr.recycler_id WHERE cr.id=?",
        (int(req_id),),
    )
    if req.empty or req.status[0] != "Rejected":
        return None
    waste_id = int(req.waste_id[0])
    tried = query("SELECT recycler_id FROM collection_requests WHERE waste_id=?", (waste_id,))
    return _dispatch(
        waste_id,
        float(req.quantity_kg[0]),
        f"Automatically re-dispatched after rejection by {req.rejected_by[0]}.",
        exclude_ids=[int(x) for x in tried.recycler_id.tolist()],
    )

def _ensure_processing_outcomes(c):
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='processing_records'").fetchone()
    if row is None or "Upcycled" in (row[0] or ""):
        return
    c.executescript("""
        CREATE TABLE processing_records_new(id INTEGER PRIMARY KEY, collection_id INTEGER UNIQUE NOT NULL REFERENCES collection_requests(id) ON DELETE CASCADE,
         outcome TEXT NOT NULL CHECK(outcome IN ('Reused','Recycled','Upcycled (Craft Products)')), quantity_kg REAL NOT NULL, verified INTEGER NOT NULL DEFAULT 1, processed_date TEXT NOT NULL);
        INSERT INTO processing_records_new(id, collection_id, outcome, quantity_kg, verified, processed_date)
         SELECT id, collection_id, outcome, quantity_kg, verified, processed_date FROM processing_records;
        DROP TABLE processing_records;
        ALTER TABLE processing_records_new RENAME TO processing_records;
    """)

def init_db():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with closing(conn()) as c, c:
        c.executescript(SCHEMA)
        _ensure_processing_outcomes(c)
        # column migrations
        cols_w = [row[1] for row in c.execute("PRAGMA table_info(waste_records)").fetchall()]
        if "image_path" not in cols_w:
            c.execute("ALTER TABLE waste_records ADD COLUMN image_path TEXT")
        cols_s = [row[1] for row in c.execute("PRAGMA table_info(status_log)").fetchall()]
        if "notes" not in cols_s:
            c.execute("ALTER TABLE status_log ADD COLUMN notes TEXT")
        # remove the fictional facilities from earlier versions (and requests linked to them)
        c.execute("DELETE FROM collection_requests WHERE recycler_id IN (SELECT id FROM recycler_directory WHERE name LIKE '%(DEMO)')")
        c.execute("DELETE FROM recycler_directory WHERE name LIKE '%(DEMO)'")
        # remove all demo waste batches and their requests to ensure 100% real database statistics
        c.execute("DELETE FROM collection_requests WHERE waste_id IN (SELECT id FROM waste_records WHERE is_demo=1)")
        c.execute("DELETE FROM waste_records WHERE is_demo=1")
        existing_names = {row[0] for row in c.execute("SELECT name FROM recycler_directory").fetchall()}
        to_insert = [f for f in FACILITIES if f[0] not in existing_names]
        if to_insert:
            c.executemany("INSERT INTO recycler_directory(name,ftype,materials,collection,location,contact,description,is_demo) VALUES(?,?,?,?,?,?,?,0)", to_insert)
