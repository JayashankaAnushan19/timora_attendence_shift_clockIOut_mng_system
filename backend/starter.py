"""Employees added once to a brand-new installation, from config/starter_data.json."""
import json

from .config import ROOT
from .database import get_db, transaction
from .employees import create_employee
from .settings import get_settings, set_setting

STARTER_FILE = ROOT / "config" / "starter_data.json"


def load_starter_data(app, starter_file=STARTER_FILE):
    """Runs one time only, so removed or restored data is never added back."""
    created = []
    with app.app_context():
        conn = get_db()
        settings = dict(get_settings(conn))
        if settings.get("starter_data_loaded"):
            return created
        has_employees = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        if not has_employees and starter_file.exists():
            for person in json.loads(starter_file.read_text(encoding="utf-8")).get("employees", []):
                shift = conn.execute("SELECT id FROM shifts WHERE code = ?", (person.get("shift_code"),)).fetchone()
                created.append(create_employee(
                    conn, {**person, "shift_id": shift["id"] if shift else None}, settings, "starter-data"))
        with transaction(conn):
            set_setting(conn, "starter_data_loaded", True)
    return created
