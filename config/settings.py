"""Settings loader — reads config/settings.yaml and environment variables."""

import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent


def load_settings() -> dict:
    """Load settings from config/settings.yaml."""
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    # Resolve database path relative to project root
    settings["database"]["path"] = str(PROJECT_ROOT / settings["database"]["path"])
    return settings


def get_monday_token() -> str:
    """Get Monday API token from environment."""
    token = os.environ.get("MONDAY_API_TOKEN")
    if not token:
        raise RuntimeError(
            "MONDAY_API_TOKEN environment variable not set. "
            "Get your token from monday.com → Avatar → Developers → My Access Tokens"
        )
    return token
