import json
import logging
import os

logger = logging.getLogger(__name__)

_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "language_settings.json"
)

SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"

# user_id (str) -> language code
_state: dict = {}


def _load_settings() -> None:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _state.update({str(k): v for k, v in data.items() if v in SUPPORTED_LANGUAGES})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass  # no saved settings yet — keep defaults


def _save_settings() -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"Failed to save language settings: {e}")


_load_settings()


def get_language(user_id: int) -> str:
    return _state.get(str(user_id), DEFAULT_LANGUAGE)


def set_language(user_id: int, lang: str) -> None:
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {lang}")
    _state[str(user_id)] = lang
    _save_settings()