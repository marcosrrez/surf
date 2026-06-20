"""Shared configuration for surf modules. Breaks circular imports."""
import os

CONFIG_PATH = os.path.expanduser("~/.config/surf/config")
SESSION_FILE = os.path.expanduser("~/.config/surf/session.json")
SESSION_TTL = 4 * 60 * 60  # 4 hours
THREAD_DIR = os.path.expanduser("~/.config/surf/threads")
SNAPSHOT_DIR = os.path.expanduser("~/.config/surf/snapshots")

_API_KEY_NAMES = [
    "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
    "CEREBRAS_API_KEY", "BRAVE_API_KEY", "TAVILY_API_KEY",
]

try:
    import keyring as _keyring
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

_KEYRING_SERVICE = "surf-terminal"


def _get_key_from_keyring(key_name: str) -> str:
    """Try to read an API key from the system keyring. Returns '' if not found."""
    if not _HAS_KEYRING:
        return ""
    try:
        val = _keyring.get_password(_KEYRING_SERVICE, key_name)
        return val or ""
    except Exception:
        return ""


def save_key_to_keyring(key_name: str, value: str) -> bool:
    """Save an API key to the system keyring. Returns True on success."""
    if not _HAS_KEYRING:
        return False
    try:
        _keyring.set_password(_KEYRING_SERVICE, key_name, value)
        return True
    except Exception:
        return False


def load_config() -> dict:
    """Load key=value pairs from ~/.config/surf/config, with keyring overlay for API keys."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
    if _HAS_KEYRING:
        for key_name in _API_KEY_NAMES:
            if key_name not in config or not config[key_name]:
                val = _get_key_from_keyring(key_name)
                if val:
                    config[key_name] = val
    return config
