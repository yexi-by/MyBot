"""Neavo 群聊生图插件配置。"""

from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from app.config.plugin_config import load_plugin_config
from app.models import NapCatId, StrictModel

CONFIG_SECTION: Final[str] = "neavo_image_generate"

type PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
type PollIntervalSeconds = Annotated[
    float,
    Field(ge=2.0, le=5.0, allow_inf_nan=False),
]
type PositiveByteCount = Annotated[int, Field(gt=0)]


class NeavoImageGenerateConfig(StrictModel):
    """Neavo 群聊生图插件的完整配置。"""

    group_ids: Annotated[list[NapCatId], Field(min_length=1)]
    base_url: str
    api_token: SecretStr
    poll_interval_seconds: PollIntervalSeconds
    generation_timeout_seconds: PositiveSeconds
    request_timeout_seconds: PositiveSeconds
    max_image_bytes: PositiveByteCount

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """校验并规范化 Neavo 服务根地址。"""
        base_url = value.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url 必须是有效的 HTTP 或 HTTPS 地址")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url 不允许包含用户信息")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url 不允许包含查询参数或片段")
        return base_url

    @field_validator("api_token")
    @classmethod
    def validate_api_token(cls, value: SecretStr) -> SecretStr:
        """拒绝空 Token，且不在错误信息中回显其内容。"""
        token = value.get_secret_value().strip()
        if token == "":
            raise ValueError("api_token 不能为空")
        return SecretStr(token)


def load_neavo_image_generate_config() -> NeavoImageGenerateConfig:
    """读取并校验 Neavo 群聊生图插件配置。"""
    return load_plugin_config(
        section_name=CONFIG_SECTION,
        model_cls=NeavoImageGenerateConfig,
    )


__all__ = [
    "CONFIG_SECTION",
    "NeavoImageGenerateConfig",
    "load_neavo_image_generate_config",
]
