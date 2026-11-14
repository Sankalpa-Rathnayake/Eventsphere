import re
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db
from app import models
from app.security import hash_password


def make_client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def add_user(db, email, role, pw="secret1"):
    db.add(models.User(email=email, password_hash=hash_password(pw), role=role))
    db.commit()


def login(client, email, pw="secret1"):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client.post("/login", data={"email": email, "password": pw, "csrf_token": token},
                       follow_redirects=False)


def test_password_is_hashed(db):
    add_user(db, "h@x.com", "attendee", "plain-password")
    u = db.query(models.User).filter_by(email="h@x.com").first()
    assert u.password_hash != "plain-password" and u.password_hash.startswith("$2")


def test_admin_page_blocks_attendee(db):
    add_user(db, "att@x.com", "attendee")
    c = make_client(db)
    login(c, "att@x.com")
    assert c.get("/admin/dashboard", follow_redirects=False).status_code == 403
    app.dependency_overrides.clear()


def test_scanner_blocks_attendee(db):
    add_user(db, "att2@x.com", "attendee")
    c = make_client(db)
    login(c, "att2@x.com")
    assert c.get("/staff/scanner", follow_redirects=False).status_code == 403
    app.dependency_overrides.clear()


def test_admin_can_open_dashboard(db):
    add_user(db, "adm@x.com", "admin")
    c = make_client(db)
    login(c, "adm@x.com")
    assert c.get("/admin/dashboard").status_code == 200
    app.dependency_overrides.clear()


def test_anonymous_redirected_to_login(db):
    c = make_client(db)
    r = c.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    app.dependency_overrides.clear()


def test_post_without_csrf_token_is_blocked(db):
    add_user(db, "csrf@x.com", "attendee")
    c = make_client(db)
    r = c.post("/login", data={"email": "csrf@x.com", "password": "secret1"}, follow_redirects=False)
    assert r.status_code == 403
    app.dependency_overrides.clear()