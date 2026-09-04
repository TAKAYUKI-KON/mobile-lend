import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("MOBILEND_SECRET_KEY", "dev-only-change-me"),
        DATABASE=str(Path(app.instance_path) / "mobilend.sqlite3"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        db = get_db()
        with open(BASE_DIR / "schema.sql", encoding="utf-8") as f:
            db.executescript(f.read())
        now = datetime.now().isoformat(timespec="seconds")
        users = [
            ("admin01", "情報企画 管理者", "Admin123!", "情報企画課", "admin", 0),
            ("user01", "山田 太郎", "User123!", "営業部", "user", 0),
            ("user02", "佐藤 花子", "User123!", "総務部", "user", 0),
            ("user03", "鈴木 一郎", "User123!", "開発部", "user", 0),
            ("retired01", "退職者 テスト", "User123!", "旧所属", "user", 1),
        ]
        for user_id, name, password, dept, role, retired in users:
            db.execute(
                "INSERT INTO users(user_id,name,password_hash,department,role,retired,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, name, generate_password_hash(password), dept, role, retired, now),
            )
        plans = [("5GB/月", "少量利用向け"), ("10GB/月", "標準USB向け"),
                 ("無制限", "大容量利用向け"), ("50GB/月", "標準WiFi向け")]
        for name, desc in plans:
            db.execute("INSERT INTO contract_plans(name,description,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                       (name, desc, now, now))
        devices = [
            ("USB-001", "USB", "090-1000-0001", "5GB/月"),
            ("USB-002", "USB", "090-1000-0002", "10GB/月"),
            ("WIFI-001", "WiFi", "090-2000-0001", "無制限"),
            ("WIFI-002", "WiFi", "090-2000-0002", "50GB/月"),
            ("WIFI-003", "WiFi", "090-2000-0003", "無制限"),
        ]
        for number, dtype, phone, plan in devices:
            db.execute("INSERT INTO devices(device_number,device_type,phone_number,plan_id,active,created_at) SELECT ?,?,?,id,1,? FROM contract_plans WHERE name=?",
                       (number, dtype, phone, now, plan))
        today = date.today()
        admin = db.execute("SELECT id FROM users WHERE user_id='admin01'").fetchone()[0]
        user1 = db.execute("SELECT id FROM users WHERE user_id='user01'").fetchone()[0]
        user2 = db.execute("SELECT id FROM users WHERE user_id='user02'").fetchone()[0]
        user3 = db.execute("SELECT id FROM users WHERE user_id='user03'").fetchone()[0]
        dev = {r["device_number"]: r["id"] for r in db.execute("SELECT id,device_number FROM devices")}
        rows = [
            (dev["USB-001"], user1, admin, today - timedelta(days=2), today + timedelta(days=12), None, "borrowed", "営業訪問用"),
            (dev["WIFI-001"], user2, admin, today - timedelta(days=14), today - timedelta(days=2), None, "borrowed", "出張用"),
            (dev["USB-002"], user3, admin, today - timedelta(days=20), today - timedelta(days=5), today - timedelta(days=6), "returned", "返却済み"),
        ]
        for row in rows:
            db.execute("INSERT INTO loans(device_id,borrower_id,lent_by_id,checkout_date,due_date,returned_at,status,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       tuple(str(x) if isinstance(x, date) else x for x in row) + (now,))
        db.commit()

    with app.app_context():
        if not Path(app.config["DATABASE"]).exists():
            init_db()

    def login_required(view):
        @wraps(view)
        def wrapped(**kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            return view(**kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(**kwargs):
            if g.user["role"] != "admin":
                abort(403)
            return view(**kwargs)
        return wrapped

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        if session.get("user_id"):
            g.user = get_db().execute("SELECT * FROM users WHERE id=? AND retired=0", (session["user_id"],)).fetchone()
            if g.user is None:
                session.clear()
        if request.method == "POST":
            token = session.get("csrf_token")
            if not token or not secrets.compare_digest(token, request.form.get("csrf_token", "")):
                abort(400, "CSRFトークンが不正です。")

    @app.context_processor
    def template_helpers():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(24)
        return {"csrf_token": session["csrf_token"], "today": date.today().isoformat()}

    @app.route("/")
    def index():
        return redirect(url_for("dashboard" if g.user else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = get_db().execute("SELECT * FROM users WHERE user_id=?", (request.form.get("user_id", "").strip(),)).fetchone()
            if not user or user["retired"] or not check_password_hash(user["password_hash"], request.form.get("password", "")):
                flash("ユーザーIDまたはパスワードが正しくありません。", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_hex(24)
                return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    def loan_query(where="", params=()):
        return get_db().execute(f"""SELECT l.*,d.device_number,d.device_type,u.name borrower,u.department,
            CASE WHEN l.status='borrowed' AND l.due_date < date('now','localtime') THEN 1 ELSE 0 END overdue
            FROM loans l JOIN devices d ON d.id=l.device_id JOIN users u ON u.id=l.borrower_id {where}
            ORDER BY CASE WHEN l.status='borrowed' THEN 0 ELSE 1 END,l.due_date""", params).fetchall()

    @app.get("/dashboard")
    @login_required
    def dashboard():
        if g.user["role"] == "admin":
            counts = get_db().execute("""SELECT COUNT(*) total,
                SUM(CASE WHEN active=1 AND NOT EXISTS(SELECT 1 FROM loans WHERE device_id=devices.id AND status='borrowed') THEN 1 ELSE 0 END) available,
                SUM(CASE WHEN EXISTS(SELECT 1 FROM loans WHERE device_id=devices.id AND status='borrowed') THEN 1 ELSE 0 END) borrowed FROM devices""").fetchone()
            overdue = get_db().execute("SELECT COUNT(*) FROM loans WHERE status='borrowed' AND due_date < date('now','localtime')").fetchone()[0]
            return render_template("dashboard.html", counts=counts, overdue=overdue, loans=loan_query("WHERE l.status='borrowed'"))
        return render_template("dashboard.html", loans=loan_query("WHERE l.borrower_id=? AND l.status='borrowed'", (g.user["id"],)))

    @app.route("/devices", methods=["GET", "POST"])
    @login_required
    def devices():
        db = get_db()
        if request.method == "POST":
            if g.user["role"] != "admin": abort(403)
            number, dtype, phone, plan_id = (request.form.get(k, "").strip() for k in ("device_number", "device_type", "phone_number", "plan_id"))
            plan = db.execute("SELECT id FROM contract_plans WHERE id=? AND active=1", (plan_id,)).fetchone()
            if not all((number, dtype, phone, plan_id)) or dtype not in ("USB", "WiFi") or not plan:
                flash("入力内容を確認してください。", "error")
            else:
                try:
                    db.execute("INSERT INTO devices(device_number,device_type,phone_number,plan_id,active,created_at) VALUES(?,?,?,?,1,?)",
                               (number, dtype, phone, plan_id, datetime.now().isoformat(timespec="seconds")))
                    db.commit(); flash("端末を登録しました。", "success")
                except sqlite3.IntegrityError: flash("端末番号はすでに登録されています。", "error")
        rows = db.execute("""SELECT d.*,p.name plan_name,p.active plan_active,u.name borrower,l.due_date
            FROM devices d JOIN contract_plans p ON p.id=d.plan_id
            LEFT JOIN loans l ON l.device_id=d.id AND l.status='borrowed' LEFT JOIN users u ON u.id=l.borrower_id
            ORDER BY d.device_number""").fetchall()
        plans = db.execute("SELECT * FROM contract_plans WHERE active=1 ORDER BY name").fetchall()
        return render_template("devices.html", devices=rows, plans=plans)

    @app.route("/plans", methods=["GET", "POST"])
    @admin_required
    def plans():
        db = get_db()
        if request.method == "POST":
            name, desc = request.form.get("name", "").strip(), request.form.get("description", "").strip()
            if not name: flash("プラン名は必須です。", "error")
            else:
                try:
                    now = datetime.now().isoformat(timespec="seconds")
                    db.execute("INSERT INTO contract_plans(name,description,active,created_at,updated_at) VALUES(?,?,1,?,?)", (name, desc, now, now))
                    db.commit(); flash("契約プランを登録しました。", "success")
                except sqlite3.IntegrityError: flash("同名のプランが存在します。", "error")
        rows = db.execute("SELECT p.*,COUNT(d.id) device_count FROM contract_plans p LEFT JOIN devices d ON d.plan_id=p.id GROUP BY p.id ORDER BY p.name").fetchall()
        return render_template("plans.html", plans=rows)

    @app.post("/plans/<int:plan_id>/edit")
    @admin_required
    def edit_plan(plan_id):
        name, desc = request.form.get("name", "").strip(), request.form.get("description", "").strip()
        if not name: flash("プラン名は必須です。", "error")
        else:
            try:
                cur = get_db().execute("UPDATE contract_plans SET name=?,description=?,updated_at=? WHERE id=?", (name, desc, datetime.now().isoformat(timespec="seconds"), plan_id))
                get_db().commit(); flash("契約プランを更新しました。" if cur.rowcount else "対象が見つかりません。", "success" if cur.rowcount else "error")
            except sqlite3.IntegrityError: flash("同名のプランが存在します。", "error")
        return redirect(url_for("plans"))

    @app.post("/plans/<int:plan_id>/toggle-active")
    @admin_required
    def toggle_plan(plan_id):
        get_db().execute("UPDATE contract_plans SET active=1-active,updated_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), plan_id))
        get_db().commit(); flash("契約プランの状態を変更しました。", "success")
        return redirect(url_for("plans"))

    @app.route("/loans", methods=["GET", "POST"])
    @login_required
    def loans():
        db = get_db()
        if request.method == "POST":
            if g.user["role"] != "admin": abort(403)
            try:
                device_id, borrower_id = int(request.form.get("device_id", 0)), int(request.form.get("borrower_id", 0))
                checkout, due = date.fromisoformat(request.form.get("checkout_date", "")), date.fromisoformat(request.form.get("due_date", ""))
            except (ValueError, TypeError):
                flash("貸出情報をすべて入力してください。", "error"); return redirect(url_for("loans"))
            device = db.execute("SELECT * FROM devices WHERE id=? AND active=1 AND NOT EXISTS(SELECT 1 FROM loans WHERE device_id=? AND status='borrowed')", (device_id, device_id)).fetchone()
            borrower = db.execute("SELECT * FROM users WHERE id=? AND retired=0 AND role='user'", (borrower_id,)).fetchone()
            if not device or not borrower or due < checkout:
                flash("端末・利用者・日付を確認してください。", "error")
            else:
                try:
                    db.execute("INSERT INTO loans(device_id,borrower_id,lent_by_id,checkout_date,due_date,status,note,created_at) VALUES(?,?,?,?,?,'borrowed',?,?)",
                               (device_id, borrower_id, g.user["id"], str(checkout), str(due), request.form.get("note", "").strip(), datetime.now().isoformat(timespec="seconds")))
                    db.commit(); flash("貸出を登録しました。", "success")
                except sqlite3.IntegrityError: flash("この端末はすでに貸出中です。", "error")
        where, params = ("", ()) if g.user["role"] == "admin" else ("WHERE l.borrower_id=?", (g.user["id"],))
        available = db.execute("SELECT * FROM devices d WHERE active=1 AND NOT EXISTS(SELECT 1 FROM loans WHERE device_id=d.id AND status='borrowed') ORDER BY device_number").fetchall()
        borrowers = db.execute("SELECT * FROM users WHERE retired=0 AND role='user' ORDER BY name").fetchall()
        return render_template("loans.html", loans=loan_query(where, params), devices=available, borrowers=borrowers)

    def finish_loan(loan_id, status):
        db = get_db()
        loan = db.execute("SELECT * FROM loans WHERE id=? AND status='borrowed'", (loan_id,)).fetchone()
        if not loan: flash("貸出中の記録が見つかりません。", "error")
        else:
            db.execute("UPDATE loans SET status=?,returned_at=? WHERE id=?", (status, date.today().isoformat() if status == "returned" else None, loan_id))
            if status == "lost": db.execute("UPDATE devices SET active=0 WHERE id=?", (loan["device_id"],))
            db.commit(); flash("返却を登録しました。" if status == "returned" else "紛失を登録し、端末を利用停止にしました。", "success" if status == "returned" else "warning")
        return redirect(url_for("loans"))

    @app.post("/loans/<int:loan_id>/return")
    @admin_required
    def return_loan(loan_id): return finish_loan(loan_id, "returned")

    @app.post("/loans/<int:loan_id>/lost")
    @admin_required
    def lose_loan(loan_id): return finish_loan(loan_id, "lost")

    @app.route("/users", methods=["GET", "POST"])
    @admin_required
    def users():
        db = get_db()
        if request.method == "POST":
            uid, name, password, dept, role = (request.form.get(k, "").strip() for k in ("user_id", "name", "password", "department", "role"))
            if not all((uid, name, password, dept, role)) or len(password) < 8 or role not in ("admin", "user"):
                flash("入力内容を確認してください（パスワードは8文字以上）。", "error")
            else:
                try:
                    db.execute("INSERT INTO users(user_id,name,password_hash,department,role,retired,created_at) VALUES(?,?,?,?,?,0,?)", (uid, name, generate_password_hash(password), dept, role, datetime.now().isoformat(timespec="seconds")))
                    db.commit(); flash("ユーザーを登録しました。", "success")
                except sqlite3.IntegrityError: flash("ユーザーIDはすでに登録されています。", "error")
        return render_template("users.html", users=db.execute("SELECT * FROM users ORDER BY user_id").fetchall())

    @app.post("/users/<int:user_id>/toggle-retired")
    @admin_required
    def toggle_user(user_id):
        if user_id == g.user["id"]: flash("自分自身を退職済みに変更できません。", "error")
        else:
            get_db().execute("UPDATE users SET retired=1-retired WHERE id=?", (user_id,)); get_db().commit()
            flash("在籍状態を変更しました。", "success")
        return redirect(url_for("users"))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def error_page(error):
        messages = {400: "不正なリクエストです。", 403: "この操作を行う権限がありません。", 404: "ページが見つかりません。"}
        return render_template("error.html", code=error.code, message=messages[error.code]), error.code

    app.get_db = get_db
    app.init_db = init_db
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
