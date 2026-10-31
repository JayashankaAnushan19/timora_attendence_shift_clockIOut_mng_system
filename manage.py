"""Maintenance commands.

  python manage.py init                       Create the database
  python manage.py reset-password <username>  Set a new password from the terminal
  python manage.py seed-demo                  Add sample employees (only into an empty system)
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

from backend import database, timeutil
from backend.config import load_config

DEMO_SHIFT_CODE = "DEMO"

# name, department, designation, shift code, date of joining
DEMO_EMPLOYEES = [
    ("John Smith", "Operations", "Supervisor", DEMO_SHIFT_CODE, "2025-03-10"),
    ("Amara Perera", "Finance", "Accountant", DEMO_SHIFT_CODE, "2025-05-19"),
    ("Liam Chen", "Warehouse", "Picker", DEMO_SHIFT_CODE, "2025-07-01"),
    ("Sofia Garcia", "Security", "Guard", "NIGHT", "2025-08-14"),
    ("Noah Khan", "Sales", "Executive", "FLEX", "2025-09-02"),
    ("Priya Nair", "Human Resources", "HR Officer", "MORNING", "2025-10-06"),
    ("Daniel Fernando", "Operations", "Coordinator", "MORNING", "2025-11-17"),
    ("Mei Tanaka", "Finance", "Analyst", "MORNING", "2026-01-12"),
    ("Omar Hassan", "Warehouse", "Forklift Operator", "EVENING", "2026-02-23"),
    ("Grace Mensah", "Customer Support", "Support Agent", "EVENING", "2026-04-06"),
]


def cmd_init(cfg):
    database.init_db(cfg["database"])
    print(f"Database ready: {cfg['database']}")


def cmd_reset_password(cfg, username):
    database.init_db(cfg["database"])
    conn = database.connect(cfg["database"])
    try:
        row = conn.execute("SELECT id FROM administrators WHERE username = ?", (username,)).fetchone()
        if row is None:
            sys.exit(f"No administrator named '{username}'.")
        password = getpass.getpass("New password: ")
        if len(password) < 8:
            sys.exit("Password must be at least 8 characters.")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords did not match.")
        with database.transaction(conn):
            conn.execute(
                "UPDATE administrators SET password_hash = ?, must_change_password = 1, is_active = 1, "
                "session_epoch = session_epoch + 1, failed_attempts = 0, locked_until = NULL, "
                "updated_at = ? WHERE id = ?",
                (generate_password_hash(password), timeutil.to_iso(timeutil.now_utc()), row["id"]),
            )
            database.audit(conn, "user.reset_password_cli", "administrator", row["id"],
                           actor={"id": None, "username": "command-line"})
        print(f"Password updated for {username}. They must choose a new one at next login.")
    finally:
        conn.close()


def cmd_seed_demo(cfg):
    from backend.app import create_app
    from backend.employees import create_employee
    from backend.settings import get_settings

    app = create_app()
    with app.app_context():
        conn = database.get_db()
        if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]:
            sys.exit("Employees already exist, so demo data was not added.")
        settings = dict(get_settings(conn))
        _ensure_demo_shift(conn)
        for number, (name, department, designation, code, joined) in enumerate(DEMO_EMPLOYEES, 1):
            shift = conn.execute("SELECT id FROM shifts WHERE code = ?", (code,)).fetchone()
            email = name.lower().replace(" ", ".") + "@example.com"
            data = {"name": name, "department": department, "designation": designation,
                    "contact_number": f"+1 555 010 {number:04d}", "email": email,
                    "date_of_joining": joined, "shift_id": shift["id"] if shift else None,
                    "notes": "Demo employee"}
            print("Created", create_employee(conn, data, settings, "demo-data"), name, f"[{code}]")


def _ensure_demo_shift(conn):
    """Open all day, every day, so clock-in can be tried at any moment."""
    stamp = timeutil.to_iso(timeutil.now_utc())
    with database.transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO shifts(name, code, start_time, end_time, working_days, "
            "is_flexible, created_at, updated_at) VALUES (?, ?, '00:00', '23:59', ?, 1, ?, ?)",
            ("Demo All-Day Shift", DEMO_SHIFT_CODE, "[0,1,2,3,4,5,6]", stamp, stamp),
        )


def main():
    args = sys.argv[1:]
    cfg = load_config()
    if args == ["init"]:
        cmd_init(cfg)
    elif len(args) == 2 and args[0] == "reset-password":
        cmd_reset_password(cfg, args[1])
    elif args == ["seed-demo"]:
        cmd_seed_demo(cfg)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
