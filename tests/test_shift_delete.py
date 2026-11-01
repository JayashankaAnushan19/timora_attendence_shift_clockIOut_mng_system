"""Deleting shifts: who may, when it's refused, and that history survives."""
import json
import sqlite3

import pytest

from backend.database import init_db
from .conftest import Api


@pytest.fixture
def login_as(app, admin):
    roles = {r["name"]: r["id"] for r in admin.get("/api/admin/roles").json["items"]}

    def create(role, username):
        assert admin.post("/api/admin/users", {
            "username": username, "password": "Temp-pass1", "role_id": roles[role]}).status_code == 201
        api = Api(app.test_client())
        assert api.login(username, "Temp-pass1").status_code == 200
        assert api.post("/api/auth/change-password", {"current_password": "Temp-pass1", "new_password": "Own-pass-99"}).status_code == 200
        return api
    return create


def codes(admin):
    return [s["code"] for s in admin.get("/api/admin/shifts").json["items"]]


@pytest.mark.parametrize("role,expected", [
    ("Super Admin", 200), ("Admin", 200), ("Manager", 403), ("Attendance Manager", 403), ("Viewer", 403),
])
def test_only_super_admin_and_admin_can_delete(make_shift, login_as, role, expected):
    shift = make_shift("TEMP", "09:00", "17:00")
    assert login_as(role, "tester").delete(f"/api/admin/shifts/{shift}").status_code == expected


def test_a_role_that_manages_shifts_still_cannot_delete(make_shift, admin, login_as):
    shift = make_shift("TEMP", "09:00", "17:00")
    role = next(r for r in admin.get("/api/admin/roles").json["items"] if r["name"] == "Viewer")
    assert admin.put(f"/api/admin/roles/{role['id']}", {"permissions": ["shifts.view", "shifts.manage"]}).status_code == 200
    api = login_as("Viewer", "vera")
    assert api.put(f"/api/admin/shifts/{shift}", {"name": "New", "code": "TEMP", "start_time": "09:00", "end_time": "17:00", "working_days": [0]}).status_code == 200
    assert api.delete(f"/api/admin/shifts/{shift}").status_code == 403


def test_not_logged_in_cannot_delete(make_shift, anon):
    assert anon.delete(f"/api/admin/shifts/{make_shift('TEMP', '09:00', '17:00')}").status_code == 401


def test_unused_shift_is_removed_completely(app, make_shift, admin):
    shift = make_shift("TEMP", "09:00", "17:00")
    assert admin.delete(f"/api/admin/shifts/{shift}").status_code == 200
    assert "TEMP" not in codes(admin)
    assert admin.get(f"/api/admin/shifts/{shift}").status_code == 404
    assert admin.delete(f"/api/admin/shifts/{shift}").status_code == 404
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    assert conn.execute("SELECT COUNT(*) FROM shifts WHERE id = ?", (shift,)).fetchone()[0] == 0
    conn.close()
    assert make_shift("TEMP", "09:00", "17:00")


def test_refused_while_employees_are_assigned(make_shift, make_employee, admin):
    shift = make_shift("TEMP", "09:00", "17:00")
    other = make_shift("OTHER", "09:00", "17:00")
    code = make_employee(shift_id=shift)
    response = admin.delete(f"/api/admin/shifts/{shift}")
    assert response.status_code == 409 and response.json["code"] == "SHIFT_IN_USE" and response.json["assigned"] == 1
    assert "Shift Assignments" in response.json["message"]
    assert "TEMP" in codes(admin)
    assert admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": other, "effective_from": "2026-10-26"}).status_code == 201
    assert admin.delete(f"/api/admin/shifts/{shift}").status_code == 200


def test_future_assignment_also_blocks(make_shift, make_employee, admin):
    shift = make_shift("TEMP", "09:00", "17:00")
    code = make_employee()
    admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": shift, "effective_from": "2026-12-01"})
    assert admin.delete(f"/api/admin/shifts/{shift}").json["code"] == "SHIFT_IN_USE"


def test_last_shift_cannot_be_deleted(admin):
    items = admin.get("/api/admin/shifts").json["items"]
    for shift in items[:-1]:
        assert admin.delete(f"/api/admin/shifts/{shift['id']}").status_code == 200
    response = admin.delete(f"/api/admin/shifts/{items[-1]['id']}")
    assert response.status_code == 409 and response.json["code"] == "LAST_SHIFT"


def test_history_survives_and_code_can_be_reused(make_shift, make_employee, clock_action, clock, admin):
    shift = make_shift("TEMP", "09:00", "18:00")
    other = make_shift("OTHER", "09:00", "18:00")
    code = make_employee(shift_id=shift)
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-26 18:00:00")
    clock_action(code, "clock_out")
    clock.set("2026-10-27 12:00:00")
    admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": other, "effective_from": "2026-10-27"})
    assert admin.delete(f"/api/admin/shifts/{shift}").status_code == 200

    assert "TEMP" not in codes(admin)
    assert shift not in [s["id"] for s in admin.get("/api/admin/lookups").json["shifts"]]
    row = admin.get(f"/api/admin/attendance?employee={code}&from=2026-10-26&to=2026-10-26").json["items"][0]
    assert row["shift_id"] == shift and row["shift_name"] == "TEMP shift" and row["working_seconds"] == 8 * 3600
    report = admin.get("/api/admin/reports/daily?preset=custom&from=2026-10-26&to=2026-10-26").json["items"]
    assert report[0]["shift_name"] == "TEMP shift"
    assert make_shift("TEMP", "10:00", "19:00") != shift
    # Correcting an old record still works and keeps its shift.
    assert admin.put(f"/api/admin/attendance/{row['id']}", {"notes": "Checked", "reason": "Review"}).status_code == 200
    assert admin.get(f"/api/admin/attendance/{row['id']}").json["record"]["shift_id"] == shift


def test_deleted_shift_cannot_be_assigned_edited_or_activated(make_shift, make_employee, clock_action, clock, admin):
    shift = make_shift("TEMP", "09:00", "18:00")
    keep = make_shift("KEEP", "09:00", "18:00")
    code = make_employee(shift_id=shift)
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-26 18:00:00")
    clock_action(code, "clock_out")
    clock.set("2026-10-27 12:00:00")
    admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": keep, "effective_from": "2026-10-27"})
    assert admin.delete(f"/api/admin/shifts/{shift}").status_code == 200
    assert admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": shift}).status_code == 400
    assert admin.post(f"/api/admin/shifts/{shift}/status", {"status": "active"}).status_code == 404
    assert admin.put(f"/api/admin/shifts/{shift}", {"name": "X", "code": "Y", "start_time": "09:00", "end_time": "17:00", "working_days": [0]}).status_code == 404


def test_upgrade_gives_admin_delete_and_adds_the_column(app):
    path = app.config["TIMORA"]["database"]
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE shifts DROP COLUMN deleted_at")
    permissions = json.loads(conn.execute("SELECT permissions FROM roles WHERE name = 'Admin'").fetchone()[0])
    permissions.remove("shifts.delete")
    conn.execute("UPDATE roles SET permissions = ? WHERE name = 'Admin'", (json.dumps(permissions),))
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    init_db(path)
    conn = sqlite3.connect(path)
    assert "shifts.delete" in conn.execute("SELECT permissions FROM roles WHERE name = 'Admin'").fetchone()[0]
    assert "shifts.delete" not in conn.execute("SELECT permissions FROM roles WHERE name = 'Manager'").fetchone()[0]
    assert "deleted_at" in [r[1] for r in conn.execute("PRAGMA table_info(shifts)")]
    conn.close()
