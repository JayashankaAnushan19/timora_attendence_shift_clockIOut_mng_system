"""A new installation starts with the starter data, once only."""
import json
import sqlite3

from backend.app import create_app
from backend.starter import STARTER_FILE, load_starter_data


def rows(app, sql):
    conn = sqlite3.connect(app.config["TIMORA"]["database"])
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_committed_data_set_is_only_the_manager():
    people = json.loads(STARTER_FILE.read_text(encoding="utf-8"))["employees"]
    assert [p["name"] for p in people] == ["Heyshan Senaviratne"]
    assert people[0]["employee_type"] == "Manager"


def test_new_install_gets_the_manager_on_his_shift(app):
    assert load_starter_data(app) == ["EM001"]
    assert rows(app, "SELECT employee_id, name, employee_type FROM employees") == [
        ("EM001", "Heyshan Senaviratne", "Manager")]
    assert rows(app, "SELECT s.code FROM shift_assignments a JOIN shifts s ON s.id = a.shift_id") == [("MANAGER",)]
    assert sorted(r[0] for r in rows(app, "SELECT code FROM shifts")) == ["DEFAULT", "MANAGER", "MIDNIGHT"]


def test_it_is_only_added_once(tmp_path, clock):
    config = {"database": str(tmp_path / "t.db"), "backup_dir": str(tmp_path / "b")}
    first = create_app(config)
    assert load_starter_data(first) == ["EM001"]
    restarted = create_app(config)
    assert load_starter_data(restarted) == []
    assert rows(restarted, "SELECT COUNT(*) FROM employees") == [(1,)]


def test_existing_system_is_left_alone(app, make_employee):
    code = make_employee("Already Here")
    assert load_starter_data(app) == []
    assert rows(app, "SELECT employee_id FROM employees") == [(code,)]


def test_flag_is_not_an_editable_setting(app, admin):
    load_starter_data(app)
    assert "starter_data_loaded" not in admin.get("/api/admin/settings").json["settings"]
