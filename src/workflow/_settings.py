"""Execution settings shared by the UI and workers, without Streamlit state."""
import json
import os
from pathlib import Path


def load_settings():
    path = Path("settings.json")
    return json.loads(path.read_text()) if path.exists() else {}


def online_mode(settings):
    return bool(os.environ.get("REDIS_URL")) or bool(settings.get("online_deployment", False))
