"""The fixed app name, and Super-Admin-only purge, cleanup and reset."""
import sqlite3

import pytest

from backend.database import DEFAULT_ADMIN_PASSWORD
from .conftest import NEW_PASSWORD, Api

EXECUTE = "/api/admin/critical/execute"
PREVIEW = "/api/admin/critical/preview"
RESET_TABLES_NOTE = "every table must be cleared by Reset, or a new table would survive it"


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


@pytest.fixture
def data(make_shift, make_employee, clock_action, clock, admin):
    """Two employees with attendance, overtime, salary items, a slip, leave and a holiday."""
    shift = make_shift("DAY", "09:00", "18:00")
    one, two = make_employee("One", shift, ot_rate=100), make_employee("Two", shift, ot_rate=100)
    for day in ("26", "27"):
        clock.set(f"2026-10-{day} 09:00:00")
        clock_action(one, "clock_in")
        clock_action(two, "clock_in")
        clock.set(f"2026-10-{day} 18:00:00")
        clock_action(one, "clock_out")
        clock_action(two, "clock_out")
    clock.set("2026-10-28 12:00:00")
    record = admin.get(f"/api/admin/attendance?employee={one}").json["items"][0]["id"]
    assert admin.put(f"/api/admin/attendance/{record}", {"status": "Late", "reason": "Test"}).status_code == 200
    for code in (one, two):
        assert admin.post("/api/admin/overtime", {"employee_ids": [code], "work_date": "2026-10-26", "hours": 1, "reason": "x"}).status_code == 201
        assert admin.post(f"/api/admin/payroll/{code}/items", {"kind": "allowance", "name": "Transport", "amount": 10, "month": "2026-10", "repeats": True}).status_code == 201
    assert admin.post(f"/api/admin/payroll/{one}/finalize", {"month": "2026-10"}).status_code == 200
    assert admin.post("/api/admin/leave", {"employee_id": two, "start_date": "2026-10-28", "end_date": "2026-10-28"}).status_code == 201
    assert admin.post("/api/admin/holidays", {"date": "2026-12-25", "name": "Holiday"}).status_code == 201
    return {"one": one, "two": two, "shift": shift}


def count(app, table, where="1 = 1"):
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    finally:
        conn.close()


def run(api, password=NEW_PASSWORD, phrase="PURGE", **body):
    return api.post(EXECUTE, {**body, "password": password, "phrase": phrase})


# --- Fixed application name -----------------------------------------------

def test_app_name_is_fixed_and_not_a_setting(admin, anon):
    assert anon.get("/api/public/config").json["config"]["app_name"] == "Timora v1.0"
    assert admin.get("/api/admin/me").json["display"]["app_name"] == "Timora v1.0"
    assert "app_name" not in admin.get("/api/admin/settings").json["settings"]
    assert admin.put("/api/admin/settings", {"app_name": "Other"}).status_code == 400


def test_old_saved_app_name_is_ignored(app, anon):
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    conn.execute("INSERT INTO system_settings(key, value, updated_at) VALUES ('app_name', '\"Old\"', 'x')")
    conn.commit()
    conn.close()
    assert anon.get("/api/public/config").json["config"]["app_name"] == "Timora v1.0"


# --- Access ---------------------------------------------------------------

@pytest.mark.parametrize("role", ["Admin", "Manager", "Attendance Manager", "Viewer"])
def test_only_super_admin_has_access(login_as, role):
    api = login_as(role, "tester")
    assert api.get("/api/admin/critical/tables").status_code == 403
    assert api.get("/api/admin/critical/rows?table=attendance").status_code == 403
    assert api.post(PREVIEW, {"scope": "cleanup"}).status_code == 403
    assert run(api, "Own-pass-99", scope="cleanup").status_code == 403
    assert run(api, "Own-pass-99", "RESET", scope="reset").status_code == 403
    assert api.get("/admin/critical").status_code == 302


def test_a_second_super_admin_has_access(login_as):
    assert login_as("Super Admin", "second").get("/api/admin/critical/tables").status_code == 200


def test_not_logged_in_has_no_access(anon):
    assert anon.get("/api/admin/critical/tables").status_code == 401
    assert anon.post(EXECUTE, {"scope": "reset"}).status_code == 401
    assert anon.get("/admin/critical").status_code == 302


def test_permission_cannot_be_given_to_another_role(admin):
    roles = admin.get("/api/admin/roles").json
    assert "system.critical" not in roles["all_permissions"]
    viewer = next(r for r in roles["items"] if r["name"] == "Viewer")
    assert admin.put(f"/api/admin/roles/{viewer['id']}", {"permissions": ["system.critical"]}).status_code == 400


def test_role_check_holds_even_if_the_permission_is_forced(app, login_as):
    api = login_as("Viewer", "vera")
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    conn.execute("UPDATE roles SET permissions = '[\"system.critical\"]' WHERE name = 'Viewer'")
    conn.commit()
    conn.close()
    assert api.get("/api/admin/critical/tables").status_code == 403


# --- Safeguards -----------------------------------------------------------

def test_wrong_password_or_phrase_changes_nothing(app, admin, data):
    before = count(app, "attendance")
    for response in (
        run(admin, "wrong", scope="cleanup"),
        run(admin, phrase="purge", scope="cleanup"),
        run(admin, phrase="", scope="cleanup"),
        admin.post(EXECUTE, {"scope": "cleanup"}),
        run(admin, phrase="PURGE", scope="reset"),
    ):
        assert response.status_code in (400, 403)
        assert "Nothing was changed" in response.json["message"]
    assert count(app, "attendance") == before
    assert not [f for f in admin.get("/api/admin/backup").json["files"] if "pre-" in f["name"]]


def test_preview_reports_and_changes_nothing(app, admin, data):
    response = admin.post(PREVIEW, {"scope": "table", "table": "employees", "mode": "selected", "ids": [1]}).json
    assert response["phrase"] == "PURGE"
    impact = {r["label"]: r["count"] for r in response["impact"]}
    assert impact["Employees"] == 1 and impact["Attendance Records"] == 2 and impact["Overtime Entries"] == 1
    assert count(app, "employees") == 2 and count(app, "attendance") == 4
    assert admin.post(PREVIEW, {"scope": "reset"}).json["phrase"] == "RESET"


def test_every_operation_saves_a_restorable_backup_first(admin, data):
    response = run(admin, scope="table", table="attendance", mode="filtered")
    assert response.status_code == 200
    name = response.json["safety_copy"]
    assert name.startswith("timora-pre-purge-") and name in [f["name"] for f in admin.get("/api/admin/backup").json["files"]]
    assert admin.get("/api/admin/attendance").json["total"] == 0
    assert admin.post(f"/api/admin/backup/restore/{name}").status_code == 200
    assert admin.get("/api/admin/attendance").json["total"] == 4


def test_history_protection_returns_after_a_purge(app, admin, data):
    assert run(admin, scope="table", table="holidays", mode="filtered").status_code == 200
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    for sql in ("DELETE FROM attendance", "DELETE FROM attendance_adjustments", "DELETE FROM employees",
                "DELETE FROM overtime_entries", "DELETE FROM audit_logs"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    conn.close()


def test_a_failure_part_way_changes_nothing_and_keeps_protection(app, admin, data, monkeypatch):
    from backend import critical
    monkeypatch.setitem(critical.STEPS, "holidays", [
        ("Holidays", "holidays", f"id IN {critical.IDS}"),
        ("Broken", "no_such_table", "1 = 1"),
    ])
    monkeypatch.setitem(critical.STEPS, "attendance", critical.STEPS["attendance"] + [("Broken", "no_such_table", "1 = 1")])
    response = run(admin, scope="table", table="attendance", mode="filtered")
    assert response.status_code >= 500 and response.json["success"] is False
    assert count(app, "attendance") == 4 and count(app, "attendance_adjustments") == 1
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM attendance")
    conn.close()


def test_every_purge_is_recorded_even_after_clearing_the_audit_log(admin, data):
    assert run(admin, scope="table", table="audit_logs", mode="filtered").status_code == 200
    items = admin.get("/api/admin/audit").json["items"]
    assert any(i["action"] == "critical.table" and i["admin_username"] == "admin" for i in items)


def test_nothing_to_purge_is_refused_without_a_backup(admin):
    response = run(admin, scope="table", table="holidays", mode="filtered")
    assert response.status_code == 400 and response.json["code"] == "NOTHING_TO_PURGE"
    assert admin.get("/api/admin/backup").json["files"] == []


@pytest.mark.parametrize("body", [
    {"scope": "other"}, {"scope": "table", "table": "administrators", "mode": "filtered"},
    {"scope": "table", "table": "system_settings", "mode": "filtered"},
    {"scope": "table", "table": "attendance", "mode": "other"},
    {"scope": "table", "table": "attendance", "mode": "selected", "ids": []},
    {"scope": "table", "table": "attendance", "mode": "selected", "ids": ["1 OR 1=1"]},
    {"scope": "table", "table": "attendance", "mode": "filtered", "from": "' OR 1=1 --"},
    {"scope": "table", "table": "attendance", "mode": "filtered", "from": "2026-10-10", "to": "2026-10-01"},
])
def test_bad_requests_are_refused(admin, data, body):
    assert admin.post(PREVIEW, body).status_code == 400
    assert run(admin, **body).status_code == 400


# --- Purging --------------------------------------------------------------

def test_purge_selected_rows_only(app, admin, data):
    rows = admin.get("/api/admin/critical/rows?table=attendance").json["items"]
    ids = [rows[0]["id"], rows[1]["id"]]
    assert run(admin, scope="table", table="attendance", mode="selected", ids=ids).status_code == 200
    assert count(app, "attendance") == 2
    remaining = {r["id"] for r in admin.get("/api/admin/critical/rows?table=attendance").json["items"]}
    assert not remaining & set(ids)


def test_selected_ids_that_do_not_exist_are_ignored(app, admin, data):
    response = run(admin, scope="table", table="holidays", mode="selected", ids=[999999])
    assert response.json["code"] == "NOTHING_TO_PURGE"


def test_purge_by_date_range_and_employee(app, admin, data):
    body = dict(scope="table", table="attendance", mode="filtered", employee=data["one"], **{"from": "2026-10-26", "to": "2026-10-26"})
    assert admin.post(PREVIEW, body).json["impact"][-1] == {"label": "Attendance Records", "count": 1}
    assert run(admin, **body).status_code == 200
    assert count(app, "attendance") == 3


def test_purge_whole_table_removes_children_only(app, admin, data):
    assert run(admin, scope="table", table="attendance", mode="filtered").status_code == 200
    assert count(app, "attendance") == 0 and count(app, "attendance_adjustments") == 0
    assert count(app, "employees") == 2 and count(app, "overtime_entries") == 2 and count(app, "salary_items") == 2


@pytest.mark.parametrize("table,db_table", [
    ("overtime", "overtime_entries"), ("salary_items", "salary_items"), ("salary_slips", "salary_slips"),
    ("leave", "leave_records"), ("holidays", "holidays"), ("assignments", "shift_assignments"),
])
def test_each_table_can_be_purged(app, admin, data, table, db_table):
    assert count(app, db_table) > 0
    assert run(admin, scope="table", table=table, mode="filtered").status_code == 200
    assert count(app, db_table) == 0
    assert count(app, "employees") == 2


def test_purging_an_employee_removes_all_their_records_and_keeps_the_id_unused(app, admin, data, make_employee):
    pk = admin.get("/api/admin/critical/rows?table=employees").json["items"]
    target = next(r["id"] for r in pk if r["employee_id"] == data["one"])
    assert run(admin, scope="table", table="employees", mode="selected", ids=[target]).status_code == 200
    assert count(app, "employees") == 1
    for table in ("attendance", "overtime_entries", "salary_items", "shift_assignments"):
        assert count(app, table, f"employee_pk = {target}") == 0
    assert count(app, "salary_slips") == 0 and count(app, "attendance_adjustments") == 0
    assert count(app, "attendance") == 2
    assert make_employee("Next") == "EM003"


def test_purging_shifts_keeps_attendance_and_leaves_a_shift(app, admin, data):
    assert run(admin, scope="table", table="shifts", mode="filtered").status_code == 200
    assert count(app, "attendance") == 4 and count(app, "attendance", "shift_id IS NOT NULL") == 0
    assert count(app, "shift_assignments") == 0
    assert sorted(s["code"] for s in admin.get("/api/admin/shifts").json["items"]) == ["DEFAULT", "MANAGER", "MIDNIGHT"]


# --- Full cleanup ---------------------------------------------------------

def test_full_cleanup_keeps_setup_and_login(app, admin, data, make_employee):
    assert admin.put("/api/admin/settings", {"company_name": "Acme"}).status_code == 200
    assert run(admin, scope="cleanup").status_code == 200
    for table in ("employees", "attendance", "attendance_adjustments", "overtime_entries", "salary_items",
                  "salary_slips", "leave_records", "shift_assignments", "holidays", "clock_requests"):
        assert count(app, table) == 0, table
    assert admin.get("/api/admin/dashboard").status_code == 200  # still logged in
    assert admin.get("/api/admin/settings").json["settings"]["company_name"] == "Acme"
    assert "DAY" in [s["code"] for s in admin.get("/api/admin/shifts").json["items"]]
    assert make_employee("After") == "EM003"  # IDs are not reused


# --- Reset ----------------------------------------------------------------

def test_reset_needs_its_own_word(app, admin, data):
    assert run(admin, phrase="PURGE", scope="reset").status_code == 400
    assert count(app, "employees") == 2


def test_reset_returns_to_a_new_installation(app, admin, login_as, data):
    other = login_as("Admin", "adam")
    assert admin.put("/api/admin/settings", {"company_name": "Acme", "timezone": "Asia/Colombo"}).status_code == 200
    response = run(admin, phrase="RESET", scope="reset")
    assert response.status_code == 200 and response.json["logged_out"] is True
    assert response.json["safety_copy"].startswith("timora-pre-reset-")

    assert admin.get("/api/admin/dashboard").status_code == 401
    assert other.get("/api/admin/dashboard").status_code == 401
    assert admin.login("adam", "Own-pass-99").status_code == 401
    assert admin.login("admin", NEW_PASSWORD).status_code == 401

    login = admin.login("admin", DEFAULT_ADMIN_PASSWORD)
    assert login.status_code == 200 and login.json["admin"]["must_change_password"] is True
    assert login.json["admin"]["role"] == "Super Admin"
    assert admin.post("/api/auth/change-password", {"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200

    settings = admin.get("/api/admin/settings").json["settings"]
    assert settings["company_name"] == "My Company" and settings["timezone"] == "UTC"
    employees = admin.get("/api/admin/employees").json["items"]
    assert [(e["employee_id"], e["name"]) for e in employees] == [("EM001", "Heyshan Senaviratne")]
    assert sorted(s["code"] for s in admin.get("/api/admin/shifts").json["items"]) == ["DEFAULT", "MANAGER", "MIDNIGHT"]
    assert len(admin.get("/api/admin/roles").json["items"]) == 5
    for table in ("attendance", "overtime_entries", "salary_items", "salary_slips", "holidays", "leave_records"):
        assert count(app, table) == 0, table
    actions = [a["action"] for a in admin.get("/api/admin/audit").json["items"]]
    assert "critical.reset" in actions


def test_reset_can_be_undone_from_its_backup(admin, data):
    name = run(admin, phrase="RESET", scope="reset").json["safety_copy"]
    admin.login("admin", DEFAULT_ADMIN_PASSWORD)
    admin.post("/api/auth/change-password", {"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": "Temporary-22"})
    assert admin.post(f"/api/admin/backup/restore/{name}").status_code == 200
    assert admin.login("admin", NEW_PASSWORD).status_code == 200
    assert admin.get("/api/admin/employees").json["total"] == 2
    assert admin.get("/api/admin/attendance").json["total"] == 4


def test_reset_clears_every_table(app):
    from backend.critical import RESET_STEPS
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    conn.close()
    assert tables - {step[1] for step in RESET_STEPS} == {"clock_requests"}, RESET_TABLES_NOTE
