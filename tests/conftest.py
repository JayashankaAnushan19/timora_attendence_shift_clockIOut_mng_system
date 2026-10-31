from datetime import datetime, timedelta, timezone

import pytest

from backend import timeutil
from backend.app import create_app
from backend.database import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME

NEW_PASSWORD = "NewPassword!1"
HEADERS = {"X-Timora-Request": "1"}


class Clock:
    """A controllable replacement for the system clock. All times here are UTC."""

    def __init__(self):
        self.now = datetime(2026, 10, 26, 8, 0, 0, tzinfo=timezone.utc)  # a Monday

    def set(self, value):
        self.now = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(timeutil, "now_utc", lambda: c.now)
    return c


@pytest.fixture
def app(tmp_path, clock):
    return create_app({"database": str(tmp_path / "timora.db"), "backup_dir": str(tmp_path / "backups")})


class Api:
    """Test client wrapper that sends the header the admin frontend sends.

    Tests jump the clock by days, so sessions are kept fresh unless a test is
    specifically about expiry (keep_alive = False).
    """

    def __init__(self, client):
        self.client = client
        self.keep_alive = True

    def _touch(self):
        if self.keep_alive:
            with self.client.session_transaction() as session:
                if session.get("admin_id"):
                    session["last_seen"] = timeutil.to_iso(timeutil.now_utc())

    def get(self, url, **kw):
        self._touch()
        return self.client.get(url, **kw)

    def post(self, url, json=None, **kw):
        self._touch()
        return self.client.post(url, json={} if json is None else json, headers=HEADERS, **kw)

    def put(self, url, json=None):
        self._touch()
        return self.client.put(url, json=json or {}, headers=HEADERS)

    def delete(self, url):
        self._touch()
        return self.client.delete(url, headers=HEADERS)

    def login(self, username, password):
        return self.post("/api/auth/login", {"username": username, "password": password})


@pytest.fixture
def anon(app):
    return Api(app.test_client())


@pytest.fixture
def admin(app):
    api = Api(app.test_client())
    assert api.login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).status_code == 200
    response = api.post("/api/auth/change-password", {
        "current_password": DEFAULT_ADMIN_PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    return api


@pytest.fixture
def make_shift(admin):
    def create(code, start, end, **overrides):
        body = {"name": f"{code} shift", "code": code, "start_time": start, "end_time": end,
                "working_days": [0, 1, 2, 3, 4, 5, 6], "grace_minutes": 10, "break_minutes": 60,
                "early_clock_in_minutes": 60, "late_clock_out_minutes": 240, **overrides}
        response = admin.post("/api/admin/shifts", body)
        assert response.status_code == 201, response.json
        return response.json["id"]
    return create


@pytest.fixture
def make_employee(admin):
    def create(name="John Smith", shift_id=None, joined="2026-01-01", **overrides):
        body = {"name": name, "department": "Ops", "designation": "Staff", "date_of_joining": joined,
                "shift_id": shift_id, **overrides}
        response = admin.post("/api/admin/employees", body)
        assert response.status_code == 201, response.json
        return response.json["employee_id"]
    return create


@pytest.fixture
def clock_action(anon):
    def run(code, action, request_id=None):
        body = {"employee_id": code, "action": action}
        if request_id:
            body["request_id"] = request_id
        return anon.client.post("/api/attendance/clock", json=body)
    return run


@pytest.fixture
def status_of(anon):
    def run(code):
        response = anon.get(f"/api/public/employees/{code}/status")
        assert response.status_code == 200, response.json
        return response.json
    return run
