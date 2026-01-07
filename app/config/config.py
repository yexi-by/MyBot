import tomllib
from pathlib import Path
from typing import Any

# 配置文件名
CONFIG_FILENAME = "setting.toml"


def load_config() -> dict[str, Any]:
    # 使用项目根目录（app/config 文件夹的父目录的父目录）
    project_root = Path(__file__).resolve().parents[2]
    path = project_root / CONFIG_FILENAME
    with open(path, "rb") as f:
        toml_data = tomllib.load(f)
        return toml_data


RAW_CONFIG_DICT = load_config()
