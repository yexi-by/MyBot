"""项目配置加载入口。"""

import tomllib
from pathlib import Path
from typing import cast

from .schemas import Settings

CONFIG_FILENAME = "setting.toml"


def load_config() -> dict[str, object]:
    """读取项目根目录下的 TOML 配置文件。"""
    project_root = Path(__file__).resolve().parents[2]
    path = project_root / CONFIG_FILENAME
    with path.open("rb") as file:
        toml_data = tomllib.load(file)
    return cast(dict[str, object], toml_data)


def load_settings() -> Settings:
    """读取并校验完整应用配置。"""
    return Settings.model_validate(load_config())


RAW_CONFIG_DICT: dict[str, object] = load_config()
