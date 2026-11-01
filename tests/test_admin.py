"""Login, permissions, employees, shifts, corrections, reports, backup."""
import sqlite3

import pytest

from backend.database import DEFAULT_ADMIN_PASSWORD
from .conftest import HEADERS, NEW_PASSWORD, Api


# --- Authentication -------------------------------------------------------

def test_admin_routes_require_login(anon):
    for url in ("/api/admin/dashboard", "/api/admin/employees", "/api/admin/settings",
                "/api/admin/backup", "/api/admin/attendance", "/api/admin/users",
                "/api/admin/reports/daily", "/api/admin/export/employees"):
        response = anon.get(url)
        assert response.status_code == 401, url
        assert response.json["success"] is False and response.json["code"] == "AUTH_REQUIRED"


def test_admin_pages_redirect_to_login(anon):
    for page in ("dashboard", "employees", "settings", "backup"):
        response = anon.get(f"/admin/{page}")
        assert response.status_code == 302 and response.headers["Location"].endswith("/admin/login")
    assert anon.get("/").status_code == 200


def test_login_logout_and_forced_password_change(app):
    api = Api(app.test_client())
    assert api.login("admin", "wrong").status_code == 401
    response = api.login("admin", DEFAULT_ADMIN_PASSWORD)
    assert response.status_code == 200 and response.json["admin"]["must_change_password"] is True
    blocked = api.get("/api/admin/dashboard")
    assert blocked.status_code == 403 and blocked.json["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert api.post("/api/auth/change-password", {"current_password": "bad", "new_password": NEW_PASSWORD}).status_code == 400
    assert api.post("/api/auth/change-password", {"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": "short"}).status_code == 400
    assert api.post("/api/auth/change-password", {"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200
    assert api.get("/api/admin/dashboard").status_code == 200
    assert api.post("/api/auth/logout").status_code == 200
    assert api.get("/api/admin/dashboard").status_code == 401


def test_passwords_are_hashed(app, admin):
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    stored = conn.execute("SELECT password_hash FROM administrators").fetchone()[0]
    conn.close()
    assert NEW_PASSWORD not in stored and stored.startswith(("scrypt:", "pbkdf2:"))


def test_session_expires_after_idle_timeout(admin, clock):
    admin.keep_alive = False
    assert admin.get("/api/admin/dashboard").status_code == 200
    clock.advance(minutes=31)
    assert admin.get("/api/admin/dashboard").status_code == 401


def test_session_stays_alive_while_active(admin, clock):
    admin.keep_alive = False
    for _ in range(4):
        clock.advance(minutes=20)
        assert admin.get("/api/admin/dashboard").status_code == 200


def test_login_lockout(app, admin):
    api = Api(app.test_client())
    for _ in range(5):
        assert api.login("admin", "wrong-password").status_code == 401
    locked = api.login("admin", NEW_PASSWORD)
    assert locked.status_code == 429 and locked.json["code"] == "ACCOUNT_LOCKED"


def test_mutations_need_the_custom_header(admin):
    response = admin.client.post("/api/admin/employees", json={"name": "X"})
    assert response.status_code == 403 and response.json["code"] == "CSRF_BLOCKED"


# --- Role-based access ----------------------------------------------------

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


def test_viewer_is_read_only(login_as):
    viewer = login_as("Viewer", "vera")
    assert viewer.get("/api/admin/dashboard").status_code == 200
    assert viewer.get("/api/admin/reports/daily?preset=today").status_code == 200
    assert viewer.get("/api/admin/employees").status_code == 403
    assert viewer.get("/api/admin/attendance").status_code == 403
    assert viewer.post("/api/admin/employees", {"name": "X"}).status_code == 403
    assert viewer.get("/api/admin/backup").status_code == 403
    assert viewer.get("/api/admin/settings").status_code == 403
    assert viewer.get("/admin/employees").status_code == 302


def test_attendance_manager_can_correct_but_not_manage_people(login_as):
    manager = login_as("Attendance Manager", "mira")
    assert manager.get("/api/admin/attendance").status_code == 200
    assert manager.post("/api/admin/employees", {"name": "X"}).status_code == 403
    assert manager.post("/api/admin/shifts", {}).status_code == 403
    assert manager.get("/api/admin/users").status_code == 403


def test_admin_role_limits(login_as):
    regular = login_as("Admin", "adam")
    assert regular.get("/api/admin/employees").status_code == 200
    assert regular.get("/api/admin/shifts").status_code == 200
    assert regular.get("/api/admin/users").status_code == 403
    assert regular.get("/api/admin/settings").status_code == 403
    assert regular.get("/api/admin/backup").status_code == 403


def test_role_permission_changes_apply_immediately(admin, login_as):
    viewer = login_as("Viewer", "vera")
    role = next(r for r in admin.get("/api/admin/roles").json["items"] if r["name"] == "Viewer")
    assert admin.put(f"/api/admin/roles/{role['id']}", {"permissions": ["reports.view"]}).status_code == 200
    assert viewer.get("/api/admin/dashboard").status_code == 403
    assert viewer.get("/api/admin/reports/daily").status_code == 200


def test_super_admin_role_is_locked(admin):
    role = next(r for r in admin.get("/api/admin/roles").json["items"] if r["name"] == "Super Admin")
    assert admin.put(f"/api/admin/roles/{role['id']}", {"permissions": []}).status_code == 409


def test_last_super_admin_is_protected(admin):
    users = admin.get("/api/admin/users").json["items"]
    me = users[0]
    assert admin.post(f"/api/admin/users/{me['id']}/status", {"is_active": False}).json["code"] == "SELF_DEACTIVATE"
    viewer_role = next(r for r in admin.get("/api/admin/roles").json["items"] if r["name"] == "Viewer")
    assert admin.put(f"/api/admin/users/{me['id']}", {"role_id": viewer_role["id"]}).json["code"] == "LAST_SUPER_ADMIN"


def test_password_reset_ends_sessions(admin, login_as):
    viewer = login_as("Viewer", "vera")
    user = next(u for u in admin.get("/api/admin/users").json["items"] if u["username"] == "vera")
    assert admin.post(f"/api/admin/users/{user['id']}/reset-password", {"password": "Another-1234"}).status_code == 200
    assert viewer.get("/api/admin/dashboard").status_code == 401


def test_deactivated_admin_loses_access(admin, login_as):
    viewer = login_as("Viewer", "vera")
    user = next(u for u in admin.get("/api/admin/users").json["items"] if u["username"] == "vera")
    assert admin.post(f"/api/admin/users/{user['id']}/status", {"is_active": False}).status_code == 200
    assert viewer.get("/api/admin/dashboard").status_code == 401
    assert viewer.login("vera", "Own-pass-99").status_code == 401


# --- Employees ------------------------------------------------------------

def test_employee_validation(admin):
    assert admin.post("/api/admin/employees", {"name": ""}).status_code == 400
    bad_email = admin.post("/api/admin/employees", {"name": "A", "department": "D", "designation": "D", "email": "nope"})
    assert bad_email.status_code == 400 and bad_email.json["field"] == "email"


def test_sql_injection_is_inert(make_employee, admin, anon):
    make_employee("Robert'); DROP TABLE employees;--")
    assert anon.get("/api/public/employees/search?q=' OR 1=1 --").json["items"] == []
    assert admin.get("/api/admin/employees?sort=name;DROP TABLE employees").json["total"] == 1
    assert admin.get("/api/admin/employees?department=' OR '1'='1").json["total"] == 0


def test_employee_edit_filter_deactivate_reactivate(make_employee, admin):
    code = make_employee("Old Name", department="Sales")
    assert admin.put(f"/api/admin/employees/{code}", {"name": "New Name", "department": "Finance", "designation": "Lead"}).status_code == 200
    detail = admin.get(f"/api/admin/employees/{code}").json
    assert detail["employee"]["name"] == "New Name" and detail["employee"]["employee_id"] == code
    assert admin.get("/api/admin/employees?department=Finance").json["total"] == 1
    assert admin.post(f"/api/admin/employees/{code}/deactivate").status_code == 200
    assert admin.get("/api/admin/employees?status=inactive").json["total"] == 1
    assert admin.post(f"/api/admin/employees/{code}/reactivate").status_code == 200
    assert admin.get("/api/admin/employees?status=active").json["total"] == 1


def test_ids_are_never_reused_and_cannot_be_deleted(app, make_employee, admin):
    first = make_employee("First")
    admin.post(f"/api/admin/employees/{first}/deactivate")
    assert make_employee("Second") == "EM002"
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM employees")
    conn.close()


def test_employee_limit_and_warning(make_employee, admin):
    assert admin.put("/api/admin/settings", {"max_employees": 2}).status_code == 200
    make_employee("One")
    capacity = admin.get("/api/admin/employees").json["capacity"]
    assert capacity["warning"] is True and capacity["remaining"] == 1
    make_employee("Two")
    response = admin.post("/api/admin/employees", {"name": "Three", "department": "D", "designation": "D"})
    assert response.status_code == 409 and response.json["code"] == "EMPLOYEE_LIMIT_REACHED"


def test_id_number_range_is_the_hard_limit(make_employee, admin):
    assert admin.put("/api/admin/settings", {"employee_id_start": 999}).status_code == 200
    assert make_employee("Last") == "EM999"
    assert admin.post("/api/admin/employees", {"name": "Over", "department": "D", "designation": "D"}).status_code == 409


def test_prefix_setting_applies_to_new_ids(make_employee, admin):
    make_employee("One")
    assert admin.put("/api/admin/settings", {"employee_id_prefix": "ST"}).status_code == 200
    assert make_employee("Two") == "ST002"


# --- Shifts and assignments ----------------------------------------------

def test_shift_validation_and_overnight_flag(admin, make_shift):
    shift_id = make_shift("NGT", "22:00", "07:00")
    shift = admin.get(f"/api/admin/shifts/{shift_id}").json["shift"]
    assert shift["is_overnight"] is True
    base = {"name": "X", "code": "X1", "start_time": "09:00", "end_time": "09:00", "working_days": [0]}
    assert admin.post("/api/admin/shifts", {**base, "end_time": "25:00"}).status_code == 400
    assert admin.post("/api/admin/shifts", {**base, "end_time": "10:00", "working_days": []}).status_code == 400
    assert admin.post("/api/admin/shifts", {**base, "end_time": "10:00", "code": "NGT"}).status_code == 409


def test_assignment_history_splits_cleanly(make_shift, make_employee, admin):
    a = make_shift("AAA", "09:00", "18:00")
    b = make_shift("BBB", "14:00", "23:00")
    code = make_employee(shift_id=a, joined="2026-01-01")
    assert admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": b, "effective_from": "2026-06-01", "effective_to": "2026-06-30"}).status_code == 201
    rows = admin.get(f"/api/admin/assignments?employee={code}&view=all").json["items"]
    spans = sorted((r["shift_id"], r["effective_from"], r["effective_to"]) for r in rows if r["status"] == "active")
    assert spans == [(a, "2026-01-01", "2026-05-31"), (a, "2026-07-01", None), (b, "2026-06-01", "2026-06-30")]


def test_bulk_assignment_and_same_day_replacement(make_shift, make_employee, admin):
    a = make_shift("AAA", "09:00", "18:00")
    b = make_shift("BBB", "14:00", "23:00")
    codes = [make_employee("One"), make_employee("Two")]
    assert admin.post("/api/admin/assignments", {"employee_ids": codes, "shift_id": a, "effective_from": "2026-10-01"}).json["count"] == 2
    assert admin.post("/api/admin/assignments", {"employee_ids": codes[:1], "shift_id": b, "effective_from": "2026-10-01"}).status_code == 201
    current = admin.get(f"/api/admin/assignments?employee={codes[0]}&view=current").json["items"]
    assert [r["shift_id"] for r in current] == [b]
    assert admin.get(f"/api/admin/shifts/{a}").json["employees"][0]["employee_id"] == codes[1]


def test_schedule_follows_date_of_assignment_change(make_shift, make_employee, status_of, clock, admin):
    a = make_shift("AAA", "09:00", "18:00")
    b = make_shift("BBB", "14:00", "23:00")
    code = make_employee(shift_id=a)
    admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": b, "effective_from": "2026-10-27"})
    clock.set("2026-10-26 08:30:00")
    assert status_of(code)["shift"]["code"] == "AAA"
    clock.set("2026-10-27 08:30:00")
    assert status_of(code)["shift"]["code"] == "BBB"
    assert status_of(code)["status"] == "shift_not_started"


def test_past_records_keep_the_shift_worked(make_shift, make_employee, clock_action, clock, admin):
    a = make_shift("AAA", "09:00", "18:00")
    b = make_shift("BBB", "14:00", "23:00")
    code = make_employee(shift_id=a)
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-26 18:00:00")
    clock_action(code, "clock_out")
    admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": b, "effective_from": "2026-01-01"})
    admin.put(f"/api/admin/shifts/{a}", {"name": "Renamed", "code": "AAA", "start_time": "06:00", "end_time": "07:00", "working_days": [0]})
    row = admin.get(f"/api/admin/attendance?employee={code}").json["items"][0]
    assert row["shift_id"] == a and row["working_seconds"] == 8 * 3600 and row["status"] == "Present"


def test_inactive_shift_blocks_assignment_and_scheduling(make_shift, make_employee, status_of, clock, admin):
    shift = make_shift("AAA", "09:00", "18:00")
    code = make_employee(shift_id=shift)
    assert admin.post(f"/api/admin/shifts/{shift}/status", {"status": "inactive"}).status_code == 200
    clock.set("2026-10-26 09:00:00")
    assert status_of(code)["status"] == "not_scheduled"
    assert admin.post("/api/admin/assignments", {"employee_ids": [code], "shift_id": shift}).json["code"] == "SHIFT_INACTIVE"


# --- Attendance corrections ----------------------------------------------

@pytest.fixture
def worked_day(make_shift, make_employee, clock_action, clock, admin):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-26 18:00:00")
    clock_action(code, "clock_out")
    clock.set("2026-10-27 12:00:00")
    return code, admin.get(f"/api/admin/attendance?employee={code}").json["items"][0]["id"]


def test_correction_needs_reason_and_keeps_originals(worked_day, admin):
    _code, record_id = worked_day
    assert admin.put(f"/api/admin/attendance/{record_id}", {"clock_in": "2026-10-26T09:30:00"}).status_code == 400
    response = admin.put(f"/api/admin/attendance/{record_id}", {"clock_in": "2026-10-26T09:30:00", "reason": "Badge fault"})
    assert response.status_code == 200
    detail = admin.get(f"/api/admin/attendance/{record_id}").json
    assert detail["record"]["late_seconds"] == 30 * 60 and detail["record"]["status"] == "Late"
    change = next(a for a in detail["adjustments"] if a["field_name"] == "clock_in")
    assert change["original_value"] == "2026-10-26T09:00:00Z"
    assert change["updated_value"] == "2026-10-26T09:30:00Z"
    assert change["reason"] == "Badge fault" and change["admin_username"] == "admin" and change["adjusted_at"]


def test_correction_validation(worked_day, admin):
    _code, record_id = worked_day
    url = f"/api/admin/attendance/{record_id}"
    assert admin.put(url, {"clock_out": "2026-10-26T08:00:00", "reason": "x"}).status_code == 400
    assert admin.put(url, {"clock_out": "2027-01-01T08:00:00", "reason": "x"}).status_code == 400
    assert admin.put(url, {"status": "Nonsense", "reason": "x"}).status_code == 400
    assert admin.put(url, {"reason": "nothing changed"}).json["code"] == "NO_CHANGES"


def test_manual_status_sticks_until_set_back_to_auto(worked_day, admin):
    _code, record_id = worked_day
    url = f"/api/admin/attendance/{record_id}"
    assert admin.put(url, {"status": "Half Day", "reason": "Agreed"}).status_code == 200
    assert admin.get(url).json["record"]["status"] == "Half Day"
    assert admin.put(url, {"status": "auto", "reason": "Undo"}).status_code == 200
    assert admin.get(url).json["record"]["status"] == "Present"


def test_add_missing_clock_out(make_shift, make_employee, clock_action, clock, admin):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00", late_clock_out_minutes=60))
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-27 12:00:00")
    report = admin.get("/api/admin/reports/missing_clockout?preset=custom&from=2026-10-26&to=2026-10-26").json
    assert report["total"] == 1
    record_id = report["items"][0]["id"]
    assert admin.put(f"/api/admin/attendance/{record_id}", {"clock_out": "2026-10-26T18:00:00", "reason": "Forgot"}).status_code == 200
    row = admin.get(f"/api/admin/attendance/{record_id}").json["record"]
    assert row["status"] == "Present" and row["working_seconds"] == 8 * 3600
    assert admin.get("/api/admin/reports/missing_clockout?preset=custom&from=2026-10-26&to=2026-10-26").json["total"] == 0


def test_add_missing_record_rules(worked_day, make_employee, clock, admin):
    code, record_id = worked_day
    day = {"employee_id": code, "attendance_date": "2026-10-26", "clock_in": "2026-10-26T09:00:00", "reason": "x"}
    duplicate = admin.post("/api/admin/attendance", day)
    assert duplicate.status_code == 409 and duplicate.json["record_id"] == record_id
    missing_reason = {**day, "attendance_date": "2026-10-25", "clock_in": "2026-10-25T09:00:00", "clock_out": "2026-10-25T18:00:00", "reason": ""}
    assert admin.post("/api/admin/attendance", missing_reason).status_code == 400
    added = admin.post("/api/admin/attendance", {**missing_reason, "reason": "Terminal was down"})
    assert added.status_code == 201
    detail = admin.get(f"/api/admin/attendance/{added.json['id']}").json
    assert detail["record"]["source"] == "admin" and detail["record"]["working_seconds"] == 8 * 3600
    assert any(a["reason"] == "Terminal was down" for a in detail["adjustments"])
    overlap = {**day, "attendance_date": "2026-10-20", "clock_in": "2026-10-26T10:00:00", "clock_out": "2026-10-26T11:00:00"}
    assert admin.post("/api/admin/attendance", overlap).json["code"] == "OVERLAP"
    no_times = admin.post("/api/admin/attendance", {"employee_id": code, "attendance_date": "2026-10-21", "status": "Leave", "reason": "Family"})
    assert no_times.status_code == 201
    assert admin.post("/api/admin/attendance", {"employee_id": code, "attendance_date": "2026-10-22", "reason": "x"}).status_code == 400


def test_marked_day_blocks_terminal(make_shift, make_employee, status_of, clock, admin):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 09:00:00")
    assert admin.post("/api/admin/attendance", {"employee_id": code, "attendance_date": "2026-10-26", "status": "Leave", "reason": "Called in"}).status_code == 201
    state = status_of(code)
    assert state["status"] == "not_scheduled" and state["action"] is None


def test_history_tables_are_append_only(app, worked_day, admin):
    _code, record_id = worked_day
    admin.put(f"/api/admin/attendance/{record_id}", {"status": "Late", "reason": "x"})
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    for sql in ("DELETE FROM attendance_adjustments", "UPDATE attendance_adjustments SET reason = 'y'",
                "DELETE FROM audit_logs", "UPDATE audit_logs SET action = 'y'", "DELETE FROM attendance"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    conn.close()


def test_employees_cannot_edit_records_publicly(worked_day, anon):
    _code, record_id = worked_day
    assert anon.client.put(f"/api/admin/attendance/{record_id}", json={"status": "Present", "reason": "x"}, headers=HEADERS).status_code == 401


def test_status_filter_includes_derived_absent(make_shift, make_employee, clock, admin):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-27 12:00:00")
    rows = admin.get("/api/admin/attendance?status=Absent&from=2026-10-26&to=2026-10-26").json["items"]
    assert [(r["employee_id"], r["id"], r["attendance_date"]) for r in rows] == [(code, None, "2026-10-26")]
    assert admin.get("/api/admin/attendance?status=Holiday&from=2026-10-26&to=2026-10-26").json["items"] == []


# --- Dashboard and reports ------------------------------------------------

def test_dashboard_counts(make_shift, make_employee, clock_action, clock, admin):
    shift = make_shift("DAY", "09:00", "18:00")
    working = make_employee("Working", shift)
    done = make_employee("Done", shift)
    make_employee("No Shift")
    clock.set("2026-10-26 09:30:00")
    clock_action(working, "clock_in")
    clock_action(done, "clock_in")
    clock.advance(minutes=5)
    clock_action(done, "clock_out")
    d = admin.get("/api/admin/dashboard").json
    assert d["cards"] == {"total_employees": 3, "clocked_in": 1, "clocked_out": 1, "late_today": 2,
                          "not_scheduled": 1, "records_today": 2}
    assert len(d["recent"]) == 2 and len(d["overview"]) == 3


@pytest.fixture
def report_data(make_shift, make_employee, clock_action, clock, admin):
    shift = make_shift("DAY", "09:00", "18:00")
    a = make_employee("Alice", shift, department="Ops")
    b = make_employee("Bob", shift, department="Sales")
    clock.set("2026-10-26 09:30:00")
    clock_action(a, "clock_in")
    clock.set("2026-10-26 20:00:00")
    clock_action(a, "clock_out")
    clock.set("2026-10-27 08:00:00")
    return a, b, shift


RANGE = "preset=custom&from=2026-10-26&to=2026-10-26"


def test_each_report(report_data, admin):
    a, b, _shift = report_data
    late = admin.get(f"/api/admin/reports/late?{RANGE}").json
    assert [r["employee_id"] for r in late["items"]] == [a] and late["items"][0]["late_seconds"] == 1800
    assert admin.get(f"/api/admin/reports/overtime?{RANGE}").json["items"][0]["overtime_seconds"] == 2 * 3600
    assert admin.get(f"/api/admin/reports/early?{RANGE}").json["total"] == 0
    absent = admin.get(f"/api/admin/reports/absent?{RANGE}").json
    assert [r["employee_id"] for r in absent["items"]] == [b]
    summary = {r["employee_id"]: r for r in admin.get(f"/api/admin/reports/summary?{RANGE}").json["items"]}
    assert summary[a]["days_present"] == 1 and summary[a]["late_count"] == 1
    assert summary[b]["absent_days"] == 1 and summary[b]["days_present"] == 0
    daily = admin.get(f"/api/admin/reports/daily?{RANGE}").json
    assert daily["total"] == 2
    monthly = admin.get("/api/admin/reports/monthly?preset=custom&from=2026-10-01&to=2026-10-26").json
    assert {r["month"] for r in monthly["items"]} == {"2026-10"}
    hours = admin.get(f"/api/admin/reports/hours?{RANGE}").json
    worked = next(r for r in hours["items"] if r["employee_id"] == a)
    assert worked["working_seconds"] == 10.5 * 3600 - 3600


def test_report_filters_sort_paginate(report_data, admin):
    a, b, shift = report_data
    assert admin.get(f"/api/admin/reports/daily?{RANGE}&employee={b}").json["total"] == 1
    assert admin.get(f"/api/admin/reports/daily?{RANGE}&department=Ops").json["total"] == 1
    assert admin.get(f"/api/admin/reports/daily?{RANGE}&shift={shift}").json["total"] == 2
    desc = admin.get(f"/api/admin/reports/daily?{RANGE}&sort=employee_name&dir=desc").json["items"]
    assert [r["employee_name"] for r in desc] == ["Bob", "Alice"]
    page = admin.get(f"/api/admin/reports/daily?{RANGE}&per_page=1&page=2").json
    assert len(page["items"]) == 1 and page["total"] == 2


def test_report_presets(report_data, admin, clock):
    clock.set("2026-10-27 12:00:00")
    assert admin.get("/api/admin/reports/daily?preset=yesterday").json["start"] == "2026-10-26"
    assert admin.get("/api/admin/reports/daily?preset=today").json["start"] == "2026-10-27"
    assert admin.get("/api/admin/reports/daily?preset=this_week").json["start"] == "2026-10-26"
    assert admin.get("/api/admin/reports/daily?preset=this_month").json["start"] == "2026-10-01"
    assert admin.get("/api/admin/reports/daily?preset=custom&from=2026-10-27&to=2026-10-01").status_code == 400
    assert admin.get("/api/admin/reports/daily?preset=custom&from=2020-01-01&to=2026-10-01").status_code == 400
    assert admin.get("/api/admin/reports/nonsense").status_code == 404


def test_exports(report_data, admin):
    csv_response = admin.get(f"/api/admin/reports/daily?{RANGE}&format=csv")
    assert csv_response.status_code == 200 and "text/csv" in csv_response.content_type
    text = csv_response.data.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Date,Employee ID,Employee") and "Alice" in text
    assert "2026-10-26,09:30:00" in text
    json_response = admin.get(f"/api/admin/reports/late?{RANGE}&format=json")
    assert json_response.json["rows"][0]["Late"] == "0:30"
    assert "attachment" in json_response.headers["Content-Disposition"]


def test_csv_formula_cells_are_neutralised(make_employee, admin):
    make_employee("=HYPERLINK(1)")
    text = admin.get("/api/admin/export/employees?format=csv").data.decode("utf-8-sig")
    assert "'=HYPERLINK(1)" in text


# --- Settings -------------------------------------------------------------

def test_settings_validation_and_public_view(admin, anon):
    assert admin.put("/api/admin/settings", {"timezone": "Mars/Base"}).status_code == 400
    assert admin.put("/api/admin/settings", {"max_employees": 1000}).status_code == 400
    assert admin.put("/api/admin/settings", {"session_timeout_minutes": "abc"}).status_code == 400
    assert admin.put("/api/admin/settings", {"unknown": 1}).status_code == 400
    assert admin.put("/api/admin/settings", {"company_name": "Acme", "time_format": "24"}).status_code == 200
    config = anon.get("/api/public/config").json["config"]
    assert config["company_name"] == "Acme" and config["time_format"] == "24"
    assert "session_timeout_minutes" not in config and "login_max_attempts" not in config


def test_session_timeout_setting_is_honoured(admin, clock):
    admin.keep_alive = False
    assert admin.put("/api/admin/settings", {"session_timeout_minutes": 120}).status_code == 200
    clock.advance(minutes=100)
    assert admin.get("/api/admin/dashboard").status_code == 200


def test_logo_upload_rejects_non_images(admin, anon):
    import io
    def upload(content, name):
        return admin.client.post("/api/admin/settings/logo", data={"logo": (io.BytesIO(content), name)},
                                 headers=HEADERS, content_type="multipart/form-data")
    assert upload(b"<svg onload='x'></svg>", "logo.svg").status_code == 400
    assert anon.get("/api/public/logo").status_code == 404
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    assert upload(png, "logo.png").status_code == 200
    served = anon.get("/api/public/logo")
    assert served.status_code == 200 and served.content_type == "image/png" and served.data == png
    assert admin.delete("/api/admin/settings/logo").status_code == 200
    assert anon.get("/api/public/logo").status_code == 404


def test_audit_log_records_admin_changes(make_employee, admin):
    code = make_employee("Audited")
    actions = [(a["action"], a["entity_id"], a["admin_username"]) for a in admin.get("/api/admin/audit").json["items"]]
    assert ("employee.create", code, "admin") in actions


# --- Backup and restore ---------------------------------------------------

def test_backup_and_restore_round_trip(app, make_employee, admin):
    import io
    make_employee("Before Backup")
    created = admin.post("/api/admin/backup/create")
    assert created.status_code == 201
    name = created.json["name"]
    download = admin.get(f"/api/admin/backup/files/{name}")
    assert download.status_code == 200 and download.data[:16] == b"SQLite format 3\x00"
    assert admin.get("/api/admin/backup").json["last_backup_at"]

    make_employee("After Backup")
    assert admin.get("/api/admin/employees").json["total"] == 2

    restored = admin.client.post("/api/admin/backup/restore", data={"backup": (io.BytesIO(download.data), "b.db")},
                                 headers=HEADERS, content_type="multipart/form-data")
    assert restored.status_code == 200, restored.json
    assert restored.json["safety_copy"].startswith("timora-pre-restore-")
    assert admin.get("/api/admin/employees").json["total"] == 1
    names = [f["name"] for f in admin.get("/api/admin/backup").json["files"]]
    assert restored.json["safety_copy"] in names

    # The safety copy really holds the data the restore replaced.
    again = admin.post(f"/api/admin/backup/restore/{restored.json['safety_copy']}")
    assert again.status_code == 200
    assert admin.get("/api/admin/employees").json["total"] == 2


def test_restore_rejects_bad_files(admin):
    import io
    def restore(content):
        return admin.client.post("/api/admin/backup/restore", data={"backup": (io.BytesIO(content), "x.db")},
                                 headers=HEADERS, content_type="multipart/form-data")
    assert restore(b"not a database").json["code"] == "INVALID_BACKUP"
    assert restore(b"SQLite format 3\x00" + b"junk" * 100).json["code"] == "INVALID_BACKUP"
    assert admin.post("/api/admin/backup/restore/..%2F..%2Fetc").status_code in (404,)


def test_data_survives_app_restart(tmp_path, clock):
    from backend.app import create_app
    from .conftest import HEADERS as H
    cfg = {"database": str(tmp_path / "t.db"), "backup_dir": str(tmp_path / "b")}
    first = create_app(cfg).test_client()
    first.post("/api/auth/login", json={"username": "admin", "password": DEFAULT_ADMIN_PASSWORD}, headers=H)
    first.post("/api/auth/change-password", json={"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": "Different1!"}, headers=H)
    first.put("/api/admin/settings", json={"allow_unscheduled_clock_in": True}, headers=H)
    code = first.post("/api/admin/employees", json={"name": "Keeper", "department": "D", "designation": "D", "date_of_joining": "2026-01-01"}, headers=H).json["employee_id"]
    first.post("/api/attendance/clock", json={"employee_id": code, "action": "clock_in"})

    restarted = create_app(cfg).test_client()
    state = restarted.get(f"/api/public/employees/{code}/status").json
    assert state["status"] == "clocked_in" and state["action"] == "clock_out"
    assert restarted.post("/api/attendance/clock", json={"employee_id": code, "action": "clock_in"}).json["code"] == "DUPLICATE_CLOCK_IN"
