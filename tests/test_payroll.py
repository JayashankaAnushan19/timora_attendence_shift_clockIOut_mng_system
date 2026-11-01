"""Monthly salary calculation, slips, permissions, and the public company profile."""
import pytest

from .conftest import Api

MONTH = "2026-10"


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


def add_item(api, code, kind, name, amount, month=MONTH, repeats=True):
    return api.post(f"/api/admin/payroll/{code}/items", {"kind": kind, "name": name, "amount": amount, "month": month, "repeats": repeats})


def slip(api, code, month=MONTH):
    response = api.get(f"/api/admin/payroll/{code}?month={month}")
    assert response.status_code == 200, response.json
    return response.json["slip"]


def add_ot(api, code, hours, date="2026-10-20"):
    response = api.post("/api/admin/overtime", {"employee_ids": [code], "work_date": date, "hours": hours, "reason": "Job"})
    assert response.status_code == 201, response.json


def test_full_calculation_line_by_line(make_employee, admin):
    code = make_employee("Paid", basic_salary=100000, ot_rate=500)
    assert add_item(admin, code, "allowance", "Transport", 5000.50).status_code == 201
    assert add_item(admin, code, "allowance", "Meals", 2500).status_code == 201
    assert add_item(admin, code, "deduction", "Loan", 3000).status_code == 201
    assert add_item(admin, code, "deduction", "Insurance", 1000.25).status_code == 201
    add_ot(admin, code, 2.5)

    result = slip(admin, code)
    assert [(e["label"], e["amount"]) for e in result["earnings"]] == [
        ("Basic Salary", 100000), ("Transport", 5000.5), ("Meals", 2500), ("Overtime", 1250)]
    assert [(d["label"], d["amount"]) for d in result["deductions"]] == [("Loan", 3000), ("Insurance", 1000.25)]
    assert result["totals"] == {"basic": 100000, "allowances": 7500.5, "overtime": 1250, "gross": 108750.5,
                                "deductions": 4000.25, "net": 104750.25}
    assert result["overtime_seconds"] == 9000 and result["status"] == "draft"


def test_overtime_only_counts_in_its_own_month_and_when_active(make_employee, admin, clock):
    clock.set("2026-11-15 10:00:00")
    code = make_employee("Paid", ot_rate=100)
    add_ot(admin, code, 1, "2026-10-31")
    add_ot(admin, code, 5, "2026-09-30")
    assert slip(admin, code)["totals"]["overtime"] == 100
    entry = admin.get("/api/admin/overtime?from=2026-10-01").json["items"][0]
    assert admin.post(f"/api/admin/overtime/{entry['id']}/cancel", {"reason": "Mistake"}).status_code == 200
    assert slip(admin, code)["totals"]["overtime"] == 0


def test_overtime_at_different_rates_gets_separate_lines(make_employee, admin):
    code = make_employee("Paid", ot_rate=100)
    add_ot(admin, code, 1)
    assert admin.put(f"/api/admin/payroll/{code}/pay", {"ot_rate": 200}).status_code == 200
    add_ot(admin, code, 2)
    lines = [e for e in slip(admin, code)["earnings"] if e["label"] == "Overtime"]
    assert [(line["minutes"], line["rate"], line["amount"]) for line in lines] == [(60, 100, 100), (120, 200, 400)]


def test_added_overtime_does_not_change_attendance(make_shift, make_employee, clock_action, clock, admin):
    code = make_employee("Worker", make_shift("DAY", "09:00", "18:00"), ot_rate=100)
    clock.set("2026-10-26 09:00:00")
    clock_action(code, "clock_in")
    clock.set("2026-10-26 18:00:00")
    clock_action(code, "clock_out")
    before = admin.get(f"/api/admin/attendance?employee={code}").json["items"][0]
    add_ot(admin, code, 3, "2026-10-26")
    after = admin.get(f"/api/admin/attendance?employee={code}").json["items"][0]
    assert before == after
    assert after["working_seconds"] == 8 * 3600 and after["overtime_seconds"] == 0


def test_one_month_and_repeating_items(make_employee, admin):
    code = make_employee("Paid")
    add_item(admin, code, "allowance", "Monthly", 100)
    add_item(admin, code, "allowance", "Bonus", 50, repeats=False)
    assert slip(admin, code)["totals"]["allowances"] == 150
    assert slip(admin, code, "2026-11")["totals"]["allowances"] == 100
    assert slip(admin, code, "2026-09")["totals"]["allowances"] == 0


def test_removing_a_repeating_item_keeps_earlier_months(make_employee, admin):
    code = make_employee("Paid")
    add_item(admin, code, "allowance", "Monthly", 100, month="2026-09")
    item = admin.get(f"/api/admin/payroll/{code}?month={MONTH}").json["items"][0]
    assert admin.post(f"/api/admin/payroll/items/{item['id']}/remove", {"month": MONTH}).status_code == 200
    assert slip(admin, code, "2026-09")["totals"]["allowances"] == 100
    assert slip(admin, code, MONTH)["totals"]["allowances"] == 0
    assert slip(admin, code, "2026-11")["totals"]["allowances"] == 0


def test_removing_in_its_first_month_removes_it_entirely(make_employee, admin):
    code = make_employee("Paid")
    add_item(admin, code, "deduction", "Wrong", 100)
    item = admin.get(f"/api/admin/payroll/{code}?month={MONTH}").json["items"][0]
    url = f"/api/admin/payroll/items/{item['id']}/remove"
    assert admin.post(url, {"month": MONTH}).status_code == 200
    assert slip(admin, code)["totals"]["deductions"] == 0
    assert admin.post(url, {"month": MONTH}).status_code == 404


def test_january_removal_crosses_the_year(make_employee, admin):
    code = make_employee("Paid")
    add_item(admin, code, "allowance", "Monthly", 100, month="2026-11")
    item = admin.get(f"/api/admin/payroll/{code}?month=2027-01").json["items"][0]
    admin.post(f"/api/admin/payroll/items/{item['id']}/remove", {"month": "2027-01"})
    assert slip(admin, code, "2026-12")["totals"]["allowances"] == 100
    assert slip(admin, code, "2027-01")["totals"]["allowances"] == 0


@pytest.mark.parametrize("body", [
    {"kind": "bonus"}, {"name": ""}, {"amount": 0}, {"amount": -5}, {"amount": "abc"}, {"amount": ""},
    {"amount": "1.234"}, {"month": "2026-13"}, {"month": "october"}, {"month": ""},
])
def test_item_validation(make_employee, admin, body):
    code = make_employee("Paid")
    base = {"kind": "allowance", "name": "X", "amount": 10, "month": MONTH, "repeats": True}
    assert admin.post(f"/api/admin/payroll/{code}/items", {**base, **body}).status_code == 400


def test_negative_net_is_calculated_not_hidden(make_employee, admin):
    code = make_employee("Paid", basic_salary=100)
    add_item(admin, code, "deduction", "Advance", 150)
    assert slip(admin, code)["totals"]["net"] == -50


def test_finalized_slip_never_changes(make_employee, admin):
    code = make_employee("Paid", basic_salary=1000, ot_rate=100)
    add_item(admin, code, "allowance", "Transport", 200)
    assert admin.post(f"/api/admin/payroll/{code}/finalize", {"month": MONTH}).status_code == 200

    assert admin.put(f"/api/admin/payroll/{code}/pay", {"basic_salary": 9999}).status_code == 200
    add_ot(admin, code, 5)
    admin.put(f"/api/admin/employees/{code}", {"name": "Renamed", "department": "D", "designation": "D"})
    result = slip(admin, code)
    assert result["status"] == "final" and result["finalized_by"] == "admin"
    assert result["totals"]["net"] == 1200 and result["employee"]["name"] == "Paid"
    listed = admin.get(f"/api/admin/payroll?month={MONTH}").json
    assert listed["items"][0]["net"] == 1200 and listed["items"][0]["status"] == "final"

    assert add_item(admin, code, "allowance", "Late", 1).json["code"] == "SLIP_FINALIZED"
    assert admin.post(f"/api/admin/payroll/{code}/finalize", {"month": MONTH}).json["code"] == "SLIP_FINALIZED"
    # Next month is unaffected by this month's lock and uses the new salary.
    assert add_item(admin, code, "allowance", "Next", 1, month="2026-11").status_code == 201
    assert slip(admin, code, "2026-11")["totals"]["basic"] == 9999

    assert admin.post(f"/api/admin/payroll/{code}/reopen", {"month": MONTH}).status_code == 200
    assert slip(admin, code)["totals"]["basic"] == 9999
    assert admin.post(f"/api/admin/payroll/{code}/reopen", {"month": MONTH}).json["code"] == "SLIP_NOT_FINAL"


def test_month_list_totals_and_who_appears(make_employee, admin, clock):
    a = make_employee("Alice", basic_salary=1000, joined="2026-01-01")
    make_employee("Bob", basic_salary="500.50", joined="2026-01-01")
    make_employee("Future", basic_salary=7000, joined="2026-12-01")
    gone = make_employee("Gone", basic_salary=300, joined="2026-01-01")
    clock.set("2026-11-15 10:00:00")
    admin.post(f"/api/admin/employees/{gone}/deactivate")
    add_item(admin, a, "deduction", "Loan", 100)

    october = admin.get(f"/api/admin/payroll?month={MONTH}").json
    assert sorted(i["name"] for i in october["items"]) == ["Alice", "Bob", "Gone"]
    assert october["totals"]["basic"] == 1800.5 and october["totals"]["net"] == 1700.5
    december = admin.get("/api/admin/payroll?month=2026-12").json
    assert sorted(i["name"] for i in december["items"]) == ["Alice", "Bob", "Future"]
    assert admin.get(f"/api/admin/payroll?month={MONTH}&q=ali").json["total"] == 1
    assert admin.get("/api/admin/payroll?month=nope").status_code == 400


def test_slip_includes_attendance_for_the_month(make_shift, make_employee, clock_action, clock, admin):
    code = make_employee("Worker", make_shift("DAY", "09:00", "18:00", working_days=[0]), joined="2026-10-01")
    clock.set("2026-10-26 09:30:00")  # a Monday, and late
    clock_action(code, "clock_in")
    clock.set("2026-10-26 18:00:00")
    clock_action(code, "clock_out")
    clock.set("2026-10-31 12:00:00")
    attendance = slip(admin, code)["attendance"]
    assert attendance["present"] == 1 and attendance["late"] == 1
    assert attendance["absent"] == 3  # the other Mondays in October 2026
    assert attendance["worked_seconds"] == 7.5 * 3600


@pytest.mark.parametrize("role", ["Manager", "Attendance Manager", "Viewer"])
def test_only_payroll_roles_can_reach_salary(make_employee, login_as, role):
    code = make_employee("Paid", basic_salary=1000)
    api = login_as(role, "tester")
    assert api.get(f"/api/admin/payroll?month={MONTH}").status_code == 403
    assert api.get(f"/api/admin/payroll/{code}?month={MONTH}").status_code == 403
    assert add_item(api, code, "allowance", "X", 1).status_code == 403
    assert api.put(f"/api/admin/payroll/{code}/pay", {"basic_salary": 1}).status_code == 403
    assert api.post(f"/api/admin/payroll/{code}/finalize", {"month": MONTH}).status_code == 403
    for page in ("salary", "payslip"):
        assert api.get(f"/admin/{page}").status_code == 302


def test_admin_role_can_reach_salary(make_employee, login_as):
    api = login_as("Admin", "adam")
    code = make_employee("Paid", basic_salary=1000)
    assert api.get(f"/api/admin/payroll/{code}?month={MONTH}").status_code == 200
    assert api.get("/admin/salary").status_code == 200
    assert api.get("/admin/payslip").status_code == 200


def test_not_logged_in_gets_nothing(make_employee, anon):
    code = make_employee("Paid", basic_salary=1000)
    assert anon.get(f"/api/admin/payroll/{code}?month={MONTH}").status_code == 401
    assert anon.get("/admin/payslip").status_code == 302


def test_audit_has_no_salary_amounts(make_employee, admin):
    code = make_employee("Paid")
    admin.put(f"/api/admin/payroll/{code}/pay", {"basic_salary": 91234})
    add_item(admin, code, "allowance", "Transport", 54321)
    log = admin.get("/api/admin/audit").data
    assert b"91234" not in log and b"54321" not in log and b"payroll.pay_update" in log


# --- Company profile ------------------------------------------------------

def test_company_profile_is_public_and_appears_on_the_slip(make_employee, admin, anon):
    assert admin.put("/api/admin/settings", {
        "company_name": "Acme", "company_tagline": "We build", "company_address": "1 Main St",
        "company_phone": "+94 11 234 5678", "company_email": "hi@acme.example", "company_website": "acme.example",
        "company_registration": "PV 123", "company_about": "A company.",
    }).status_code == 200
    profile = anon.get("/api/public/config").json["config"]["company_profile"]
    assert profile["website"] == "https://acme.example" and profile["registration"] == "PV 123"
    code = make_employee("Paid")
    company = admin.get(f"/api/admin/payroll/{code}?month={MONTH}").json["company"]
    assert company["name"] == "Acme" and company["address"] == "1 Main St"


@pytest.mark.parametrize("field,value", [
    ("company_website", "javascript:alert(1)"), ("company_website", "data:text/html,x"),
    ("company_website", "has space.com"), ("company_email", "not-an-email"),
    ("company_about", "x" * 401), ("company_phone", 5),
])
def test_company_profile_validation(admin, field, value):
    assert admin.put("/api/admin/settings", {field: value}).status_code == 400


def test_company_profile_can_be_blank(admin):
    assert admin.put("/api/admin/settings", {"company_website": "", "company_email": ""}).status_code == 200
