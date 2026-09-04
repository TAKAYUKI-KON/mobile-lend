import os
import secrets
import sqlite3
import hashlib
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    local_env = {}
    local_env_path = Path(app.instance_path) / "local.env"
    if local_env_path.exists():
        for line in local_env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                local_env[key.strip()] = value.strip().strip('"').strip("'")

    def setting(name, default=""):
        return os.environ.get(name, local_env.get(name, default))

    app.config.from_mapping(
        SECRET_KEY=setting("MOBILEND_SECRET_KEY", "dev-only-change-me"),
        DATABASE=str(Path(app.instance_path) / "mobilend.sqlite3"),
        BASE_URL=setting("MOBILEND_BASE_URL", "http://127.0.0.1:5000").rstrip("/"),
        MAIL_MODE=setting("MOBILEND_MAIL_MODE", "file").lower(),
        MAIL_FROM=setting("MOBILEND_MAIL_FROM", "mobilend@localhost"),
        SMTP_HOST=setting("MOBILEND_SMTP_HOST"),
        SMTP_PORT=int(setting("MOBILEND_SMTP_PORT", "587")),
        SMTP_USERNAME=setting("MOBILEND_SMTP_USERNAME"),
        SMTP_PASSWORD=setting("MOBILEND_SMTP_PASSWORD"),
        SMTP_USE_TLS=setting("MOBILEND_SMTP_USE_TLS", "1").lower() in ("1", "true", "yes"),
        TEST_RECIPIENT=setting("MOBILEND_TEST_RECIPIENT"),
        ACTION_TOKEN_HOURS=int(setting("MOBILEND_ACTION_TOKEN_HOURS", "72")),
        OUTBOX_PATH=str(Path(app.instance_path) / "outbox"),
        DEMO_SCALE=setting("MOBILEND_DEMO_SCALE", "1").lower() in ("1", "true", "yes"),
    )
    if test_config:
        app.config.update(test_config)

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
                "INSERT INTO users(user_id,name,password_hash,department,email,role,retired,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, name, generate_password_hash(password), dept, "", role, retired, now),
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

    def migrate_db():
        """Apply small, idempotent migrations to databases created by older versions."""
        db = get_db()
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "email" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS loan_action_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loan_id INTEGER NOT NULL REFERENCES loans(id),
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS loan_action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loan_id INTEGER NOT NULL REFERENCES loans(id),
                action TEXT NOT NULL CHECK(action IN ('notified','extended','returned','lost')),
                old_due_date TEXT,
                new_due_date TEXT,
                acted_at TEXT NOT NULL
            );
        """)
        if app.config.get("TEST_RECIPIENT"):
            db.execute("UPDATE users SET email=? WHERE role='user' AND retired=0 AND email=''", (app.config["TEST_RECIPIENT"],))
        db.commit()

    def ensure_demo_scale():
        """Fill a development database to 120 users and 80 devices without duplicates."""
        db = get_db()
        now = datetime.now().isoformat(timespec="seconds")
        departments = ("営業部", "総務部", "開発部", "経理部", "人事部", "企画部", "カスタマーサポート部", "品質管理部")
        demo_password_hash = generate_password_hash("User123!")
        user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        demo_number = 1
        while user_count < 120:
            user_id = f"demo{demo_number:03d}"
            if not db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone():
                db.execute("""INSERT INTO users(user_id,name,password_hash,department,email,role,retired,created_at)
                    VALUES(?,?,?,?,?,'user',?,?)""",
                    (user_id, f"テスト 利用者{demo_number:03d}", demo_password_hash,
                     departments[(demo_number - 1) % len(departments)], app.config.get("TEST_RECIPIENT", ""),
                     1 if demo_number % 25 == 0 else 0, now))
                user_count += 1
            demo_number += 1

        plan_ids = [row["id"] for row in db.execute("SELECT id FROM contract_plans ORDER BY id")]
        device_count = db.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        demo_number = 1
        while device_count < 80:
            dtype = "USB" if demo_number % 2 else "WiFi"
            device_number = f"{dtype.upper()}-T{demo_number:03d}"
            if not db.execute("SELECT 1 FROM devices WHERE device_number=?", (device_number,)).fetchone():
                db.execute("""INSERT INTO devices(device_number,device_type,phone_number,plan_id,active,created_at)
                    VALUES(?,?,?,?,1,?)""",
                    (device_number, dtype, f"070-5000-{demo_number:04d}",
                     plan_ids[(demo_number - 1) % len(plan_ids)], now))
                device_count += 1
            demo_number += 1

        admin_id = db.execute("SELECT id FROM users WHERE role='admin' AND retired=0 ORDER BY id LIMIT 1").fetchone()[0]
        borrowers = db.execute("SELECT id FROM users WHERE user_id LIKE 'demo%' AND retired=0 ORDER BY user_id LIMIT 55").fetchall()
        devices = db.execute("SELECT id FROM devices WHERE device_number LIKE '%-T%' ORDER BY device_number LIMIT 55").fetchall()
        today = date.today()
        for index, (borrower, device_row) in enumerate(zip(borrowers, devices)):
            if db.execute("SELECT 1 FROM loans WHERE device_id=?", (device_row["id"],)).fetchone():
                continue
            checkout = today - timedelta(days=(index % 18) + 2)
            if index < 40:
                due = today - timedelta(days=(index % 10) + 1) if index < 14 else today + timedelta(days=(index % 20) + 2)
                status, returned_at = "borrowed", None
                note = "大規模動作確認用（貸出中）"
            else:
                due = today - timedelta(days=2)
                status, returned_at = "returned", str(today - timedelta(days=1))
                note = "大規模動作確認用（返却済み）"
            db.execute("""INSERT INTO loans(device_id,borrower_id,lent_by_id,checkout_date,due_date,returned_at,status,note,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (device_row["id"], borrower["id"], admin_id, str(checkout), str(due), returned_at, status, note, now))
        db.commit()

    with app.app_context():
        if not Path(app.config["DATABASE"]).exists():
            init_db()
        migrate_db()
        if app.config["DEMO_SCALE"]:
            ensure_demo_scale()

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

    def write_or_send_email(message, loan_id):
        if app.config["MAIL_MODE"] == "smtp":
            if not app.config["SMTP_HOST"]:
                raise RuntimeError("MOBILEND_SMTP_HOSTが設定されていません。")
            with smtplib.SMTP(app.config["SMTP_HOST"], app.config["SMTP_PORT"], timeout=20) as smtp:
                smtp.ehlo()
                if app.config["SMTP_USE_TLS"]:
                    smtp.starttls()
                    smtp.ehlo()
                if app.config["SMTP_USERNAME"]:
                    smtp.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
                smtp.send_message(message)
            return "smtp"
        outbox = Path(app.config["OUTBOX_PATH"])
        outbox.mkdir(parents=True, exist_ok=True)
        filename = outbox / f"loan-{loan_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.eml"
        filename.write_bytes(message.as_bytes())
        return str(filename)

    def create_action_email(loan):
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now()
        expires = now + timedelta(hours=app.config["ACTION_TOKEN_HOURS"])
        action_url = f"{app.config['BASE_URL']}/loan-actions/{raw_token}"
        message = EmailMessage()
        message["Subject"] = f"【MobiLend】{loan['device_number']} の利用状況をご確認ください"
        message["From"] = app.config["MAIL_FROM"]
        message["To"] = loan["email"]
        message.set_content(f"""{loan['borrower']} 様

現在貸出中のモバイル端末について、利用状況をご確認ください。

端末番号: {loan['device_number']}
端末タイプ: {loan['device_type']}
返却期限: {loan['due_date']}

以下のURLから、返却・紛失・期限延長を登録できます。
{action_url}

このURLの有効期限は {expires.strftime('%Y-%m-%d %H:%M')} です。
心当たりがない場合は情報企画課へご連絡ください。
""")
        db = get_db()
        cursor = db.execute(
            "INSERT INTO loan_action_tokens(loan_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)",
            (loan["id"], token_hash, expires.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
        db.execute("INSERT INTO loan_action_log(loan_id,action,old_due_date,new_due_date,acted_at) VALUES(?,'notified',?,?,?)",
                   (loan["id"], loan["due_date"], loan["due_date"], now.isoformat(timespec="seconds")))
        db.commit()
        try:
            delivery = write_or_send_email(message, loan["id"])
        except Exception:
            db.execute("DELETE FROM loan_action_tokens WHERE id=?", (cursor.lastrowid,))
            db.commit()
            raise
        return delivery

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

    @app.post("/loans/<int:loan_id>/notify")
    @admin_required
    def notify_borrower(loan_id):
        loan = get_db().execute("""SELECT l.*,d.device_number,d.device_type,u.name borrower,u.email
            FROM loans l JOIN devices d ON d.id=l.device_id JOIN users u ON u.id=l.borrower_id
            WHERE l.id=? AND l.status='borrowed'""", (loan_id,)).fetchone()
        if not loan:
            flash("貸出中の記録が見つかりません。", "error")
        elif not loan["email"]:
            flash("利用者のメールアドレスが登録されていません。", "error")
        else:
            try:
                delivery = create_action_email(loan)
                if delivery == "smtp":
                    flash(f"{loan['borrower']}さんへ確認メールを送信しました。", "success")
                else:
                    flash("検証用メールをinstance/outboxに出力しました。", "warning")
            except (OSError, RuntimeError, smtplib.SMTPException) as exc:
                app.logger.exception("Loan notification failed")
                flash(f"メールを送信できませんでした: {exc}", "error")
        return redirect(url_for("loans"))

    def get_action_loan(raw_token):
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        row = get_db().execute("""SELECT l.*,t.id token_id,t.expires_at,d.device_number,d.device_type,
            u.name borrower,u.department FROM loan_action_tokens t
            JOIN loans l ON l.id=t.loan_id JOIN devices d ON d.id=l.device_id JOIN users u ON u.id=l.borrower_id
            WHERE t.token_hash=?""", (token_hash,)).fetchone()
        if not row:
            abort(404)
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            abort(410)
        return row

    @app.route("/loan-actions/<token>", methods=["GET", "POST"])
    def loan_action(token):
        loan = get_action_loan(token)
        if request.method == "POST":
            if loan["status"] != "borrowed":
                flash("この貸出はすでに処理済みです。", "warning")
                return redirect(url_for("loan_action", token=token))
            action = request.form.get("action")
            db, now = get_db(), datetime.now().isoformat(timespec="seconds")
            if action == "extend":
                try:
                    new_due = date.fromisoformat(request.form.get("due_date", ""))
                    old_due = date.fromisoformat(loan["due_date"])
                except ValueError:
                    flash("新しい返却期限を入力してください。", "error")
                    return redirect(url_for("loan_action", token=token))
                if new_due <= old_due or new_due < date.today():
                    flash("現在の返却期限より後の日付を指定してください。", "error")
                    return redirect(url_for("loan_action", token=token))
                db.execute("UPDATE loans SET due_date=? WHERE id=? AND status='borrowed'", (str(new_due), loan["id"]))
                db.execute("INSERT INTO loan_action_log(loan_id,action,old_due_date,new_due_date,acted_at) VALUES(?,'extended',?,?,?)",
                           (loan["id"], loan["due_date"], str(new_due), now))
                flash("返却期限を延長しました。", "success")
            elif action in ("returned", "lost"):
                db.execute("UPDATE loans SET status=?,returned_at=? WHERE id=? AND status='borrowed'",
                           (action, date.today().isoformat() if action == "returned" else None, loan["id"]))
                if action == "lost":
                    db.execute("UPDATE devices SET active=0 WHERE id=?", (loan["device_id"],))
                db.execute("INSERT INTO loan_action_log(loan_id,action,old_due_date,new_due_date,acted_at) VALUES(?,?,?,?,?)",
                           (loan["id"], action, loan["due_date"], loan["due_date"], now))
                flash("返却を登録しました。" if action == "returned" else "紛失を登録しました。情報企画課へもご連絡ください。",
                      "success" if action == "returned" else "warning")
            else:
                abort(400)
            db.execute("UPDATE loan_action_tokens SET last_used_at=? WHERE id=?", (now, loan["token_id"]))
            db.commit()
            return redirect(url_for("loan_action", token=token))
        return render_template("loan_action.html", loan=loan, token=token)

    @app.route("/users", methods=["GET", "POST"])
    @admin_required
    def users():
        db = get_db()
        if request.method == "POST":
            uid, name, password, dept, email, role = (request.form.get(k, "").strip() for k in ("user_id", "name", "password", "department", "email", "role"))
            if not all((uid, name, password, dept, email, role)) or len(password) < 8 or role not in ("admin", "user") or "@" not in email:
                flash("入力内容を確認してください（パスワードは8文字以上）。", "error")
            else:
                try:
                    db.execute("INSERT INTO users(user_id,name,password_hash,department,email,role,retired,created_at) VALUES(?,?,?,?,?,?,0,?)", (uid, name, generate_password_hash(password), dept, email, role, datetime.now().isoformat(timespec="seconds")))
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
    @app.errorhandler(410)
    def error_page(error):
        messages = {400: "不正なリクエストです。", 403: "この操作を行う権限がありません。", 404: "ページが見つかりません。", 410: "このURLの有効期限は終了しました。"}
        return render_template("error.html", code=error.code, message=messages[error.code]), error.code

    app.get_db = get_db
    app.init_db = init_db
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
