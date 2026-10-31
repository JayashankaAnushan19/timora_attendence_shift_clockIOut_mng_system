"""Clock-in/out rules. Business timezone is UTC unless a test changes it."""
import threading

from backend.app import create_app


def test_ids_are_sequential_and_searchable(make_employee, anon):
    assert make_employee("John Smith") == "EM001"
    assert make_employee("Jane Doe") == "EM002"
    by_id = anon.get("/api/public/employees/search?q=EM002").json["items"]
    assert by_id == [{"employee_id": "EM002", "name": "Jane Doe"}]
    by_name = anon.get("/api/public/employees/search?q=john").json["items"]
    assert by_name == [{"employee_id": "EM001", "name": "John Smith"}]


def test_dropdown_lists_all_active_employees_past_any_limit(make_employee, admin, anon):
    codes = [make_employee(f"Person {i:02d}") for i in range(12)]
    admin.post(f"/api/admin/employees/{codes[0]}/deactivate")
    listed = anon.get("/api/public/employees/search?q=").json["items"]
    assert [e["name"] for e in listed] == [f"Person {i:02d}" for i in range(1, 12)]


def test_search_exposes_only_id_and_name(make_employee, anon):
    make_employee("John Smith", email="john@example.com", contact_number="0771234567")
    assert set(anon.get("/api/public/employees/search?q=john").json["items"][0]) == {"employee_id", "name"}


def test_search_wildcards_are_literal(make_employee, anon):
    make_employee("John Smith")
    assert anon.get("/api/public/employees/search?q=%25").json["items"] == []


def test_clock_in_and_out_response_shape(make_shift, make_employee, clock_action, status_of, clock):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 08:58:32")
    assert status_of(code)["status"] == "not_clocked_in"
    assert status_of(code)["action"] == "clock_in"

    r = clock_action(code, "clock_in")
    assert r.status_code == 200
    body = r.json
    assert body["success"] is True and body["employee_id"] == code and body["employee_name"] == "John Smith"
    assert body["action"] == "clock_in" and body["status"] == "clocked_in"
    assert body["timestamp"] == "2026-10-26T08:58:32Z"
    assert body["message"] == "Clock-in successful"
    assert status_of(code)["status"] == "clocked_in"
    assert status_of(code)["action"] == "clock_out"

    clock.set("2026-10-26 18:00:00")
    r = clock_action(code, "clock_out")
    assert r.status_code == 200 and r.json["status"] == "clocked_out"
    assert status_of(code)["status"] == "shift_completed"


def test_duplicates_are_refused(make_shift, make_employee, clock_action, clock):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 09:00:00")
    assert clock_action(code, "clock_out").json["code"] == "NOT_CLOCKED_IN"
    assert clock_action(code, "clock_in").status_code == 200
    second = clock_action(code, "clock_in")
    assert second.status_code == 409 and second.json["code"] == "DUPLICATE_CLOCK_IN"
    clock.set("2026-10-26 17:00:00")
    assert clock_action(code, "clock_out").status_code == 200
    again = clock_action(code, "clock_out")
    assert again.status_code == 409 and again.json["code"] == "DUPLICATE_CLOCK_OUT"
    assert clock_action(code, "clock_in").status_code == 409  # one session per shift by default


def test_retry_with_same_request_id_is_not_applied_twice(make_shift, make_employee, clock_action, clock, admin):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 09:00:00")
    first = clock_action(code, "clock_in", "retry-token-1")
    retry = clock_action(code, "clock_in", "retry-token-1")
    assert first.status_code == retry.status_code == 200
    assert retry.json["replayed"] is True and retry.json["timestamp"] == first.json["timestamp"]
    assert admin.get("/api/admin/attendance").json["total"] == 1


def test_double_click_clock_out_window(make_shift, make_employee, clock_action, clock):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.advance(seconds=5)
    assert clock_action(code, "clock_out").json["code"] == "DUPLICATE_WINDOW"


def test_grace_period_on_time_and_late(make_shift, make_employee, clock_action, clock, admin):
    shift = make_shift("DAY", "09:00", "18:00")
    on_time = make_employee("On Time", shift)
    edge = make_employee("Edge Case", shift)
    late = make_employee("Late Person", shift)

    clock.set("2026-10-26 09:05:00")
    assert clock_action(on_time, "clock_in").json["attendance_status"] == "Present"
    clock.set("2026-10-26 09:10:00")
    assert clock_action(edge, "clock_in").json["attendance_status"] == "Present"
    clock.set("2026-10-26 09:15:00")
    response = clock_action(late, "clock_in").json
    assert response["attendance_status"] == "Late" and response["note"] == "Late by 15m"
    row = admin.get(f"/api/admin/attendance?employee={late}").json["items"][0]
    assert row["late_seconds"] == 15 * 60


def test_early_arrival_early_departure_overtime_and_break(make_shift, make_employee, clock_action, clock, admin):
    shift = make_shift("DAY", "09:00", "18:00")
    early = make_employee("Leaves Early", shift)
    extra = make_employee("Stays Late", shift)

    clock.set("2026-10-26 08:45:00")
    assert clock_action(early, "clock_in").json["note"] == "Early by 15m"
    assert clock_action(extra, "clock_in").status_code == 200

    clock.set("2026-10-26 16:00:00")
    assert clock_action(early, "clock_out").json["attendance_status"] == "Early Departure"
    row = admin.get(f"/api/admin/attendance?employee={early}").json["items"][0]
    assert row["early_departure_seconds"] == 2 * 3600
    assert row["early_arrival_seconds"] == 15 * 60
    assert row["break_seconds"] == 3600
    assert row["working_seconds"] == 7 * 3600 + 15 * 60 - 3600

    clock.set("2026-10-26 19:30:00")
    assert clock_action(extra, "clock_out").json["attendance_status"] == "Overtime"
    row = admin.get(f"/api/admin/attendance?employee={extra}").json["items"][0]
    assert row["overtime_seconds"] == 90 * 60


def test_clock_in_window(make_shift, make_employee, clock_action, status_of, clock):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00", early_clock_in_minutes=30))
    clock.set("2026-10-26 08:00:00")
    state = status_of(code)
    assert state["status"] == "shift_not_started" and state["action"] is None
    assert clock_action(code, "clock_in").json["code"] == "CLOCK_IN_NOT_ALLOWED"
    clock.set("2026-10-26 08:30:00")
    assert status_of(code)["action"] == "clock_in"
    clock.set("2026-10-26 18:30:00")
    assert status_of(code)["status"] == "shift_completed"
    assert clock_action(code, "clock_in").status_code == 409


def test_not_scheduled_cases(make_shift, make_employee, status_of, clock, admin):
    weekdays = make_shift("WK", "09:00", "18:00", working_days=[0, 1, 2, 3, 4])
    no_shift = make_employee("No Shift")
    worker = make_employee("Worker", weekdays)
    clock.set("2026-10-26 10:00:00")
    assert status_of(no_shift)["status"] == "not_scheduled"
    assert status_of(worker)["status"] == "not_clocked_in"

    clock.set("2026-10-31 10:00:00")  # Saturday
    state = status_of(worker)
    assert state["status"] == "not_scheduled" and "not a working day" in state["message"]

    assert admin.post("/api/admin/holidays", {"date": "2026-10-27", "name": "Founders Day"}).status_code == 201
    clock.set("2026-10-27 10:00:00")
    assert "Founders Day" in status_of(worker)["message"]

    assert admin.post("/api/admin/leave", {"employee_id": worker, "start_date": "2026-10-28",
                                           "end_date": "2026-10-28", "leave_type": "Sick"}).status_code == 201
    clock.set("2026-10-28 10:00:00")
    assert "Sick leave" in status_of(worker)["message"]


def test_overnight_shift_belongs_to_start_date(make_shift, make_employee, clock_action, status_of, clock, admin):
    code = make_employee(shift_id=make_shift("NGT", "22:00", "07:00"))
    clock.set("2026-10-26 22:05:00")
    assert clock_action(code, "clock_in").json["attendance_status"] == "Present"
    clock.set("2026-10-27 03:00:00")
    assert status_of(code)["status"] == "clocked_in"
    clock.set("2026-10-27 07:30:00")
    assert clock_action(code, "clock_out").status_code == 200

    row = admin.get(f"/api/admin/attendance?employee={code}").json["items"][0]
    assert row["attendance_date"] == "2026-10-26"
    assert row["working_seconds"] == 9 * 3600 + 25 * 60 - 3600
    assert row["overtime_seconds"] == 30 * 60
    assert row["early_departure_seconds"] == 0
    assert status_of(code)["status"] == "shift_not_started"  # tonight's shift is next


def test_overnight_clock_in_after_midnight(make_shift, make_employee, clock_action, clock, admin):
    code = make_employee(shift_id=make_shift("NGT", "22:00", "07:00"))
    clock.set("2026-10-27 01:00:00")
    response = clock_action(code, "clock_in").json
    assert response["attendance_status"] == "Late"
    row = admin.get(f"/api/admin/attendance?employee={code}").json["items"][0]
    assert row["attendance_date"] == "2026-10-26" and row["late_seconds"] == 3 * 3600


def test_business_timezone_controls_rules(make_shift, make_employee, clock_action, clock, admin):
    assert admin.put("/api/admin/settings", {"timezone": "Asia/Colombo"}).status_code == 200  # UTC+5:30
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 03:30:00")  # 09:00 in Colombo
    response = clock_action(code, "clock_in").json
    assert response["attendance_status"] == "Present" and response["timestamp"] == "2026-10-26T03:30:00Z"
    assert response["local_time"] == "09:00:00 AM"


def test_missing_clock_out_becomes_incomplete(make_shift, make_employee, clock_action, status_of, clock, admin):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00", late_clock_out_minutes=60))
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-27 08:30:00")  # next morning; must not offer yesterday's clock-out
    state = status_of(code)
    assert state["status"] == "not_clocked_in" and state["action"] == "clock_in"
    row = admin.get(f"/api/admin/attendance?employee={code}&from=2026-10-26&to=2026-10-26").json["items"][0]
    assert row["status"] == "Incomplete" and row["clock_out"] is None and row["is_open"] is False
    detail = admin.get(f"/api/admin/attendance/{row['id']}").json
    assert detail["adjustments"][0]["admin_username"] == "system"
    assert clock_action(code, "clock_in").status_code == 200


def test_deactivated_employee_cannot_use_terminal(make_shift, make_employee, clock_action, anon, admin, clock):
    code = make_employee(shift_id=make_shift("DAY", "09:00", "18:00"))
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    assert admin.post(f"/api/admin/employees/{code}/deactivate").json["code"] == "EMPLOYEE_CLOCKED_IN"
    clock.set("2026-10-26 17:00:00")
    clock_action(code, "clock_out")
    assert admin.post(f"/api/admin/employees/{code}/deactivate").status_code == 200
    assert anon.get("/api/public/employees/search?q=john").json["items"] == []
    assert anon.get(f"/api/public/employees/{code}/status").status_code == 404
    clock.set("2026-10-27 09:00:00")
    assert clock_action(code, "clock_in").status_code == 404
    assert admin.get(f"/api/admin/attendance?employee={code}").json["total"] == 1  # history intact


def test_simultaneous_clock_ins_create_one_session(tmp_path, clock):
    """Real threads against a real file: exactly one request may win."""
    from backend.database import DEFAULT_ADMIN_PASSWORD
    app = create_app({"database": str(tmp_path / "t.db"), "backup_dir": str(tmp_path / "b")})
    headers = {"X-Timora-Request": "1"}
    c = app.test_client()
    c.post("/api/auth/login", json={"username": "admin", "password": DEFAULT_ADMIN_PASSWORD}, headers=headers)
    c.post("/api/auth/change-password", json={"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": "Different1!"}, headers=headers)
    code = c.post("/api/admin/employees", json={"name": "Racer", "department": "D", "designation": "D", "date_of_joining": "2026-01-01"}, headers=headers).json["employee_id"]
    assert c.put("/api/admin/settings", json={"allow_unscheduled_clock_in": True}, headers=headers).status_code == 200

    results = []
    barrier = threading.Barrier(8)

    def worker():
        client = app.test_client()
        barrier.wait()
        results.append(client.post("/api/attendance/clock", json={"employee_id": code, "action": "clock_in"}).status_code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200] + [409] * 7
    assert c.get(f"/api/admin/attendance?employee={code}").json["total"] == 1


def test_many_employees_clock_in_at_once(tmp_path, clock):
    from backend.database import DEFAULT_ADMIN_PASSWORD
    app = create_app({"database": str(tmp_path / "t.db"), "backup_dir": str(tmp_path / "b")})
    headers = {"X-Timora-Request": "1"}
    c = app.test_client()
    c.post("/api/auth/login", json={"username": "admin", "password": DEFAULT_ADMIN_PASSWORD}, headers=headers)
    c.post("/api/auth/change-password", json={"current_password": DEFAULT_ADMIN_PASSWORD, "new_password": "Different1!"}, headers=headers)
    assert c.put("/api/admin/settings", json={"allow_unscheduled_clock_in": True}, headers=headers).status_code == 200
    codes = [c.post("/api/admin/employees", json={"name": f"Emp {i}", "department": "D", "designation": "D", "date_of_joining": "2026-01-01"}, headers=headers).json["employee_id"] for i in range(10)]

    results = []

    def worker(code):
        results.append(app.test_client().post("/api/attendance/clock", json={"employee_id": code, "action": "clock_in"}).status_code)

    threads = [threading.Thread(target=worker, args=(code,)) for code in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 10
