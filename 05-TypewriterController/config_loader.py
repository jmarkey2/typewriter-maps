from __future__ import annotations

import json
from typing import Any, Dict, Tuple


DEFAULT_CONFIG: Dict[str, Any] = {
    "channels": {
        "SPACEBAR": 9,
        "BLUE": 8,
        "CORRECTION": 11,
        "RETURN": 10,
    },
    "angles": {
        "SPACE_REST_ANGLE": 0,
        "SPACE_PRESS_ANGLE": 120,
        "BLUE_REST_ANGLE": 90,
        "BLUE_PRESS_ANGLE": 75,
        "CORR_REST_ANGLE": 180,
        "CORR_HOLD_ANGLE": 60,
        "RETURN_REST_ANGLE": 90,
        "RETURN_PRESS_ANGLE": 60,
    },
    "timing": {
        "PRESS_TIME": 0.3,
        "BETWEEN_KEYS": 0.2,
        "BETWEEN_CHARS": 0.2,
        "NEW_LINE_DELAY": 1.5,
        "CORR_ENGAGE_DELAY": 2.0,
        "SPACE_TOGGLE_DELAY": 1.0,
        "SPACE_REST_MOVE_DELAY": 1.5,
        "RETURN_PRESS_HOLD": 1.0,
        "CORR_RELEASE_MOVE_DELAY": 0.3,
        "CORR_RELEASE_PAUSE": 0.3,
        "SERVO_REST_MOVE_DELAY": 0.2,
        "POST_BLUE_JITTER_DELAY": 0.06,
    },
    "mode": {
        "monochrome_enabled": False,
        "monochrome_color": "BLUE",
    },
}


def load_config(path: str) -> Tuple[Dict[str, Any], str]:
    """
    Loads JSON content from a .txt (or any) file.
    Returns: (cfg, status_message)
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Light validation and merging with defaults
        merged = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy via JSON roundtrip
        for section in ("channels", "angles", "timing", "mode"):
            if isinstance(cfg.get(section), dict):
                merged[section].update(cfg[section])
        return merged, f"Loaded config: {path}"
    except FileNotFoundError:
        return DEFAULT_CONFIG, f"Config file not found ({path}). Using defaults."
    except Exception as e:
        return DEFAULT_CONFIG, f"Could not load config ({path}). Using defaults. Error: {e}"
