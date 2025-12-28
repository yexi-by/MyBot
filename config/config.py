from pathlib import Path
from typing import Any
import tomllib


def load_config() -> dict[str, Any]:
    current_path = Path(__file__).resolve().parents[0]
    path = current_path / "setting.toml"
    with open(path, "rb") as f:
        toml_data = tomllib.load(f)
        return toml_data

RAW_CONFIG_DICT  = load_config()
