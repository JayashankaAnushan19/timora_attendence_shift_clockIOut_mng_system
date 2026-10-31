"""Start Timora. Usage: python run.py [--host 0.0.0.0] [--port 5000] [--open]"""
import argparse
import json
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERROR_LOG = ROOT / "logs" / "timora-error.log"


class StartupProblem(Exception):
    """A problem the person starting Timora can fix: what happened and how to fix it."""

    def __init__(self, problem, *steps):
        super().__init__(problem)
        self.problem = problem
        self.steps = steps


def show_problem(problem, steps):
    line = "=" * 66
    print(f"\n{line}\n  TIMORA COULD NOT START\n{line}\n")
    print(f"  Problem:\n    {problem}\n")
    print("  How to fix it:")
    for number, step in enumerate(steps, 1):
        print(f"    {number}. {step}")
    print(f"\n{line}\n")


def load_settings():
    from backend.config import load_config
    try:
        return load_config()
    except json.JSONDecodeError as exc:
        raise StartupProblem(
            f"The file config\\settings.json has a mistake on line {exc.lineno}.",
            "Open config\\settings.json in Notepad and correct that line.",
            "If unsure, restore the original content shown in README.md.",
        )
    except (ValueError, TypeError):
        raise StartupProblem(
            'A value in config\\settings.json is not valid. "port" must be a number.',
            "Open config\\settings.json in Notepad and correct the value.",
        )
    except PermissionError as exc:
        raise StartupProblem(
            f"Timora is not allowed to write to: {exc.filename}",
            "Move the Timora folder somewhere you can write to, such as your Desktop or Documents.",
            "Or right-click the folder, choose Properties > Security, and give your account Full control.",
        )


def is_timora(url):
    try:
        with urllib.request.urlopen(f"{url}/api/public/time", timeout=2) as response:
            return "utc" in json.loads(response.read().decode("utf-8"))
    except Exception:
        return False


def port_is_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def open_when_ready(url, host_port):
    def wait_and_open():
        for _ in range(100):
            try:
                with socket.create_connection(host_port, timeout=0.5):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.2)
    threading.Thread(target=wait_and_open, daemon=True).start()


def create_application(cfg):
    from backend.app import create_app
    try:
        return create_app()
    except PermissionError as exc:
        raise StartupProblem(
            f"Timora is not allowed to write to: {exc.filename or cfg['database']}",
            "Move the Timora folder somewhere you can write to, such as your Desktop or Documents.",
            "Or give your account Full control of the folder under Properties > Security.",
        )
    except sqlite3.OperationalError as exc:
        raise StartupProblem(
            f"The database could not be opened ({exc}).",
            "Close any other program using database\\timora.db, such as a database viewer.",
            "Check that the disk is not full.",
            "Then run Timora again.",
        )
    except sqlite3.DatabaseError:
        raise StartupProblem(
            "The database file database\\timora.db is damaged.",
            "Close Timora and rename database\\timora.db to timora-damaged.db.",
            "Copy your most recent file from the backups folder to database\\timora.db.",
            "Run Timora again. Without a backup, a new empty database is created.",
        )


def serve(app, args, url):
    print(f"\n  Timora is running.\n")
    print(f"  Attendance screen : {url}/")
    print(f"  Admin panel       : {url}/admin\n")
    print("  Keep this window open while using Timora.")
    print("  To stop Timora, close this window or press Ctrl+C.\n")
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)
    else:
        waitress_serve(app, host=args.host, port=args.port, threads=8, ident="Timora")


def main():
    cfg = load_settings()
    parser = argparse.ArgumentParser(description="Start the Timora server.")
    parser.add_argument("--host", default=cfg["host"], help="Use 0.0.0.0 to allow other computers on the network.")
    parser.add_argument("--port", type=int, default=cfg["port"])
    parser.add_argument("--open", action="store_true", help="Open the browser once the server is ready.")
    args = parser.parse_args()

    local_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{local_host}:{args.port}"

    if not port_is_free(args.host, args.port):
        if is_timora(url):
            print(f"\n  Timora is already running at {url}/\n")
            if args.open:
                webbrowser.open(url)
            return
        raise StartupProblem(
            f"Another program is already using port {args.port}, which Timora needs.",
            "Close the other program and run Timora again.",
            f'Or open config\\settings.json in Notepad, change "port": {args.port} to another '
            'number such as 5050, and run Timora again.',
        )

    app = create_application(cfg)
    if args.open:
        open_when_ready(url, (local_host, args.port))
    serve(app, args, url)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Timora stopped.\n")
    except StartupProblem as problem:
        show_problem(problem.problem, problem.steps)
        sys.exit(2)
    except ImportError as exc:
        show_problem(f"A required component is missing ({exc.name}).", [
            "Delete the .venv folder inside the Timora folder.",
            "Double-click start-timora.bat again. It reinstalls everything.",
        ])
        sys.exit(2)
    except Exception:
        ERROR_LOG.parent.mkdir(exist_ok=True)
        ERROR_LOG.write_text(traceback.format_exc(), encoding="utf-8")
        show_problem("Something unexpected went wrong while starting.", [
            "Restart the computer and run Timora again.",
            f"If it still fails, send this file to your support contact: {ERROR_LOG}",
        ])
        sys.exit(2)
