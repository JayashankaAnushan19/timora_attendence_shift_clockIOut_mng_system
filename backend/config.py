"""Deployment configuration: where things live and how the server binds.

Business settings (timezone, grace periods, ...) live in the system_settings
table so they travel with database backups. This file only covers what must be
known before the database can be opened.
"""
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5000,
    "database": "database/timora.db",
    "backup_dir": "backups",
    "max_upload_mb": 200,
}

ENV_OVERRIDES = {
    "TIMORA_HOST": "host",
    "TIMORA_PORT": "port",
    "TIMORA_DATABASE": "database",
    "TIMORA_BACKUP_DIR": "backup_dir",
}


def _resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def load_config(overrides=None):
    cfg = dict(DEFAULTS)
    settings_file = ROOT / "config" / "settings.json"
    if settings_file.exists():
        cfg.update(json.loads(settings_file.read_text(encoding="utf-8")))
    for env, key in ENV_OVERRIDES.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    cfg.update(overrides or {})
    cfg["port"] = int(cfg["port"])
    cfg["database"] = _resolve(cfg["database"])
    cfg["backup_dir"] = _resolve(cfg["backup_dir"])
    cfg["database"].parent.mkdir(parents=True, exist_ok=True)
    cfg["backup_dir"].mkdir(parents=True, exist_ok=True)
    return cfg


def load_secret_key(database_path):
    """Session-signing key, generated once and kept beside the database."""
    key_file = Path(database_path).parent / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key
