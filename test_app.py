import re
import pytest
from app import create_app

@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "test.sqlite3")})

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
