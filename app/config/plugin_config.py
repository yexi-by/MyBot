"""插件配置读取工具。"""

import tomllib
from pathlib import Path
from typing import Final, cast

from pydantic import BaseModel

CONFIG_PATH: Final[Path] = Path("plugins_config/plugins.toml")


def load_plugin_config[T: BaseModel](
    *, section_name: str, model_cls: type[T], config_path: Path = CONFIG_PATH
) -> T:
    """从插件配置文件读取指定配置节并校验为 Pydantic 模型。"""
    if not config_path.exists():
        raise FileNotFoundError(f"插件配置文件不存在: {config_path}")
    with config_path.open("rb") as file:
        # tomllib 返回外部配置树，入口处只能先以 object 表达，下一行立刻收窄配置节类型。
        raw_config = cast(dict[str, object], tomllib.load(file))
    raw_section = raw_config.get(section_name)
    if not isinstance(raw_section, dict):
        raise ValueError(f"插件配置缺少 [{section_name}] 节")
    return model_cls.model_validate(raw_section)
