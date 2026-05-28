#!/usr/bin/env python3
import re
import os

CONFIG_PATH = os.path.expanduser("~/.config/surf/config")

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

# Matches: nasa.gov, nasa.gov/path, www.nasa.gov, http://nasa.gov
_URL_PATTERN = re.compile(
    r'^(https?://|www\.)'         # explicit scheme or www
    r'|^[a-zA-Z0-9-]+\.[a-zA-Z]{2,6}(/\S*)?$'  # bare domain like nasa.gov
)

def detect_input_type(text: str) -> str:
    """Return 'url' if text looks like a URL, 'query' otherwise."""
    text = text.strip()
    if _URL_PATTERN.match(text):
        return "url"
    return "query"
