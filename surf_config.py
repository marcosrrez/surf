"""Shared configuration for surf modules. Breaks circular imports."""
import os

CONFIG_PATH = os.path.expanduser("~/.config/surf/config")
SESSION_FILE = os.path.expanduser("~/.config/surf/session.json")
SESSION_TTL = 4 * 60 * 60  # 4 hours
THREAD_DIR = os.path.expanduser("~/.config/surf/threads")
SNAPSHOT_DIR = os.path.expanduser("~/.config/surf/snapshots")


def load_config() -> dict:
    """Load key=value pairs from ~/.config/surf/config"""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
    return config
