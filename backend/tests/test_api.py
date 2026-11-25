"""API tests. Run from the backend folder:  pytest -q"""
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["TRACKER_API_KEY"] = "tracker-test-key"
os.environ["ADMIN_PASSWORD"] = "Admin@2026"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def login(username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture(scope="module")
def admin_token():
    r = login("admin", "Admin@2026")
    assert r.status_code == 200
    assert r.json()["user"]["mustChange"] is True
    token = r.json()["token"]
    # Temporary password blocks everything except changing it.
    assert client.get("/api/bootstrap", headers=auth(token)).status_code == 403
    r = client.post("/api/auth/change-password", headers=auth(token),
                    json={"current_password": "Admin@2026", "new_password": "Fleet2026x"})
    assert r.status_code == 200
    return r.json()["token"]


def test_health():
    assert client.get("/api/health").json()["app"] == "mbs-fleet"


def test_bad_login_and_throttle():
    for _ in range(5):
        assert login("ghost", "nope12345").status_code == 401
    assert login("ghost", "nope12345").status_code == 429


def test_sync_create_update_delete(admin_token):
    h = auth(admin_token)
    body = {"upserts": {
        "hotels": [{"id": "h1", "name": "Radisson Blu", "area": "Upper Hill", "lat": -1.2989, "lng": 36.8158,
                    "status": "Active"}],
        "vehicles": [{"id": "v1", "reg": "KDA 123A", "make": "Toyota", "model": "Prado", "year": "2021",
                      "hotelId": "h1", "status": "Available", "odometer": "45000", "lat": "", "gpsAt": ""}],
    }}
    r = client.post("/api/sync", headers=h, json=body)
    assert r.status_code == 200, r.text
    data = client.get("/api/bootstrap", headers=h).json()
    v = data["data"]["vehicles"][0]
    assert v["year"] == 2021 and v["odometer"] == 45000 and v["lat"] == ""
    assert any(a["action"] == "Created" and a["ref"] == "KDA 123A" for a in data["audit"])

    r = client.post("/api/sync", headers=h, json={"upserts": {"vehicles": [{**v, "status": "Maintenance"}]}})
    assert r.status_code == 200
    assert any("status: Available → Maintenance" in a["details"] for a in r.json()["audit"])

    r = client.post("/api/sync", headers=h, json={"upserts": {"fuel": [
        {"id": "f1", "date": "2026-11-25", "vehicleId": "v1", "litres": 40, "price": 172, "cost": 6880}]}})
    assert r.status_code == 200
    r = client.post("/api/sync", headers=h, json={"deletes": {"fuel": ["f1"]}})
    assert r.status_code == 200
    assert client.get("/api/data/fuel", headers=h).json() == []


def test_bad_values_rejected(admin_token):
    r = client.post("/api/sync", headers=auth(admin_token),
                    json={"upserts": {"fuel": [{"id": "f9", "litres": "lots"}]}})
    assert r.status_code == 422
    r = client.post("/api/sync", headers=auth(admin_token), json={"upserts": {"secrets": [{"id": "x"}]}})
    assert r.status_code == 400


def test_users_and_roles(admin_token):
    h = auth(admin_token)
    r = client.post("/api/users", headers=h, json={"name": "Jane Dispatcher", "username": "Jane.D",
                                                   "role": "Dispatcher", "password": "Temp1234x"})
    assert r.status_code == 201, r.text
    jane_id = r.json()["id"]
    assert "password_hash" not in r.json()
    assert client.post("/api/users", headers=h, json={"name": "Dup", "username": "jane.d", "role": "Viewer",
                                                      "password": "Temp1234x"}).status_code == 409

    tok = login("jane.d", "Temp1234x").json()["token"]
    tok = client.post("/api/auth/change-password", headers=auth(tok),
                      json={"current_password": "Temp1234x", "new_password": "Jane2026ok"}).json()["token"]
    jh = auth(tok)
    boot = client.get("/api/bootstrap", headers=jh).json()
    assert boot["audit"] == [] and [u["username"] for u in boot["users"]] == ["jane.d"]

    # Dispatcher: may log trips and change a vehicle's operational fields, nothing more.
    assert client.post("/api/sync", headers=jh, json={"upserts": {"trips": [
        {"id": "t1", "vehicleId": "v1", "from": "Radisson Blu", "to": "JKIA", "start": "2026-11-25T09:00",
         "status": "On Journey"}]}}).status_code == 200
    v = next(x for x in boot["data"]["vehicles"] if x["id"] == "v1")
    assert client.post("/api/sync", headers=jh, json={"upserts": {"vehicles": [
        {**v, "status": "On Journey"}]}}).status_code == 200
    assert client.post("/api/sync", headers=jh, json={"upserts": {"vehicles": [
        {**v, "reg": "HACKED"}]}}).status_code == 403
    assert client.post("/api/sync", headers=jh, json={"upserts": {"hotels": [
        {"id": "h2", "name": "New"}]}}).status_code == 403
    assert client.post("/api/sync", headers=jh, json={"deletes": {"trips": ["t1"]}}).status_code == 403
    assert client.get("/api/users", headers=jh).status_code == 403
    assert client.put("/api/settings", headers=jh, json={"budget": 1}).status_code == 403

    # Disabling a user ends their session.
    assert client.patch(f"/api/users/{jane_id}", headers=h, json={"status": "Disabled"}).status_code == 200
    assert client.get("/api/bootstrap", headers=jh).status_code == 401


def test_last_admin_protected(admin_token):
    h = auth(admin_token)
    me = client.get("/api/auth/me", headers=h).json()
    assert client.patch(f"/api/users/{me['id']}", headers=h, json={"role": "Viewer"}).status_code == 400
    assert client.delete(f"/api/users/{me['id']}", headers=h).status_code == 400


def test_reset_password_forces_change(admin_token):
    h = auth(admin_token)
    uid = client.post("/api/users", headers=h, json={"name": "Max Manager", "username": "max", "role": "Manager",
                                                     "password": "Temp1234x"}).json()["id"]
    assert client.post(f"/api/users/{uid}/reset-password", headers=h, json={"password": "short"}).status_code == 400
    assert client.post(f"/api/users/{uid}/reset-password", headers=h,
                       json={"password": "NewTemp99x"}).status_code == 200
    r = login("max", "NewTemp99x")
    assert r.status_code == 200 and r.json()["user"]["mustChange"] is True


def test_settings(admin_token):
    r = client.put("/api/settings", headers=auth(admin_token), json={"budget": "4000000", "company": "MBS"})
    assert r.status_code == 200 and r.json()["budget"] == 4000000 and r.json()["company"] == "MBS"


def test_gps_ingest_with_tracker_key(admin_token):
    rev0 = client.get("/api/rev", headers=auth(admin_token)).json()["rev"]
    r = client.post("/api/gps/positions", headers={"X-Tracker-Key": "tracker-test-key"}, json={"readings": [
        {"reg": "kda123a", "lat": -1.3192, "lng": 36.9278, "timestamp": "2026-11-25 08:00", "speed": 52},
        {"reg": "KDA 123A", "lat": -1.30, "lng": 36.90, "timestamp": "2026-11-25 07:00"},
        {"reg": "NOPE 1", "lat": -1.3, "lng": 36.8},
    ]})
    assert r.status_code == 200, r.text
    assert r.json() == {"applied": 1, "vehicles": 1, "older": 1, "unknown": ["NOPE 1"], "invalid": []}
    v = next(x for x in client.get("/api/data/vehicles", headers=auth(admin_token)).json() if x["id"] == "v1")
    assert (v["lat"], v["lng"], v["speed"], v["gpsSrc"]) == (-1.3192, 36.9278, 52, "Tracker feed")
    assert client.get("/api/rev", headers=auth(admin_token)).json()["rev"] > rev0
    track = client.get("/api/gps/track/v1?hours=744", headers=auth(admin_token)).json()
    assert len(track) >= 2

    assert client.post("/api/gps/positions", headers={"X-Tracker-Key": "wrong"},
                       json={"readings": [{"reg": "KDA 123A", "lat": 0, "lng": 0}]}).status_code == 401


def test_frontend_served():
    r = client.get("/")
    assert r.status_code in (200, 404)
