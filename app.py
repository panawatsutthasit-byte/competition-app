from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3, os, threading, webbrowser, datetime

# ---------------------- Config / Env ----------------------
def env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")

# พาธ DB: ถ้าอยู่บน Render ให้ตั้งเป็น /var/data/app.db ผ่าน ENV
DB_PATH = os.environ.get("DB_PATH", "app.db")

# ให้แน่ใจว่าโฟลเดอร์ของ DB มีอยู่ (เช่น /var/data)
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# พาธของไฟล์ schema.sql แบบ absolute (กันปัญหาบน Render/Gunicorn)
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# ---------- Site config (แก้ง่าย / override ด้วย ENV ได้) ----------
app.config.update(
    SITE_NAME     = os.environ.get("SITE_NAME", "Competition Registration"),
    EVENT_DATE    = os.environ.get("EVENT_DATE", "2025-09-30"),        # YYYY-MM-DD
    EVENT_LOCATION= os.environ.get("EVENT_LOCATION", "สนามกีฬาโรงเรียนของคุณ"),
    REG_DEADLINE  = os.environ.get("REG_DEADLINE", "2025-09-25"),      # YYYY-MM-DD
    REG_OPEN      = env_bool("REG_OPEN", True),                        # ปิดรับสมัคร = False
)

# โหมดรัน (สำหรับ dev/prod)
DEBUG_MODE   = env_bool("DEBUG", False) or env_bool("FLASK_DEBUG", False)
OPEN_BROWSER = env_bool("OPEN_BROWSER", True) and not os.environ.get("RENDER")  # ปิดบน Render

# ให้ทุกเทมเพลตใช้ตัวแปร site_name ได้
@app.context_processor
def inject_site_name():
    return dict(site_name=app.config["SITE_NAME"])

# ---------------------- Utilities ----------------------
def _enable_fk(con: sqlite3.Connection):
    try:
        con.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass

def get_db():
    con = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    _enable_fk(con)
    return con

def parse_date_yyyy_mm_dd(s: str):
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None

def today():
    return datetime.date.today().isoformat()

# ---------------------- DB init / migrate ----------------------
def init_db():
    """สร้างตารางครั้งแรกจาก schema.sql หรือ migrate ถ้ามี DB เดิม"""
    first_time = not os.path.exists(DB_PATH)
    con = get_db()

    if first_time:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            con.executescript(f.read())
        # seed categories
        cats = [
            ("วิ่ง 100 ม.",), ("วิ่ง 200 ม.",), ("กระโดดไกล",), ("พุ่งแหลน",), ("ว่ายน้ำ",),
            ("แบดมินตัน",), ("ฟุตบอล",), ("บาสเกตบอล",), ("เทควันโด",), ("หมากล้อม",)
        ]
        con.executemany("INSERT OR IGNORE INTO category(name) VALUES(?)", cats)
        con.commit()
        con.close()
        return

    # ----- กรณีมี DB เดิม: ทำ migration -----
    try:
        con.execute("ALTER TABLE participant ADD COLUMN competition_date TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE result ADD COLUMN event_date TEXT")
    except sqlite3.OperationalError:
        pass

    # เติมค่าเริ่มต้นให้เรคคอร์ดเก่า
    con.execute("UPDATE participant SET competition_date = DATE('now') WHERE competition_date IS NULL")
    con.execute("UPDATE result SET event_date = DATE('now') WHERE event_date IS NULL")

    # ดัชนีสำคัญ
    con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_participant_unique
                   ON participant(first_name, last_name, school_id, category_id, competition_date)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_participant_date ON participant(competition_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_result_date ON result(event_date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_participant_cat ON participant(category_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_participant_school ON participant(school_id)")
    con.commit()
    con.close()

# เรียก init_db ตอน import เพื่อให้ตารางถูกสร้างบน Render/Gunicorn ด้วย
try:
    init_db()
except Exception as e:
    print(f"[WARN] init_db() at import: {e}", flush=True)

# ---------------------- Health check (สำหรับ Render/Load balancer) ----------------------
@app.route("/healthz")
def healthz():
    try:
        con = get_db()
        con.execute("SELECT 1")
        con.close()
        return {"ok": True}, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

# ---------------------- Routes ----------------------
@app.route("/")
def home():
    # หน้า Landing (Home)
    event = dict(
        name=app.config["SITE_NAME"],
        date=app.config["EVENT_DATE"],
        location=app.config["EVENT_LOCATION"],
        reg_deadline=app.config["REG_DEADLINE"],
        reg_open=app.config["REG_OPEN"],
    )
    return render_template("home.html", event=event, title="Home")

@app.route("/register", methods=["GET","POST"])
def register():
    con = get_db()
    if request.method == "POST":
        # ก่อนอื่น: ตรวจสถานะเปิดรับสมัคร/เดดไลน์
        if not app.config["REG_OPEN"]:
            flash("ขออภัย ขณะนี้ปิดรับสมัครแล้ว")
            return redirect(url_for("register"))
        deadline = parse_date_yyyy_mm_dd(app.config["REG_DEADLINE"])
        if deadline and datetime.date.today() > deadline:
            flash("เลยกำหนดวันปิดรับสมัครแล้ว")
            return redirect(url_for("register"))

        first  = request.form["first_name"].strip()
        last   = request.form["last_name"].strip()
        school = request.form["school"].strip()
        cat_id = int(request.form["category_id"])
        comp_date = (request.form.get("competition_date") or "").strip()  # YYYY-MM-DD

        if not (first and last and school and comp_date):
            flash("กรอกข้อมูลให้ครบถ้วน (รวมถึงวันที่แข่งขัน)")
            return redirect(url_for("register"))

        # ตรวจรูปแบบวันที่ และไม่เกินวันงาน (ถ้าตั้งไว้)
        cd = parse_date_yyyy_mm_dd(comp_date)
        if not cd:
            flash("รูปแบบวันที่ไม่ถูกต้อง (ต้องเป็น YYYY-MM-DD)")
            return redirect(url_for("register"))
        event_date = parse_date_yyyy_mm_dd(app.config["EVENT_DATE"])
        if event_date and cd > event_date:
            flash("วันที่แข่งขันต้องไม่เกินวันจัดงาน")
            return redirect(url_for("register"))

        # upsert school
        row = con.execute("SELECT id FROM school WHERE name=?", (school,)).fetchone()
        if row:
            school_id = row["id"]
        else:
            con.execute("INSERT INTO school(name) VALUES(?)", (school,))
            school_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        # insert participant (กันลงทะเบียนซ้ำด้วย unique index)
        try:
            con.execute(
                """INSERT INTO participant(first_name,last_name,school_id,category_id,competition_date)
                   VALUES(?,?,?,?,?)""",
                (first, last, school_id, cat_id, comp_date)
            )
            con.commit()
            flash("ลงทะเบียนสำเร็จ")
        except sqlite3.IntegrityError:
            flash("มีการลงทะเบียนซ้ำสำหรับคน/ประเภท/วันเดียวกันแล้ว")
        return redirect(url_for("register"))

    cats = con.execute("SELECT id,name FROM category ORDER BY id").fetchall()
    con.close()
    return render_template("register.html", categories=cats, today=today(), title="ลงทะเบียน")

@app.route("/dashboard")
def dashboard():
    con = get_db()
    # กรองช่วงวันที่จาก participant.competition_date
    date_from = (request.args.get("date_from") or "").strip()
    date_to   = (request.args.get("date_to") or "").strip()

    where, params = [], []
    if date_from:
        where.append("p.competition_date >= ?"); params.append(date_from)
    if date_to:
        where.append("p.competition_date <= ?"); params.append(date_to)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # KPI หลัก
    total_schools = con.execute(
        f"SELECT COUNT(DISTINCT p.school_id) c FROM participant p {where_sql}", params
    ).fetchone()["c"]

    total_participants = con.execute(
        f"SELECT COUNT(*) c FROM participant p {where_sql}", params
    ).fetchone()["c"]

    # ผู้เข้าร่วมแยกตามประเภท + จำนวนโรงเรียนต่อประเภท
    by_category = con.execute(
        f"""
        SELECT c.id, c.name,
               COUNT(p.id)                 AS participants,
               COUNT(DISTINCT p.school_id) AS schools
        FROM category c
        LEFT JOIN participant p ON p.category_id = c.id
        {'WHERE ' + ' AND '.join(where) if where else ''}
        GROUP BY c.id, c.name
        ORDER BY c.id
        """,
        params
    ).fetchall()

    # -------- ใช้ event_date จาก result สำหรับช่วงผลการแข่งขัน --------
    res_where, res_params = [], []
    if date_from:
        res_where.append("r.event_date >= ?"); res_params.append(date_from)
    if date_to:
        res_where.append("r.event_date <= ?"); res_params.append(date_to)
    res_where_sql = ("WHERE " + " AND ".join(res_where)) if res_where else ""

    results_count = con.execute(
        f"SELECT COUNT(*) c FROM result r {res_where_sql}", res_params
    ).fetchone()["c"]

    completed_participants = con.execute(
        f"""
        SELECT COUNT(DISTINCT p.id) c
        FROM participant p
        JOIN result r ON r.participant_id = p.id
        {where_sql if where_sql else ''}
        """,
        params
    ).fetchone()["c"]
    completion_pct = round((completed_participants * 100.0 / total_participants), 1) if total_participants else 0.0

    top_schools = con.execute(
        f"""
        SELECT s.name AS school,
               SUM(CASE WHEN r.rank=1 THEN 1 ELSE 0 END) AS gold,
               SUM(CASE WHEN r.rank=2 THEN 1 ELSE 0 END) AS silver,
               SUM(CASE WHEN r.rank=3 THEN 1 ELSE 0 END) AS bronze,
               COUNT(r.id) AS total
        FROM result r
        JOIN participant p ON p.id = r.participant_id
        JOIN school s      ON s.id = p.school_id
        {res_where_sql}
        GROUP BY s.id
        ORDER BY gold DESC, silver DESC, bronze DESC, total DESC, school ASC
        LIMIT 5
        """,
        res_params
    ).fetchall()

    # รายชื่อที่ยังไม่มีผล (งานค้าง)
    pending_sql = """
        SELECT p.first_name || ' ' || p.last_name AS fullname,
               s.name AS school,
               c.name AS category
        FROM participant p
        JOIN school s ON s.id = p.school_id
        JOIN category c ON c.id = p.category_id
        LEFT JOIN result r ON r.participant_id = p.id
    """
    pending_params = list(params)
    if where:
        pending_sql += "WHERE " + " AND ".join(where) + " AND r.id IS NULL"
    else:
        pending_sql += "WHERE r.id IS NULL"
    pending_sql += " ORDER BY p.id DESC LIMIT 10"
    pending_list = con.execute(pending_sql, pending_params).fetchall()

    pending_count_sql = "SELECT COUNT(*) c FROM participant p LEFT JOIN result r ON r.participant_id = p.id "
    pending_count_params = list(params)
    if where:
        pending_count_sql += "WHERE " + " AND ".join(where) + " AND r.id IS NULL"
    else:
        pending_count_sql += "WHERE r.id IS NULL"
    pending_count = con.execute(pending_count_sql, pending_count_params).fetchone()["c"]

    # ข้อมูลสำหรับกราฟ
    cat_labels    = [row["name"]         for row in by_category]
    cat_counts    = [row["participants"] for row in by_category]
    school_counts = [row["schools"]      for row in by_category]

    con.close()
    return render_template(
        "dashboard.html",
        total_schools=total_schools,
        total_participants=total_participants,
        by_category=by_category,
        date_from=date_from, date_to=date_to,
        results_count=results_count,
        completion_pct=completion_pct,
        top_schools=top_schools,
        pending_list=pending_list,
        pending_count=pending_count,
        cat_labels=cat_labels, cat_counts=cat_counts, school_counts=school_counts,
        title="Dashboard"
    )

@app.route("/results", methods=["GET","POST"])
def results():
    con = get_db()

    # ---------------- POST: บันทึกผล ----------------
    if request.method == "POST":
        pid   = int(request.form["participant_id"])
        rank  = int(request.form["rank"])
        # แปลงคะแนน -> float หรือ None
        score_raw = (request.form.get("score") or "").strip()
        try:
            score = float(score_raw) if score_raw != "" else None
        except ValueError:
            score = None
        note  = request.form.get("note", "")
        event_date = (request.form.get("event_date") or today()).strip()

        con.execute(
            "INSERT INTO result(participant_id,rank,score,note,event_date) VALUES(?,?,?,?,?)",
            (pid, rank, score, note, event_date)
        )
        con.commit()
        flash("บันทึกผลสำเร็จ")

        # คงค่าตัวกรองเดิมไว้หลังบันทึกเสร็จ
        return redirect(url_for(
            "results",
            comp_date=request.args.get("comp_date", ""),
            category_id=request.args.get("category_id", ""),
            q=request.args.get("q", "")
        ))

    # ---------------- GET: โหลดรายการผู้เข้าแข่งขันพร้อมตัวกรอง ----------------
    comp_date  = (request.args.get("comp_date") or today()).strip()   # default = วันนี้
    cat_id     = request.args.get("category_id", type=int)
    q          = (request.args.get("q") or "").strip()

    clauses, params = [], []
    if comp_date:
        clauses.append("p.competition_date = ?"); params.append(comp_date)
    if cat_id:
        clauses.append("c.id = ?"); params.append(cat_id)
    if q:
        clauses.append("(p.first_name || ' ' || p.last_name LIKE ? OR s.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    sql = """
        SELECT p.id,
               p.first_name || ' ' || p.last_name AS fullname,
               s.name AS school,
               c.name AS category
        FROM participant p
        JOIN school s ON s.id = p.school_id
        JOIN category c ON c.id = p.category_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY p.id DESC"

    rows = con.execute(sql, params).fetchall()
    cats = con.execute("SELECT id,name FROM category ORDER BY id").fetchall()
    con.close()

    # ให้ค่า default ของ event_date เท่ากับวันที่ที่กรอง
    event_default = comp_date or today()

    return render_template(
        "results.html",
        participants=rows,
        categories=cats,
        selected_cat=cat_id,
        q=q,
        comp_date=comp_date,
        event_default=event_default,
        title="บันทึกผล"
    )

@app.route("/leaderboard")
def leaderboard():
    con = get_db()
    # ตัวกรอง
    cat_id    = request.args.get("category_id", type=int)
    school_q  = (request.args.get("school") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to   = (request.args.get("date_to") or "").strip()

    cats = con.execute("SELECT id, name FROM category ORDER BY id").fetchall()

    sql = """
      SELECT r.id, r.rank, r.score, r.note, r.event_date,
             p.first_name || ' ' || p.last_name AS fullname,
             s.name AS school,
             c.id AS category_id, c.name AS category
      FROM result r
      JOIN participant p ON p.id = r.participant_id
      JOIN school s      ON s.id = p.school_id
      JOIN category c    ON c.id = p.category_id
    """
    clauses, params = [], []
    if cat_id:
        clauses.append("c.id = ?"); params.append(cat_id)
    if school_q:
        clauses.append("s.name LIKE ?"); params.append(f"%{school_q}%")
    if date_from:
        clauses.append("r.event_date >= ?"); params.append(date_from)
    if date_to:
        clauses.append("r.event_date <= ?"); params.append(date_to)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " ORDER BY c.id ASC, r.rank ASC, r.score DESC, r.id DESC"

    rows = con.execute(sql, params).fetchall()
    con.close()
    return render_template(
        "leaderboard.html",
        categories=cats,
        selected_cat=cat_id,
        school_q=school_q,
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        title="ผลการแข่งขัน"
    )

# ---------------------- Auto-open Chrome (dev only) ----------------------
def _open_in_chrome():
    url = "http://127.0.0.1:5000/"
    for name in ("chrome", "google-chrome", "chrome.exe"):
        try:
            webbrowser.get(name).open_new(url); return
        except:
            pass
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            webbrowser.register("chrome-fixed", None, webbrowser.BackgroundBrowser(p))
            webbrowser.get("chrome-fixed").open_new(url); return
    webbrowser.open_new(url)

# ---------------------- Main ----------------------
if __name__ == "__main__":
    # init_db()  # เรียกตอน import แล้ว ด้านบน; จะคงไว้ก็ไม่เป็นไร
    if OPEN_BROWSER and os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Timer(1.0, _open_in_chrome).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=DEBUG_MODE)