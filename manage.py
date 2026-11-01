"""Maintenance commands.

  python manage.py init                       Create the database
  python manage.py reset-password <username>  Set a new password from the terminal
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

from backend import database, timeutil
from backend.config import load_config

def cmd_init(cfg):
    from backend.app import create_app
    from backend.starter import load_starter_data

    load_starter_data(create_app())
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


def main():
    args = sys.argv[1:]
    cfg = load_config()
    if args == ["init"]:
        cmd_init(cfg)
    elif len(args) == 2 and args[0] == "reset-password":
        cmd_reset_password(cfg, args[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
