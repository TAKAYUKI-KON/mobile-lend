import re
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
import pytest
from app import create_app

@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "test.sqlite3"),
                       "MAIL_MODE": "file", "OUTBOX_PATH": str(tmp_path / "outbox"),
                       "TEST_RECIPIENT": "verify@example.invalid", "BASE_URL": "http://localhost",
                       "DEMO_SCALE": False})

@pytest.fixture()
def client(app): return app.test_client()

def token(client, path="/login"):
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)

def login(client, uid="admin01", password="Admin123!"):
    return client.post("/login", data={"csrf_token": token(client), "user_id": uid, "password": password}, follow_redirects=True)

def test_admin_login_and_overdue(client):
    body = login(client).get_data(as_text=True)
    assert "ダッシュボード" in body and "期限超過" in body and "WIFI-001" in body
    assert 'data-device-type="USB"' in body and 'data-device-type="WiFi"' in body
    assert "端末タイプ別内訳" in body and "総台数" in body and "貸出可能" in body

def test_user_isolation_and_forbidden(client):
    login(client, "user01", "User123!")
    body = client.get("/loans").get_data(as_text=True)
    assert "USB-001" in body and "WIFI-001" not in body
    assert client.get("/users").status_code == 403

def test_retired_login_rejected(client):
    response = login(client, "retired01", "User123!")
    assert "正しくありません" in response.get_data(as_text=True)

def test_checkout_and_return(client, app):
    login(client)
    csrf = token(client, "/loans")
    response = client.post("/loans", data={"csrf_token": csrf, "device_id": 4, "borrower_id": 4, "checkout_date": "2026-09-01", "due_date": "2026-09-10", "note": "test"}, follow_redirects=True)
    assert "貸出を登録しました" in response.get_data(as_text=True)
    with app.app_context(): loan_id = app.get_db().execute("SELECT id FROM loans WHERE device_id=4 AND status='borrowed'").fetchone()[0]
    response = client.post(f"/loans/{loan_id}/return", data={"csrf_token": token(client, "/loans")}, follow_redirects=True)
    assert "返却を登録しました" in response.get_data(as_text=True)

def test_csrf_required(client):
    assert client.post("/login", data={"user_id": "admin01", "password": "Admin123!"}).status_code == 400

def test_notification_link_extend_and_return(client, app):
    login(client)
    response = client.post("/loans/1/notify", data={"csrf_token": token(client, "/loans")}, follow_redirects=True)
    assert "instance/outbox" in response.get_data(as_text=True)
    message_path = next((__import__("pathlib").Path(app.config["OUTBOX_PATH"])).glob("*.eml"))
    message = BytesParser(policy=policy.default).parsebytes(message_path.read_bytes())
    assert message["To"] == "verify@example.invalid"
    action_path = re.search(r"http://localhost(/loan-actions/[A-Za-z0-9_-]+)", message.get_content()).group(1)
    assert "端末利用状況の登録" in client.get(action_path).get_data(as_text=True)

    new_due = str(date.today() + timedelta(days=30))
    response = client.post(action_path, data={"csrf_token": token(client, action_path), "action": "extend", "due_date": new_due}, follow_redirects=True)
    assert "返却期限を延長しました" in response.get_data(as_text=True)
    with app.app_context():
        assert app.get_db().execute("SELECT due_date FROM loans WHERE id=1").fetchone()[0] == new_due

    response = client.post(action_path, data={"csrf_token": token(client, action_path), "action": "returned"}, follow_redirects=True)
    assert "返却を登録しました" in response.get_data(as_text=True)
    with app.app_context():
        assert app.get_db().execute("SELECT status FROM loans WHERE id=1").fetchone()[0] == "returned"

def test_notification_link_lost_stops_device(client, app):
    login(client)
    client.post("/loans/2/notify", data={"csrf_token": token(client, "/loans")})
    paths = sorted((__import__("pathlib").Path(app.config["OUTBOX_PATH"])).glob("*.eml"))
    message = BytesParser(policy=policy.default).parsebytes(paths[-1].read_bytes())
    action_path = re.search(r"http://localhost(/loan-actions/[A-Za-z0-9_-]+)", message.get_content()).group(1)
    client.post(action_path, data={"csrf_token": token(client, action_path), "action": "lost"})
    with app.app_context():
        db = app.get_db()
        assert db.execute("SELECT status FROM loans WHERE id=2").fetchone()[0] == "lost"
        assert db.execute("SELECT active FROM devices WHERE id=3").fetchone()[0] == 0

def test_demo_scale_seed_is_idempotent(tmp_path):
    database = str(tmp_path / "scale.sqlite3")
    scaled = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database,
                         "DEMO_SCALE": True, "TEST_RECIPIENT": "verify@example.invalid"})
    with scaled.app_context():
        db = scaled.get_db()
        assert db.execute("SELECT COUNT(id) FROM users").fetchone()[0] == 120
        assert db.execute("SELECT COUNT(id) FROM devices").fetchone()[0] == 80
        assert db.execute("SELECT COUNT(id) FROM loans WHERE status='borrowed'").fetchone()[0] == 42
        assert db.execute("SELECT name FROM users WHERE user_id='demo001'").fetchone()[0] == "織田 信長"
        assert db.execute("SELECT COUNT(DISTINCT name) FROM users WHERE user_id LIKE 'demo%'").fetchone()[0] == 115
        assert db.execute("SELECT COUNT(id) FROM users WHERE name LIKE 'テスト 利用者%'").fetchone()[0] == 0
    scaled_again = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database,
                               "DEMO_SCALE": True, "TEST_RECIPIENT": "verify@example.invalid"})
    with scaled_again.app_context():
        assert scaled_again.get_db().execute("SELECT COUNT(id) FROM users").fetchone()[0] == 120
        assert scaled_again.get_db().execute("SELECT COUNT(id) FROM devices").fetchone()[0] == 80
